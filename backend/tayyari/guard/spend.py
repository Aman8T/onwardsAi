"""The daily spend ceiling: a soft stop that switches AI off until the next UTC midnight.

Spend is read from the ``spend_daily`` ledger (`LimitRepo.spent_today`, micro-USD, scope ``total``), which
every recorded Claude and Jev call adds to. The guard reads one row, cached for a few seconds, and offers three
checks with deliberately different strictness:

- `admit(est_usd)`: before an analysis or chat turn starts. Raises `SpendCeilingReached` when today's spend
  plus the estimate reaches the ceiling. **Fails closed**: if the ledger cannot be read the request is
  refused (a server-side log says why), because without the database nothing can be saved anyway.
- `admit_call(purpose)`: before each individual model call. A run that has already paid for its readers
  may finish while spend is under ``ceiling x ceiling_grace``; beyond that the call is refused. **Fails
  open** when the ledger cannot be read, so a ledger hiccup never kills a run half way.
- `tripped()`: a synchronous yes/no for code that cannot await (the decision runner answers with its
  neutral value while tripped). It only looks at the cached reading and, when that has gone stale, starts a
  background refresh, so it may lag by one TTL plus one query. Unknown counts as "not tripped".

The ceiling is soft by design: replicas and in-flight runs overshoot it by a few dollars; the Anthropic
workspace spend limit is the hard backstop. A ceiling of 0 switches AI off entirely; to disable the guard
use ``GuardSettings.enabled = False``. `SpendCeilingReached.retry_after_s` is the number of seconds to the
next UTC midnight (the ledger's day boundary).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from tayyari.core.clock import Clock
from tayyari.core.logging import get_logger
from tayyari.core.settings import GuardSettings
from tayyari.llm.errors import SpendCeilingReached
from tayyari.storage.ports import LimitRepo

log = get_logger(__name__)

MICRO = 1_000_000
LEDGER_UNAVAILABLE_RETRY_S = 30.0
"""`retry_after_s` when `admit` refuses because the ledger could not be read (a guess, not a deadline)."""


@dataclass(frozen=True, slots=True)
class _Reading:
    day: date
    micro_usd: int
    at: float
    """`Clock.monotonic()` when it was read."""


class _LedgerUnavailable(Exception):
    """The ledger could not be read (error, timeout, or a recent failure still in back-off)."""


def seconds_to_utc_midnight(now: datetime) -> float:
    """Seconds from ``now`` to the next 00:00 UTC, at least 1."""
    utc_now = now.astimezone(UTC)
    midnight = datetime.combine(utc_now.date() + timedelta(days=1), time.min, tzinfo=UTC)
    return max(1.0, (midnight - utc_now).total_seconds())


class SpendGuard:
    """See the module docstring. One instance per process; `GuardedClaudeClient` and the routes share it."""

    def __init__(
        self,
        repo: LimitRepo,
        settings: GuardSettings,
        clock: Clock,
        *,
        cache_ttl_s: float = 5.0,
        db_timeout_s: float = 3.0,
        failure_backoff_s: float = 2.0,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._clock = clock
        self._ttl = cache_ttl_s
        self._db_timeout_s = db_timeout_s
        self._backoff = failure_backoff_s
        self._cache: _Reading | None = None
        self._failed_until: float | None = None
        self._lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None

    # ───────────────────────── configuration ─────────────────────────

    @property
    def ceiling_micro(self) -> int:
        """The daily ceiling in micro-USD."""
        return round(self._settings.daily_ceiling_usd * MICRO)

    @property
    def hard_ceiling_micro(self) -> int:
        """The level at which `admit_call` refuses: ceiling x grace."""
        return round(self.ceiling_micro * self._settings.ceiling_grace)

    def _today(self) -> date:
        return self._clock.now().astimezone(UTC).date()

    def _fresh(self, reading: _Reading | None) -> bool:
        return (
            reading is not None
            and reading.day == self._today()
            and self._clock.monotonic() - reading.at < self._ttl
        )

    # ───────────────────────── ledger ─────────────────────────

    async def _spent(self, *, force: bool = False) -> int:
        """Micro-USD spent today: the cached reading while it is fresh, otherwise one ledger read.

        Concurrent callers share one read. Raises `_LedgerUnavailable` when the read fails or a recent
        failure is still being backed off from (``force`` skips the cache and the back-off).
        """
        cached = self._cache
        if not force and cached is not None and self._fresh(cached):
            return cached.micro_usd
        async with self._lock:
            cached = self._cache
            if not force and cached is not None and self._fresh(cached):
                return cached.micro_usd
            now = self._clock.monotonic()
            if self._failed_until is not None:
                if now < self._failed_until and not force:
                    raise _LedgerUnavailable
                self._failed_until = None
            day = self._today()
            try:
                async with asyncio.timeout(self._db_timeout_s):
                    micro = await self._repo.spent_today(day)
            except Exception as exc:
                self._failed_until = self._clock.monotonic() + self._backoff
                log.error("spend_ledger_unavailable", error=type(exc).__name__)
                raise _LedgerUnavailable from exc
            self._cache = _Reading(day=day, micro_usd=micro, at=self._clock.monotonic())
            return micro

    async def refresh(self) -> None:
        """Re-read the ledger now, ignoring the cache. Never raises (an unreadable ledger is logged)."""
        try:
            await self._spent(force=True)
        except _LedgerUnavailable:
            return

    # ───────────────────────── checks ─────────────────────────

    async def admit(self, est_usd: float) -> None:
        """Before an analysis or chat turn: refuse when spent today plus ``est_usd`` reaches the ceiling.

        Fails closed: a ledger that cannot be read also refuses (`SpendCeilingReached`, logged server-side).
        """
        if not self._settings.enabled:
            return
        try:
            spent = await self._spent()
        except _LedgerUnavailable:
            log.error("spend_guard_failing_closed", reason="ledger unavailable")
            raise SpendCeilingReached(LEDGER_UNAVAILABLE_RETRY_S, detail="spend ledger unavailable") from None
        estimate = round(max(est_usd, 0.0) * MICRO)
        if spent + estimate >= self.ceiling_micro:
            log.info(
                "spend_ceiling_reached",
                stage="admit",
                spent_micro_usd=spent,
                estimate_micro_usd=estimate,
                ceiling_micro_usd=self.ceiling_micro,
            )
            raise SpendCeilingReached(
                seconds_to_utc_midnight(self._clock.now()),
                detail=f"spent={spent} estimate={estimate} ceiling={self.ceiling_micro}",
            )

    async def admit_call(self, purpose: str) -> None:
        """Before each model call: refuse once spent today reaches ``ceiling x ceiling_grace``.

        Fails open: a ledger that cannot be read lets the call through.
        """
        if not self._settings.enabled:
            return
        try:
            spent = await self._spent()
        except _LedgerUnavailable:
            log.warning("spend_guard_failing_open", purpose=purpose)
            return
        if spent >= self.hard_ceiling_micro:
            log.info(
                "spend_ceiling_reached",
                stage="call",
                purpose=purpose,
                spent_micro_usd=spent,
                hard_ceiling_micro_usd=self.hard_ceiling_micro,
            )
            raise SpendCeilingReached(
                seconds_to_utc_midnight(self._clock.now()),
                detail=f"purpose={purpose} spent={spent} hard_ceiling={self.hard_ceiling_micro}",
            )

    def tripped(self) -> bool:
        """True when the cached ledger reading says today's spend has reached the ceiling.

        Synchronous: it never waits for the database. A stale or missing reading starts a background refresh
        (when an event loop is running) and an unknown ledger counts as not tripped.
        """
        if not self._settings.enabled:
            return False
        if self.ceiling_micro <= 0:
            return True
        cached = self._cache
        if not self._fresh(cached):
            self._schedule_refresh()
        if cached is None or cached.day != self._today():
            return False
        return cached.micro_usd >= self.ceiling_micro

    def _schedule_refresh(self) -> None:
        task = self._refresh_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # called outside an event loop (a sync test): nothing to refresh with
            return
        self._refresh_task = loop.create_task(self._refresh_quietly(), name="spend-guard-refresh")

    async def _refresh_quietly(self) -> None:
        try:
            await self._spent()
        except _LedgerUnavailable:
            return

    async def aclose(self) -> None:
        """Cancel a background refresh that is still running (call from the lifespan shutdown)."""
        task = self._refresh_task
        self._refresh_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task])


__all__ = ["LEDGER_UNAVAILABLE_RETRY_S", "SpendGuard", "seconds_to_utc_midnight"]
