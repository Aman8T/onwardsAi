"""The daily spend ceiling: a soft stop that switches AI off until the next UTC midnight.

Spend is read from the ``spend_daily`` ledger (`LimitRepo.spent_today`, micro-USD, scope ``total``), which
every recorded Claude and Jev call adds to. The guard reads one row, cached for a few seconds, and offers these
checks with deliberately different strictness:

- `reserve(est_usd)`: an async context manager around an analysis or chat turn. It admits like `admit`, but
  the ledger only grows when a call *finishes*, so requests that start together would all see the same
  stale number. Every admitted reservation is therefore held (in this process, in micro-USD) until the
  ``async with`` block exits, however it exits, and counts against the ceiling for everyone else:
  ``ledger + reservations + estimate >= ceiling`` refuses. Use this for work that costs money.
- `admit(est_usd)`: the same check without holding anything (kept for compatibility). Raises
  `SpendCeilingReached` when today's spend, the reservations in flight and the estimate reach the ceiling.
  **Fails closed**: if the ledger cannot be read the request is refused (a server-side log says why),
  because without the database nothing can be saved anyway.
- `admit_call(purpose)`: before each individual model call. A run that has already paid for its readers
  may finish while ledger plus reservations are under ``ceiling x ceiling_grace``; beyond that the call is
  refused. **Fails open** when the ledger cannot be read, so a ledger hiccup never kills a run half way.
- `tripped()`: a synchronous yes/no for code that cannot await (the decision runner answers with its
  neutral value while tripped). It only looks at the cached reading and, when that has gone stale, starts a
  background refresh, so it may lag by one TTL plus one query. Unknown counts as "not tripped".

Reservations are per process: other replicas only show up through the ledger. They deliberately overlap
with the ledger while a run is in progress (a finished reader's cost is in the ledger and still inside the
run's reservation), which errs on the side of refusing. The ceiling is soft by design: replicas and the
ledger's few seconds of lag overshoot it by a few dollars; the Anthropic workspace spend limit is the hard
backstop. A ceiling of 0 switches AI off entirely; to disable the guard
use ``GuardSettings.enabled = False``. `SpendCeilingReached.retry_after_s` is the number of seconds to the
next UTC midnight (the ledger's day boundary).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
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
        self._reserved_micro = 0
        self._reservations = 0

    # ───────────────────────── configuration ─────────────────────────

    @property
    def ceiling_micro(self) -> int:
        """The daily ceiling in micro-USD."""
        return round(self._settings.daily_ceiling_usd * MICRO)

    @property
    def hard_ceiling_micro(self) -> int:
        """The level at which `admit_call` refuses: ceiling x grace."""
        return round(self.ceiling_micro * self._settings.ceiling_grace)

    @property
    def reserved_micro(self) -> int:
        """Micro-USD held by the reservations currently in flight in this process."""
        return self._reserved_micro

    @property
    def reservations_in_flight(self) -> int:
        """How many `reserve` blocks are open in this process right now."""
        return self._reservations

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

    @staticmethod
    def _micro(est_usd: float) -> int:
        """An estimate in whole micro-USD; negative (and NaN) counts as zero."""
        return round(est_usd * MICRO) if est_usd > 0 else 0

    async def _check(self, est_usd: float, *, stage: str) -> int:
        """Refuse when ledger + reservations in flight + the estimate reach the ceiling; else the estimate.

        Nothing is held here. Everything after the ledger read is synchronous, so a caller that records the
        estimate right after this returns (with no await in between) does so atomically.
        """
        try:
            spent = await self._spent()
        except _LedgerUnavailable:
            log.error("spend_guard_failing_closed", reason="ledger unavailable")
            raise SpendCeilingReached(LEDGER_UNAVAILABLE_RETRY_S, detail="spend ledger unavailable") from None
        estimate = self._micro(est_usd)
        reserved = self._reserved_micro
        if spent + reserved + estimate >= self.ceiling_micro:
            log.info(
                "spend_ceiling_reached",
                stage=stage,
                spent_micro_usd=spent,
                reserved_micro_usd=reserved,
                estimate_micro_usd=estimate,
                ceiling_micro_usd=self.ceiling_micro,
            )
            raise SpendCeilingReached(
                seconds_to_utc_midnight(self._clock.now()),
                detail=f"spent={spent} reserved={reserved} estimate={estimate} ceiling={self.ceiling_micro}",
            )
        return estimate

    async def admit(self, est_usd: float) -> None:
        """Before an analysis or chat turn: refuse when spent today, the reservations in flight and
        ``est_usd`` reach the ceiling. Holds nothing; use `reserve` for work that is about to start.

        Fails closed: a ledger that cannot be read also refuses (`SpendCeilingReached`, logged server-side).
        """
        if not self._settings.enabled:
            return
        await self._check(est_usd, stage="admit")

    def reserve(self, est_usd: float) -> AbstractAsyncContextManager[None]:
        """Admit like `admit`, then hold ``est_usd`` against the ceiling until the block exits::

            async with spend_guard.reserve(settings.guard.est_chat_usd):
                ...  # run the turn

        Raises `SpendCeilingReached` from ``__aenter__`` (nothing is held then). The hold is released when the
        block ends by any route: normal exit, an exception, or the task being cancelled. Fails closed when the
        ledger cannot be read, like `admit`. With the guard disabled it holds nothing.
        """
        return self._reserve(est_usd)

    @asynccontextmanager
    async def _reserve(self, est_usd: float) -> AsyncIterator[None]:
        if not self._settings.enabled:
            yield
            return
        estimate = await self._check(est_usd, stage="reserve")
        # No await between the check above and this hold, so two reservations cannot both pass on the same
        # free headroom.
        self._reserved_micro += estimate
        self._reservations += 1
        try:
            yield
        finally:
            self._reserved_micro = max(0, self._reserved_micro - estimate)
            self._reservations = max(0, self._reservations - 1)

    async def admit_call(self, purpose: str) -> None:
        """Before each model call: refuse once spent today plus the reservations in flight reach
        ``ceiling x ceiling_grace``.

        Fails open: a ledger that cannot be read lets the call through.
        """
        if not self._settings.enabled:
            return
        try:
            spent = await self._spent()
        except _LedgerUnavailable:
            log.warning("spend_guard_failing_open", purpose=purpose)
            return
        reserved = self._reserved_micro
        if spent + reserved >= self.hard_ceiling_micro:
            log.info(
                "spend_ceiling_reached",
                stage="call",
                purpose=purpose,
                spent_micro_usd=spent,
                reserved_micro_usd=reserved,
                hard_ceiling_micro_usd=self.hard_ceiling_micro,
            )
            raise SpendCeilingReached(
                seconds_to_utc_midnight(self._clock.now()),
                detail=(
                    f"purpose={purpose} spent={spent} reserved={reserved} "
                    f"hard_ceiling={self.hard_ceiling_micro}"
                ),
            )

    def tripped(self) -> bool:
        """True when the cached ledger reading says today's spend has reached the ceiling.

        Reservations are not counted: this means "AI is switched off", not "one more request would not fit".

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
