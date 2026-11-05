"""The composition root: builds every client, repository and service once, and closes them on shutdown.

`Container.build(settings)` is the only place that knows which implementation backs which Protocol::

    Database -> migrations -> Repositories -> ip_hash_key
    SpendGuard, RateLimiter                       (cost and abuse protection, shared by routes and clients)
    AnthropicClaudeClient -> GuardedClaudeClient  (every Claude call checks the spend ceiling first)
    HttpJevClient, DecisionRecorder, DecisionRunner -> JevDecisionService (or NullDecisionService)
    SampleProvider                                (the bundled sample persona, also seeded into the database)
    PipelineAnalysisService, CoachChatService, PortfolioService
    AuthService                                   (sign-in, sessions and emailed links; its own small HTTP client)
    EntitlementService, BillingService            (what an account may use, and how it pays: Razorpay for INR on
                                                   its key pair, Stripe for USD on its two secrets)
    MatchService                                  (the posting match: the guarded, recording Claude client, the
                                                   match models, and the stored postings and extracted facts)
    DiscoverService                               (job discovery over employers' own public boards; no model, its own
                                                   HTTP client; a refresh runs in the background when the data is old and
                                                   is cancelled at shutdown)
    JobSearchService                              (search with AI: Haiku scouts over employers' job sites, the boards
                                                   read through Discover's fetcher, a checked Haiku read; the guarded
                                                   Claude client)
    InterviewService                              (practice the interview: Haiku phrases, judges and debriefs; the guarded
                                                   Claude client, its own session store, and a role read back through
                                                   the job search)
    SpeechService                                 (Sarvam AI speech for practice interviews: the engine over the official
                                                   SDK when SARVAM_API_KEY is set and speech is on, none otherwise; a soft
                                                   budget in the kv table; its own HTTP client)
    PrepareService                                ("How do I prepare for this role?": the plan's numbers from the graph,
                                                   two calls of the chat model at once, checked and stored; the guarded
                                                   Claude client and the plan store)
    HandoffService                                (the marketing site's hand-off: a resume and links posted once, taken once)
    WeeklyService                                 (The Leafout Weekly: subscriptions, editions built from the reader's graph and
                                                   emailed on their day; the guarded Claude client for the news and the lead's
                                                   read, the spend guard around them, the mail provider when a key is set)
    CardService                                   (the radar card: the owner's settings and link, the snapshot drawn from the
                                                   newest profile; no model)
    match_anonymous                               (may a visitor with no sign-in match a posting: the setting
                                                   resolved against the sign-in methods this server offers)
    maintenance                                   (retention purge, a tracked background task; it keeps
                                                   the bundled sample persona's profile)
    weekly_loop                                   (every 15 minutes: build and email the editions that are due; a tracked
                                                   background task, switched by ``weekly.enabled``)

The FastAPI lifespan builds one container per process and `aclose`s it on shutdown. The CLI builds its own
from the same settings. Tests hand in `ContainerOverrides` to swap any part for a fake; the container closes
only what it built itself, so an object passed in stays open for whoever created it.

Policy: when Jev is switched off (``TAYYARI_JEV__ENABLED=false``) or no ``JEV_API_KEY`` is set, decisions are a
`NullDecisionService` and Jev is skipped entirely (behaviour like the legacy app). Claude fallbacks only cover
individual low-confidence Jev answers; they never stand in for Jev as a whole.

The service classes (analysis, chat, portfolio, decisions) are imported inside `build`, not at module level, so
importing this module (the CLI help, the layering test) stays cheap and cannot fail on a service that is
mid-rewrite.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx2

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

if TYPE_CHECKING:
    import requests
    import stripe

    from tayyari.auth.email import EmailSender
    from tayyari.auth.service import AuthService
    from tayyari.billing.entitlements import EntitlementService
    from tayyari.billing.service import BillingService
    from tayyari.card.png import CardPictures
    from tayyari.card.service import CardService
    from tayyari.discover.service import DiscoverService
    from tayyari.handoff.service import HandoffService
    from tayyari.interview.service import InterviewService
    from tayyari.jobsearch.service import JobSearchService
    from tayyari.match.service import MatchService
    from tayyari.portfolio.service import PortfolioService
    from tayyari.prepare.service import PrepareService
    from tayyari.speech.engine import SpeechEngine
    from tayyari.speech.service import SpeechService
    from tayyari.weekly.service import WeeklyService

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
    - ``decisions``, ``analysis``, ``chat``, ``portfolio``, ``auth``: the services themselves.
    - ``mailer``: the sender of sign-in emails (otherwise Resend when ``RESEND_API_KEY`` is set, else none).
    - ``auth_transport``: an ``httpx2`` transport for every outgoing sign-in call (Google's token endpoint and keys,
      the email provider); tests hand in an ``httpx2.MockTransport`` so the flow runs without a network.
    - ``entitlements``, ``billing``, ``match``, ``discover``: the services themselves.
    - ``discover_transport``: an ``httpx2`` transport for Discover's reads of employers' boards (and the job search's);
      tests hand in an ``httpx2.MockTransport`` so a refresh runs without a network.
    - ``jobsearch``, ``interview``, ``prepare``: the job search, practice interview and prepare services themselves.
    - ``handoff``: the marketing-site hand-off service itself.
    - ``weekly``: The Leafout Weekly service itself (``mailer`` also stands in for its mail provider).
    - ``card``: the radar card service itself.
    - ``speech``: the speech service itself. ``speech_engine``: just its engine (the service is still built here, over the
      real ``kv`` budget); giving one switches Sarvam speech on without a ``SARVAM_API_KEY`` (the
      ``TAYYARI_SPEECH__ENABLED`` kill switch still wins). Tests hand in a fake engine; nothing calls Sarvam.
    - ``razorpay_session``: the ``requests.Session`` the Razorpay SDK sends through; tests hand in one that answers
      from memory.
    - ``stripe_http``: a ``stripe.HTTPClient`` for the Stripe SDK's requests; tests hand in one that answers from memory.
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
    portfolio: PortfolioService | None = None
    auth: AuthService | None = None
    mailer: EmailSender | None = None
    auth_transport: httpx2.AsyncBaseTransport | None = None
    entitlements: EntitlementService | None = None
    billing: BillingService | None = None
    match: MatchService | None = None
    discover: DiscoverService | None = None
    discover_transport: httpx2.AsyncBaseTransport | None = None
    jobsearch: JobSearchService | None = None
    interview: InterviewService | None = None
    speech: SpeechService | None = None
    speech_engine: SpeechEngine | None = None
    prepare: PrepareService | None = None
    handoff: HandoffService | None = None
    weekly: WeeklyService | None = None
    card: CardService | None = None
    razorpay_session: requests.Session | None = None
    stripe_http: stripe.HTTPClient | None = None


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
    portfolio: PortfolioService
    auth: AuthService
    entitlements: EntitlementService
    """What an account may use. The posting-match routes call ``entitlements.consume(user_id, match_id)``."""
    billing: BillingService
    match: MatchService
    """The posting match pipeline. Routes pay for a match with ``entitlements.consume`` before they run it."""
    match_anonymous: bool
    """Whether a visitor with no sign-in may match a posting: ``settings.match.allow_anonymous`` resolved against the
    sign-in methods this server offers (``auto`` is true exactly when it offers none). Decided once, at startup."""
    discover: DiscoverService
    """Job discovery. It starts a refresh of the employers' boards in the background when its data is old; the container
    cancels that task at shutdown."""
    jobsearch: JobSearchService
    """Search with AI. The route reserves spend and counts the run; every Claude call goes through the guarded client."""
    interview: InterviewService
    """Practice the interview. The routes count a started interview and reserve spend; every Claude call goes through the
    guarded client."""
    speech: SpeechService
    """Speech for practice interviews through Sarvam AI. Its engine is None without a key (the browser's own speech is used);
    the routes count each request in the ``speech`` window."""
    prepare: PrepareService
    """Prepare for a role. The routes count a written plan and a headline rewrite in the ``prepare`` window and reserve
    spend; every Claude call goes through the guarded client."""
    handoff: HandoffService
    """The marketing site's hand-off. The routes count a hand-off in the ``handoff`` window; no model is called."""
    weekly: WeeklyService
    """The Leafout Weekly. The routes count an edition built on demand in the ``weekly`` window; the paid part of a build
    reserves ``est_weekly_usd`` itself; every Claude call goes through the guarded client."""
    card: CardService
    """The radar card's owner side. No model is called; the public routes count in the ``card`` window."""
    card_pictures: CardPictures
    """The radar card's LinkedIn pictures, drawn by resvg off the event loop and kept in memory by link and version."""
    rate_limiter: RateLimiter
    spend_guard: SpendGuard
    ip_hash_key: bytes
    analysis_slots: asyncio.Semaphore
    """How many analyses this process runs at once (``settings.concurrency.max_concurrent_analyses``)."""
    jev_active: bool
    """True when decisions go through Jev (it is enabled and has a key, or a client was injected)."""
    maintenance: asyncio.Task[None] | None = None
    weekly_loop: asyncio.Task[None] | None = None
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
        from tayyari.auth.email import ResendEmailSender
        from tayyari.auth.google import STATE_KEY_NAME
        from tayyari.auth.service import AUTH_HTTP_HEADERS, AuthService
        from tayyari.billing.entitlements import EntitlementService
        from tayyari.billing.products import below_floor
        from tayyari.billing.razorpay import RazorpayGateway
        from tayyari.billing.service import BillingService
        from tayyari.billing.stripe_gw import StripeGateway
        from tayyari.card.png import CardPictures
        from tayyari.card.service import CardService
        from tayyari.chat.service import CoachChatService
        from tayyari.decisions.packs.service_impl import JevDecisionService
        from tayyari.discover.service import DiscoverService
        from tayyari.handoff.service import HandoffService
        from tayyari.interview.service import InterviewService
        from tayyari.jobsearch.boards import BoardReader
        from tayyari.jobsearch.service import JobSearchService
        from tayyari.match.service import MatchService
        from tayyari.portfolio.service import PortfolioService
        from tayyari.prepare.service import PrepareService
        from tayyari.speech.service import SpeechService
        from tayyari.weekly.compose import EditionComposer
        from tayyari.weekly.jobs import JobDesk
        from tayyari.weekly.news import NewsDesk
        from tayyari.weekly.service import WeeklyService

        ov = overrides or ContainerOverrides()
        configure_logging(settings)
        clock: Clock = ov.clock if ov.clock is not None else SystemClock()
        closers: list[tuple[str, _Closer]] = []
        maintenance: asyncio.Task[None] | None = None
        weekly_loop: asyncio.Task[None] | None = None
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
            auth_state_key = await repos.kv.get_or_create_secret(STATE_KEY_NAME)

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
            portfolio: PortfolioService
            if ov.portfolio is not None:
                portfolio = ov.portfolio
            else:
                portfolio = PortfolioService(
                    settings=settings,
                    claude=claude,
                    decisions=decisions,
                    portfolios=repos.portfolios,
                    profiles=repos.profiles,
                    sample=sample,
                    clock=clock,
                )
                closers.append(("portfolio", portfolio.aclose))
            auth: AuthService
            if ov.auth is not None:
                auth = ov.auth
            else:
                auth_http = httpx2.AsyncClient(
                    timeout=httpx2.Timeout(10.0), transport=ov.auth_transport, headers=AUTH_HTTP_HEADERS
                )
                closers.append(("auth_http", auth_http.aclose))
                mailer = ov.mailer
                if mailer is None and settings.resend_api_key is not None:
                    mailer = ResendEmailSender(
                        api_key=settings.resend_api_key.get_secret_value(),
                        sender=settings.resend_from,
                        client=auth_http,
                    )
                auth = AuthService(
                    settings=settings,
                    users=repos.users,
                    limits=repos.limits,
                    clock=clock,
                    state_key=auth_state_key,
                    mailer=mailer,
                    http=auth_http,
                    transport=ov.auth_transport,
                )

            entitlements: EntitlementService
            if ov.entitlements is not None:
                entitlements = ov.entitlements
            else:
                entitlements = EntitlementService(repos.billing, settings.billing, clock)
            billing: BillingService
            if ov.billing is not None:
                billing = ov.billing
            else:
                razorpay: RazorpayGateway | None = None
                if settings.razorpay_key_id is not None and settings.razorpay_key_secret is not None:
                    webhook_secret = settings.razorpay_webhook_secret
                    razorpay = RazorpayGateway(
                        key_id=settings.razorpay_key_id.get_secret_value(),
                        key_secret=settings.razorpay_key_secret.get_secret_value(),
                        webhook_secret=None if webhook_secret is None else webhook_secret.get_secret_value(),
                        session=ov.razorpay_session,
                    )
                    if not settings.razorpay_webhooks_enabled:
                        log.warning(
                            "razorpay_webhooks_off",
                            hint="hand-off only: a payment whose browser never returns is not granted until a webhook is configured",
                        )
                stripe_gateway: StripeGateway | None = None
                if settings.stripe_secret_key is not None and settings.stripe_webhook_secret is not None:
                    stripe_gateway = StripeGateway(
                        secret_key=settings.stripe_secret_key.get_secret_value(),
                        webhook_secret=settings.stripe_webhook_secret.get_secret_value(),
                        http_client=ov.stripe_http,
                    )
                billing = BillingService(
                    settings=settings,
                    repo=repos.billing,
                    entitlements=entitlements,
                    clock=clock,
                    razorpay=razorpay,
                    stripe=stripe_gateway,
                )
                for item in below_floor(settings):
                    log.warning(
                        "price_below_provider_minimum",
                        product=item.product,
                        currency=item.currency,
                        price_minor=item.price_minor,
                        minimum_minor=item.minimum_minor,
                    )

            match: MatchService
            if ov.match is not None:
                match = ov.match
            else:
                match = MatchService(
                    claude=claude,
                    model=settings.match_model,
                    writer_model=settings.match_writer_model,
                    postings=repos.postings,
                    facts_cache=repos.profile_facts,
                    call_timeout_s=settings.match.call_timeout_s,
                    clock=clock,
                )

            discover: DiscoverService
            if ov.discover is not None:
                discover = ov.discover
            else:
                discover = DiscoverService(
                    settings=settings.discover,
                    repo=repos.discover,
                    clock=clock,
                    transport=ov.discover_transport,
                )
                closers.append(("discover", discover.aclose))
                log.info(
                    "discover_ready",
                    enabled=settings.discover.enabled,
                    employers=len(discover.registry.employers),
                    refresh_hours=settings.discover.refresh_hours,
                )

            jobsearch: JobSearchService
            if ov.jobsearch is not None:
                jobsearch = ov.jobsearch
            else:
                found_by = discover
                jobsearch = JobSearchService(
                    claude=claude,
                    settings=settings.jobsearch,
                    repo=repos.discover,
                    runs=repos.jobsearch,
                    registry=lambda: found_by.registry,
                    reader=BoardReader(
                        clock=clock,
                        timeout_s=settings.jobsearch.request_timeout_s,
                        concurrency=settings.jobsearch.fetch_concurrency,
                        cache_minutes=settings.jobsearch.board_cache_minutes,
                        listing_cap=settings.discover.max_listings_per_employer,
                        transport=ov.discover_transport,
                    ),
                    clock=clock,
                    discover_enabled=lambda: found_by.enabled,
                )
                log.info(
                    "jobsearch_ready",
                    enabled=settings.jobsearch.enabled,
                    model=settings.jobsearch.model,
                    scouts=settings.jobsearch.scouts,
                    searches_per_scout=settings.jobsearch.searches_per_scout,
                )

            interview: InterviewService
            if ov.interview is not None:
                interview = ov.interview
            else:
                interview = InterviewService(
                    claude=claude,
                    settings=settings.interview,
                    sessions=repos.interview,
                    marks=repos.claim_marks,
                    roles=jobsearch,
                    clock=clock,
                    plans=repos.prepare,
                )
                log.info(
                    "interview_ready",
                    enabled=settings.interview.enabled,
                    model=settings.interview.model,
                    assistant_messages=settings.interview.assistant_messages,
                )

            speech: SpeechService
            if ov.speech is not None:
                speech = ov.speech
            else:
                engine = ov.speech_engine
                if engine is None and settings.sarvam_api_key is not None and settings.speech.enabled:
                    from tayyari.speech.sarvam import SarvamEngine

                    built_engine = SarvamEngine(settings.sarvam_api_key, settings.speech)
                    closers.append(("sarvam", built_engine.aclose))
                    engine = built_engine
                speech = SpeechService(engine, settings.speech, repos.kv, clock)
                closers.append(("speech", speech.aclose))
                log.info(
                    "speech_ready",
                    provider="sarvam" if speech.configured else None,
                    enabled=settings.speech.enabled,
                    key_set=settings.sarvam_api_key is not None,
                    stt_model=settings.speech.stt_model,
                    tts_model=settings.speech.tts_model,
                    stt_daily_seconds=settings.speech.stt_daily_seconds,
                    tts_daily_chars=settings.speech.tts_daily_chars,
                )

            prepare: PrepareService
            if ov.prepare is not None:
                prepare = ov.prepare
            else:
                prepare = PrepareService(
                    claude=claude,
                    model=settings.chat_model,
                    effort=settings.chat_effort,
                    repo=repos.prepare,
                    profiles=repos.profiles,
                    sample=sample,
                    clock=clock,
                )
                log.info("prepare_ready", model=settings.chat_model, effort=settings.chat_effort)

            handoff: HandoffService
            if ov.handoff is not None:
                handoff = ov.handoff
            else:
                handoff = HandoffService(repos.handoff, settings.handoff, env=settings.env, clock=clock)
                log.info(
                    "handoff_ready",
                    origins=len(settings.handoff.origins),
                    localhost=settings.handoff.localhost_allowed(settings.env),
                    ttl_seconds=settings.handoff.ttl_seconds,
                )

            weekly: WeeklyService
            if ov.weekly is not None:
                weekly = ov.weekly
            else:
                weekly_mailer = ov.mailer
                if weekly_mailer is None and settings.resend_api_key is not None:
                    weekly_http = httpx2.AsyncClient(
                        timeout=httpx2.Timeout(10.0), transport=ov.auth_transport, headers=AUTH_HTTP_HEADERS
                    )
                    closers.append(("weekly_http", weekly_http.aclose))
                    weekly_mailer = ResendEmailSender(
                        api_key=settings.resend_api_key.get_secret_value(),
                        sender=settings.weekly.sender or settings.resend_from,
                        client=weekly_http,
                    )
                found_in = discover
                weekly = WeeklyService(
                    settings=settings.weekly,
                    repo=repos.weekly,
                    users=repos.users,
                    composer=EditionComposer(
                        settings=settings.weekly,
                        jobs=JobDesk(
                            claude=claude,
                            settings=settings.weekly,
                            repo=repos.discover,
                            registry=lambda: found_in.registry,
                        ),
                        news=NewsDesk(claude, settings.weekly),
                        users=repos.users,
                        profiles=repos.profiles,
                        prepare=repos.prepare,
                        interview=repos.interview,
                        marks=repos.claim_marks,
                        reserve=spend_guard.reserve,
                        est_usd=settings.guard.est_weekly_usd,
                    ),
                    mailer=weekly_mailer,
                    clock=clock,
                    env=settings.env,
                    data_dir=settings.data_dir,
                )
                log.info(
                    "weekly_ready",
                    enabled=settings.weekly.enabled,
                    email="resend" if weekly_mailer is not None else None,
                    news=settings.weekly.news,
                    model=settings.weekly.model,
                    paid_cap=settings.weekly.max_paid_editions_per_week,
                )

            card = (
                ov.card
                if ov.card is not None
                else CardService(
                    profiles=repos.profiles,
                    users=repos.users,
                    cards=repos.card,
                    clock=clock,
                    public_url=settings.auth.public_url,
                )
            )

            match_anonymous = settings.match.anonymous_allowed(auth.providers)
            log.info(
                "match_anonymous",
                allowed=match_anonymous,
                setting=settings.match.allow_anonymous,
                google=auth.providers.google,
                magic=auth.providers.magic,
                leafout=auth.providers.leafout,
            )

            # ── background work: never blocks startup ──
            if db is not None:
                # The bundled sample persona is the one sample profile the purge must never delete.
                sample_id = sample.sample_profile_id()
                keep = {sample_id} if sample_id else set()
                maintenance = asyncio.create_task(
                    _maintenance_loop(db, settings, clock, keep, card), name="tayyari-maintenance"
                )
            if settings.weekly.enabled:
                weekly_loop = asyncio.create_task(_weekly_loop(weekly, settings), name="tayyari-weekly")
        except BaseException:
            if maintenance is not None:
                maintenance.cancel()
            if weekly_loop is not None:
                weekly_loop.cancel()
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
            portfolio=portfolio,
            auth=auth,
            entitlements=entitlements,
            billing=billing,
            match=match,
            match_anonymous=match_anonymous,
            discover=discover,
            jobsearch=jobsearch,
            interview=interview,
            speech=speech,
            prepare=prepare,
            handoff=handoff,
            weekly=weekly,
            card=card,
            card_pictures=CardPictures(settings.card.png_cache_size),
            rate_limiter=rate_limiter,
            spend_guard=spend_guard,
            ip_hash_key=ip_hash_key,
            analysis_slots=asyncio.Semaphore(settings.concurrency.max_concurrent_analyses),
            jev_active=jev_active,
            maintenance=maintenance,
            weekly_loop=weekly_loop,
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
        for task in (self.maintenance, self.weekly_loop):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.maintenance = self.weekly_loop = None
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
    db: Database,
    settings: Settings,
    clock: Clock,
    keep_profile_ids: Collection[str] = (),
    card: CardService | None = None,
) -> None:
    """Retention purge shortly after startup and then every few hours, then the radar cards whose account has a newer
    profile are redrawn (so a shared card follows the graph within a day). Never raises (except cancellation).

    ``keep_profile_ids`` are the sample profiles the purge leaves alone (the bundled persona).
    """
    await asyncio.sleep(MAINTENANCE_DELAY_S)
    while True:
        try:
            await run_maintenance(db, settings, clock.now(), keep_profile_ids=keep_profile_ids)
        except Exception:
            log.error("maintenance_failed", exc_info=True)
        if card is not None:
            try:
                moved = await card.refresh_all()
                if moved:
                    log.info("cards_refreshed", moved=moved)
            except Exception:
                log.error("card_refresh_failed", exc_info=True)
        # Real time on purpose: a test clock that "sleeps" instantly would turn this into a busy loop.
        await asyncio.sleep(MAINTENANCE_INTERVAL_S)


async def _weekly_loop(service: WeeklyService, settings: Settings) -> None:
    """The Leafout Weekly: every few minutes (15 by default) build and email the editions that are due. Never raises (except
    cancellation). `WeeklyService.run_once` knows nothing of the schedule: it looks at the India clock itself, claims each
    reader's week in the database so two replicas never both work on it, and does nothing outside a reader's day."""
    await asyncio.sleep(settings.weekly.first_tick_delay_s)
    while True:
        try:
            await service.run_once()
        except Exception:
            log.error("weekly_loop_failed", exc_info=True)
        # Real time on purpose, like the maintenance loop: a test clock that "sleeps" instantly would spin.
        await asyncio.sleep(settings.weekly.tick_seconds)


__all__ = ["DRAIN_TIMEOUT_S", "IP_HASH_KEY", "Container", "ContainerOverrides"]
