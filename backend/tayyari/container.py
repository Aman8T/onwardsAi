"""The composition root: builds every client, repository and service once, and closes them on shutdown.

`Container.build(settings)` is the only place that knows which implementation backs which Protocol::

    Database -> migrations -> Repositories -> ip_hash_key
    SpendGuard, RateLimiter                       (cost and abuse protection, shared by routes and clients)
    AnthropicClaudeClient -> GuardedClaudeClient  (every Claude call checks the spend ceiling first)
    HttpJevClient, DecisionRecorder, DecisionRunner -> JevDecisionService (or NullDecisionService)
    SampleProvider                                (the bundled sample persona, also seeded into the database)
    PipelineAnalysisService, CoachChatService
    maintenance                                   (retention purge, a tracked background task; it keeps
                                                   the bundled sample persona's profile)

The FastAPI lifespan builds one container per process and `aclose`s it on shutdown. The CLI builds its own
from the same settings. Tests hand in `ContainerOverrides` to swap any part for a fake; the container closes
only what it built itself, so an object passed in stays open for whoever created it.

Policy: when Jev is switched off (``TAYYARI_JEV__ENABLED=false``) or no ``JEV_API_KEY`` is set, decisions are a
`NullDecisionService` and Jev is skipped entirely (behaviour like the legacy app). Claude fallbacks only cover
individual low-confidence Jev answers; they never stand in for Jev as a whole.

The three service classes (analysis, chat, decisions) are imported inside `build`, not at module level, so
importing this module (the CLI help, the layering test) stays cheap and cannot fail on a service that is
mid-rewrite.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field

from tayyari.analysis.ports import AnalysisService
from tayyari.chat.ports import ChatService
from tayyari.contract.models import StorageKind
from tayyari.core.clock import Clock, SystemClock
from tayyari.core.logging import configure_logging, get_logger
from tayyari.core.settings import Settings
from tayyari.decisions.config import DecisionConfigStore
from tayyari.decisions.recorder import DecisionRecorder
from tayyari.decisions.runner import DecisionRunner
from tayyari.decisions.service import DecisionService, NullDecisionService
from tayyari.guard.ratelimit import RateLimiter
from tayyari.guard.spend import SpendGuard
from tayyari.llm.anthropic_client import AnthropicClaudeClient
from tayyari.llm.breaker import CircuitBreaker
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.guarded import GuardedClaudeClient
from tayyari.llm.jev import JevClient
from tayyari.llm.jev_http import HttpJevClient
from tayyari.sample.provider import SampleProvider
from tayyari.storage.db import Database
from tayyari.storage.maintenance import MIN_INTERVAL, run_maintenance
from tayyari.storage.migrate import migrate_to_head
from tayyari.storage.repos import Repositories, build_repositories

log = get_logger(__name__)

IP_HASH_KEY = "ip_hash_key"
"""The ``kv`` row that holds the HMAC key for client-IP hashes (generated on first start, shared by replicas)."""
MAINTENANCE_DELAY_S = 10.0
"""The retention purge starts this long after startup, so the first requests of a cold start come first."""
MAINTENANCE_INTERVAL_S = MIN_INTERVAL.total_seconds()
"""A long-lived process tries again this often; `run_maintenance` itself runs at most once per interval."""
DRAIN_TIMEOUT_S = 5.0
"""Shutdown waits this long for background decision work (shadow and audit calls) before cancelling it."""

_Closer = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ContainerOverrides:
    """Parts a test (or a tool) swaps for its own. Anything left as None is built from the settings.

    - ``claude``: the *inner* Claude client; the container still wraps it in the spend-checking
      `GuardedClaudeClient`, so the guard stays in play with a fake.
    - ``jev``: a Jev client. Giving one switches decisions on even without a ``JEV_API_KEY`` (the
      ``TAYYARI_JEV__ENABLED`` kill switch still wins).
    - ``decisions``, ``analysis``, ``chat``: the services themselves.
    - ``db``: a ready `Database` (migrated here, idempotent). ``repos``: ready repositories (fakes); without a
      ``db`` the container has no database, so migrations, maintenance and the deep health check are skipped.
    - ``clock``: used by every component.
    """

    claude: ClaudeClient | None = None
    jev: JevClient | None = None
    decisions: DecisionService | None = None
    repos: Repositories | None = None
    db: Database | None = None
    clock: Clock | None = None
    analysis: AnalysisService | None = None
    chat: ChatService | None = None


def _log_unknown_decision_ids(store: DecisionConfigStore) -> None:
    """Warn about ``defaults.toml`` entries and overrides that name a spec no pack defines (typos).

    Diagnostic only: it never stops the app from starting.
    """
    from tayyari.decisions.packs.service_impl import build_registry

    try:
        unknown = build_registry().unknown_config_ids(store)
    except Exception:
        log.warning("decision_registry_check_failed", exc_info=True)
        return
    if unknown:
        log.warning("decision_config_unknown_ids", ids=unknown, hint="check defaults.toml and overrides")


async def _seed_sample(repos: Repositories, sample: SampleProvider) -> None:
    """Store the bundled sample profile and its chat context once, so "Try a sample profile" can be chatted about."""
    profile = sample.sample_profile
    if profile is None:
        log.info("sample_seed_skipped", reason="no sample fixtures")
        return
    try:
        if await repos.profiles.get_profile(profile.id) is not None:
            return
        await repos.profiles.save(
            profile.id,
            profile.to_dict(),
            sample.sample_context,
            goal=profile.goal,
            owner_hash=None,
            is_sample=True,
        )
        log.info("sample_seeded", profile_id=profile.id)
    except Exception:
        # The sample is also served from its file, so a failed seed only costs chat about it.
        log.error("sample_seed_failed", profile_id=profile.id, exc_info=True)


@dataclass(repr=False, eq=False)
class Container:
    """Everything the API and the CLI need, built once. See the module docstring."""

    settings: Settings
    clock: Clock
    db: Database | None
    """None only when a test handed in fake repositories without a database."""
    repos: Repositories
    claude: ClaudeClient
    """The spend-checking client every service uses."""
    jev: JevClient
    recorder: DecisionRecorder
    runner: DecisionRunner
    decisions: DecisionService
    sample: SampleProvider
    analysis: AnalysisService
    chat: ChatService
    rate_limiter: RateLimiter
    spend_guard: SpendGuard
    ip_hash_key: bytes
    analysis_slots: asyncio.Semaphore
    """How many analyses this process runs at once (``settings.concurrency.max_concurrent_analyses``)."""
    jev_active: bool
    """True when decisions go through Jev (it is enabled and has a key, or a client was injected)."""
    maintenance: asyncio.Task[None] | None = None
    _closers: list[tuple[str, _Closer]] = field(default_factory=list, repr=False)
    _closed: bool = field(default=False, repr=False)

    def __repr__(self) -> str:
        return (
            f"Container(storage={self.storage_kind!r}, jev_active={self.jev_active}, "
            f"db={'yes' if self.db is not None else 'no'})"
        )

    @property
    def storage_kind(self) -> StorageKind | None:
        """``"postgres"`` or ``"ephemeral"`` (what /api/health reports); None for a durable local SQLite file."""
        config = self.db.config if self.db is not None else self.settings.db
        if config.ephemeral:
            return "ephemeral"
        return "postgres" if config.kind == "postgres" else None

    # ───────────────────────────── build ─────────────────────────────

    @classmethod
    async def build(cls, settings: Settings, *, overrides: ContainerOverrides | None = None) -> Container:
        """Build the whole object graph. Raises when the database cannot be reached or migrated (the app must
        not start half-wired); anything built before the failure is closed again."""
        # Imported here, not at module level: see the module docstring.
        from tayyari.analysis.service import PipelineAnalysisService
        from tayyari.chat.service import CoachChatService
        from tayyari.decisions.packs.service_impl import JevDecisionService

        ov = overrides or ContainerOverrides()
        configure_logging(settings)
        clock: Clock = ov.clock if ov.clock is not None else SystemClock()
        closers: list[tuple[str, _Closer]] = []
        maintenance: asyncio.Task[None] | None = None
        try:
            # ── storage ──
            db = ov.db
            if db is None and ov.repos is None:
                db = Database(settings.db)
                closers.append(("database", db.dispose))
            if db is not None:
                if db.ephemeral:
                    log.warning(
                        "ephemeral_storage",
                        database=db.safe_url,
                        effect="profiles and chats are lost on restart and differ between replicas",
                        fix="connect a Postgres database (DATABASE_URL), e.g. the Neon integration on FastAPI Cloud",
                    )
                revision = await migrate_to_head(db)
                log.info("storage_ready", kind=db.kind, revision=revision)
            repos = ov.repos if ov.repos is not None else build_repositories(_require(db), clock)
            ip_hash_key = await repos.kv.get_or_create_secret(IP_HASH_KEY)

            # ── guard and model clients ──
            spend_guard = SpendGuard(repos.limits, settings.guard, clock)
            closers.append(("spend_guard", spend_guard.aclose))
            rate_limiter = RateLimiter(repos.limits, settings.guard, clock)

            inner_claude: ClaudeClient
            if ov.claude is not None:
                inner_claude = ov.claude
            else:
                built_claude = AnthropicClaudeClient(
                    settings=settings, usage_sink=repos.llm_calls, clock=clock
                )
                closers.append(("claude", built_claude.aclose))
                inner_claude = built_claude
            claude = GuardedClaudeClient(inner_claude, spend_guard.admit_call)

            jev: JevClient
            if ov.jev is not None:
                jev = ov.jev
            else:
                built_jev = HttpJevClient(settings=settings.jev, api_key=settings.jev_api_key, clock=clock)
                closers.append(("jev", built_jev.aclose))
                jev = built_jev

            # ── decisions ──
            recorder = DecisionRecorder(repos.decisions, clock)
            closers.append(("decision_recorder", recorder.aclose))
            store = DecisionConfigStore.from_settings(settings)
            runner = DecisionRunner(
                jev,
                claude,
                recorder,
                store,
                CircuitBreaker(clock=clock),
                spend_guard.admit_call,
                clock,
                settings.reader_model,
            )
            closers.append(("decision_runner", lambda: runner.drain(DRAIN_TIMEOUT_S, cancel_remaining=True)))
            jev_active = settings.jev.enabled and (ov.jev is not None or settings.jev_api_key is not None)
            decisions: DecisionService
            if ov.decisions is not None:
                decisions = ov.decisions
            elif jev_active:
                decisions = JevDecisionService(runner=runner, settings=settings, clock=clock)
            else:
                decisions = NullDecisionService()
                log.info(
                    "jev_inactive",
                    reason="disabled by TAYYARI_JEV__ENABLED"
                    if not settings.jev.enabled
                    else "no JEV_API_KEY",
                )
            _log_unknown_decision_ids(store)

            # ── sample and services ──
            sample = SampleProvider()
            await _seed_sample(repos, sample)
            analysis: AnalysisService
            if ov.analysis is not None:
                analysis = ov.analysis
            else:
                analysis = PipelineAnalysisService(
                    settings=settings,
                    claude=claude,
                    decisions=decisions,
                    profiles=repos.profiles,
                    sample=sample,
                    clock=clock,
                )
            chat: ChatService
            if ov.chat is not None:
                chat = ov.chat
            else:
                chat = CoachChatService(
                    settings=settings,
                    claude=claude,
                    decisions=decisions,
                    profiles=repos.profiles,
                    sessions=repos.sessions,
                    sample=sample,
                    clock=clock,
                )

            # ── background work: never blocks startup ──
            if db is not None:
                # The bundled sample persona is the one sample profile the purge must never delete.
                sample_id = sample.sample_profile_id()
                keep = {sample_id} if sample_id else set()
                maintenance = asyncio.create_task(
                    _maintenance_loop(db, settings, clock, keep), name="tayyari-maintenance"
                )
        except BaseException:
            if maintenance is not None:
                maintenance.cancel()
            await _run_closers(closers)
            raise

        return cls(
            settings=settings,
            clock=clock,
            db=db,
            repos=repos,
            claude=claude,
            jev=jev,
            recorder=recorder,
            runner=runner,
            decisions=decisions,
            sample=sample,
            analysis=analysis,
            chat=chat,
            rate_limiter=rate_limiter,
            spend_guard=spend_guard,
            ip_hash_key=ip_hash_key,
            analysis_slots=asyncio.Semaphore(settings.concurrency.max_concurrent_analyses),
            jev_active=jev_active,
            maintenance=maintenance,
            _closers=closers,
        )

    # ───────────────────────────── shutdown ─────────────────────────────

    async def aclose(self) -> None:
        """Stop the background work, drain decision logging and close what this container built.

        Each step is guarded: one failure is logged and the rest still run. Safe to call twice.
        """
        if self._closed:
            return
        self._closed = True
        task, self.maintenance = self.maintenance, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await _run_closers(self._closers)


def _require(db: Database | None) -> Database:
    if db is None:  # pragma: no cover - build() only reaches this with a database
        raise RuntimeError("a database is required to build repositories")
    return db


async def _run_closers(closers: list[tuple[str, _Closer]]) -> None:
    """Run the closers newest first; a failing one is logged and does not stop the others."""
    for name, close in reversed(closers):
        try:
            await close()
        except Exception:
            log.error("shutdown_step_failed", step=name, exc_info=True)


async def _maintenance_loop(
    db: Database, settings: Settings, clock: Clock, keep_profile_ids: Collection[str] = ()
) -> None:
    """Retention purge shortly after startup and then every few hours. Never raises (except cancellation).

    ``keep_profile_ids`` are the sample profiles the purge leaves alone (the bundled persona).
    """
    await asyncio.sleep(MAINTENANCE_DELAY_S)
    while True:
        try:
            await run_maintenance(db, settings, clock.now(), keep_profile_ids=keep_profile_ids)
        except Exception:
            log.error("maintenance_failed", exc_info=True)
        # Real time on purpose: a test clock that "sleeps" instantly would turn this into a busy loop.
        await asyncio.sleep(MAINTENANCE_INTERVAL_S)


__all__ = ["DRAIN_TIMEOUT_S", "IP_HASH_KEY", "Container", "ContainerOverrides"]
