"""`PortfolioService`: builds a portfolio from a profile, streams its generation, and serves the reads and edits.

The pure pipeline is `build` (no repository, so the CLI evals can run it on a fixture):

    facts      Haiku turns the stored source texts into structured facts: it points at numbered source lines and code
               copies their text, so every quote and highlight is verbatim (`facts`). The facts of an earlier
               portfolio are reused when the source texts are unchanged.
               Jev's first read of the resume runs beside it; its answers feed the composer.
    design     the frozen graph snapshot, the person, the proof stats and the deterministic designer (`compose`):
               up to three valid looks. A draft page with verbatim fallback wording is sent here, so the page paints
               before any copy exists.
    copy       Sonnet writes the slots from the facts only, as two concurrent calls (the fixed slots; the per-fact
               ones), so the wall time is that of the longer one. Every slot is checked against the quotes of the
               facts it cites (code, then Jev); the ones that fail get ONE re-ask with the checker's notes; what still
               fails, and what the model left out, comes from the deterministic fallback wording (always grounded).
    check      a last strict pass over every generated slot: `BuildReport.ungrounded` must be 0.

A model failure never fails the page: an error or timeout in the facts or the copy step keeps the fallback wording
and the page is still complete. Only the daily spend ceiling stops a build (`SpendCeilingReached` propagates).

`generate` is the NDJSON stream behind POST /api/portfolio (progress, a draft, then done or error). `get`,
`has_readable`, `patch`, `rewrite`, `delete`, `public_preview` and `public` are what the other routes call. The
portfolio lives in the `portfolios` table as one JSON document, apart from the profile: the row owns the public id,
the version, the timestamps and the published flag, and `_hydrate` always takes those from the row. A page written for
a profile that an account has claimed carries that account's id (`portfolios.user_id`, read from the profile when the
build is saved); who may *open* the page is decided before the service is called, by `api.deps.require_profile_access`.

A regenerate keeps the owner's choices (`compose.carry_owner_choices`: each block's visible / public flags, the hidden
graph areas, the privacy switches, the default view). It is saved over the row as it is *when the build ends*, not as
it was when the build began: a page published, edited or privacy-switched while the model ran keeps that state, and a
page deleted meanwhile is not brought back.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final

from pydantic import ValidationError

from tayyari.contract.models import Profile
from tayyari.contract.portfolio import (
    FACT_SLOT_PREFIXES,
    KIT_VERSION,
    CopySlot,
    Fact,
    GraphSnapshot,
    GraphSpec,
    Portfolio,
    PortfolioContent,
    PortfolioDoneEvent,
    PortfolioDraftEvent,
    PortfolioErrorEvent,
    PortfolioEvent,
    PortfolioPatch,
    PortfolioPerson,
    PortfolioProgressEvent,
    PortfolioSpec,
    ProgressStep,
    PublicPortfolio,
    PublishInfo,
    slot_limit,
    spec_problems,
)
from tayyari.contract.requests import RequestContext
from tayyari.contract.stored import StoredContext
from tayyari.core.clock import Clock, SystemClock
from tayyari.core.errors import AppError, BadRequest, NotFound, ServiceUnavailable
from tayyari.core.ids import is_sample_id, new_public_id
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings
from tayyari.decisions.service import DecisionContext, DecisionService, FirstReadInput
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import NETWORK_MESSAGE, ClaudeError, SpendCeilingReached
from tayyari.llm.types import CallContext
from tayyari.sample.provider import SampleProvider
from tayyari.storage.ports import PortfolioConflict, PortfolioRepo, ProfileRepo, StoredPortfolio

from .compose import carry_owner_choices, compose, default_spec, person_seed
from .copy import partition_slots, slot_ids_to_fill, write_copy
from .edit import apply_patch
from .facts import DroppedFact, extract_facts, facts_hash, usable_sources
from .fallback import fallback_slots
from .features import compute_features
from .ground import check_slot, verify_slots
from .public import to_public
from .snapshot import build_person, graph_snapshot, open_to, proof_stats

log = get_logger(__name__)

STEP_LABELS: Final[dict[ProgressStep, str]] = {
    "facts": "Reading your resume",
    "design": "Designing your page",
    "copy": "Writing your copy",
    "check": "Checking every line against your sources",
}
"""The calm labels of the four progress steps, in the order the stream sends them."""

PROFILE_NOT_FOUND = "We couldn't find that profile. Run the analysis again to build your Skill Graph first."
PORTFOLIO_NOT_FOUND = "Portfolio not found"
PAUSED_MESSAGE = (
    "Portfolio generation is paused for today because Tayyari has reached its daily usage limit. "
    "Your Skill Graph and chat still work. Please try again tomorrow."
)
GENERIC_MESSAGE = "Something went wrong while building your portfolio. Please try again."
REWRITE_UNAVAILABLE = "We couldn't rewrite that section just now. Please try again in a moment."
NOTHING_TO_REWRITE_FROM = "There are no resume details to write this section from yet."
CANNOT_REWRITE = "That section can't be written from your resume details. You can edit it by hand instead."
GRAPH_GONE = "The analysis behind this page is no longer available, so its graph can't be updated."
DELETED_MEANWHILE = "This page was deleted while it was being rebuilt, so the new version was not saved."

FACTS_TIMEOUT_S: Final = 120.0
COPY_TIMEOUT_S: Final = 120.0
FIRST_READ_TIMEOUT_S: Final = 8.0
NOTE_MAX_CHARS: Final = 600
"""A slot's validator notes are cut to this many characters in the re-ask prompt."""
MIN_GRAPH_DOMAINS: Final = 3

Emit = Callable[[PortfolioProgressEvent | PortfolioDraftEvent], None]


class PortfolioVersionConflict(AppError):
    """The page was saved by someone else since it was read (HTTP 409)."""

    status = 409
    code = "portfolio_conflict"
    user_message = "This page was changed somewhere else. Reload it and try again."


@dataclass(frozen=True, slots=True)
class BuildReport:
    """What a build did, in counts (never text): the numbers the CLI eval prints and the log line carries."""

    facts_kept: int = 0
    facts_dropped: int = 0
    """Whole facts the source checks refused."""
    parts_dropped: int = 0
    """Fields and highlights refused inside facts that were kept."""
    facts_reused: bool = False
    facts_partial: bool = False
    """Some sources could not be read (their call failed): the facts are incomplete and are not cached."""
    facts_failed: bool = False
    """The extraction call failed: the page was built from the profile alone."""
    slots_model: int = 0
    slots_resume: int = 0
    slots_missing: int = 0
    """Slots that ended with no wording at all (nothing to say, so the block shows without them)."""
    reasked: int = 0
    fallbacks: int = 0
    """Slots the model was asked for and the deterministic wording replaced."""
    copy_failed: bool = False
    ungrounded: int = 0
    """Generated slots that failed the final strict check (they are left off the page). Must be 0."""
    seconds: float = 0.0
    dropped: tuple[DroppedFact, ...] = ()


@dataclass(frozen=True, slots=True)
class BuildResult:
    portfolio: Portfolio
    generated: list[CopySlot]
    """The slots as generated (model or fallback wording): what "reset to generated wording" restores."""
    facts_hash: str | None
    """Cache key of the facts; None when the facts were not extracted (a failed call, or an offline build)."""
    report: BuildReport


@dataclass(slots=True)
class _Copy:
    slots: list[CopySlot] = field(default_factory=list)
    reasked: int = 0
    fallbacks: int = 0
    missing: int = 0
    ungrounded: int = 0
    failed: bool = False


async def _gather[T](*calls: Awaitable[T]) -> list[T]:
    """The results of ``calls`` run concurrently, in order; the first error cancels the rest and is raised."""
    tasks = [asyncio.ensure_future(c) for c in calls]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _iso(moment: datetime) -> str:
    stamp = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _note(problems: Sequence[str]) -> str:
    return "; ".join(problems)[:NOTE_MAX_CHARS]


def graph_is_outdated(snapshot: GraphSnapshot, profile: Profile, today: date) -> bool:
    """Whether the profile's graph has moved on from ``snapshot`` (a domain, a score or the skill count differs)."""
    fresh = graph_snapshot(profile, today)
    return [(d.id, d.score) for d in fresh.domains] != [(d.id, d.score) for d in snapshot.domains] or (
        fresh.skillCount != snapshot.skillCount
    )


def event_message(exc: BaseException) -> str:
    """The sentence a person may see for a failure (never ``str(exc)``: that can hold paths or provider text)."""
    if isinstance(exc, SpendCeilingReached):
        return PAUSED_MESSAGE
    if isinstance(exc, AppError | ClaudeError):
        return exc.user_message
    return GENERIC_MESSAGE


class PortfolioService:
    """See the module docstring."""

    def __init__(
        self,
        *,
        settings: Settings,
        claude: ClaudeClient,
        decisions: DecisionService,
        portfolios: PortfolioRepo,
        profiles: ProfileRepo,
        sample: SampleProvider,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._claude = claude
        self._decisions = decisions
        self._portfolios = portfolios
        self._profiles = profiles
        self._sample = sample
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._workers: set[asyncio.Task[None]] = set()

    async def aclose(self) -> None:
        """Cancel the generations still running (shutdown) and wait until they are gone."""
        workers = list(self._workers)
        for task in workers:
            task.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

    # ───────────────────────── the pure pipeline ─────────────────────────

    async def build(
        self,
        profile: Profile,
        ctx: StoredContext,
        *,
        profile_id: str,
        existing: StoredPortfolio | None,
        call_ctx: CallContext,
        decision_ctx: DecisionContext,
        emit: Emit | None = None,
        offline: bool = False,
    ) -> BuildResult:
        """Facts, design and copy for one profile (see the module docstring); nothing is stored.

        ``existing`` supplies the cached facts and the public id. ``emit`` receives the progress and draft events.
        ``offline`` makes no model call at all (no facts, no first read, fallback wording only).
        """
        started = self._clock.monotonic()
        now = self._clock.now()
        today = now.date()
        self._send(emit, PortfolioProgressEvent(step="facts", label=STEP_LABELS["facts"]))

        source_hash = facts_hash(ctx)
        first_read_task: asyncio.Task[Mapping[str, Any] | None] | None = None
        if not offline:
            first_read_task = asyncio.create_task(self._first_read(ctx, decision_ctx, today))
        try:
            facts, dropped, reused, facts_failed, facts_partial = await self._facts(
                profile, ctx, existing, source_hash, call_ctx, offline=offline
            )
        except BaseException:
            if first_read_task is not None:
                first_read_task.cancel()
                await asyncio.gather(first_read_task, return_exceptions=True)
            raise
        first_read = await first_read_task if first_read_task is not None else None

        self._send(emit, PortfolioProgressEvent(step="design", label=STEP_LABELS["design"]))
        graph = graph_snapshot(profile, today)
        person = build_person(profile)
        slot_ids = slot_ids_to_fill(facts)
        verbatim = [s for s in fallback_slots(facts, person, graph, slot_ids) if s.text.strip()]
        content = PortfolioContent(
            person=person,
            facts=facts,
            slots=verbatim,
            stats=proof_stats(profile, ctx),
            practice=None,
            openTo=open_to(profile),
        )
        looks = self._design(profile, facts, first_read, content, graph, profile_id)
        public_id = existing.public_id if existing is not None else new_public_id()
        version = existing.version + 1 if existing is not None else 1
        created_at = existing.created_at if existing is not None else now
        published = existing.published if existing is not None else False
        previous = self._try_hydrate(existing)

        def assemble(body: PortfolioContent) -> Portfolio:
            page = Portfolio(
                profileId=profile_id,
                publicId=public_id,
                version=version,
                kitVersion=KIT_VERSION,
                createdAt=_iso(created_at),
                updatedAt=_iso(now),
                lookIndex=0,
                looks=looks,
                spec=looks[0].model_copy(deep=True),
                content=body,
                graph=graph,
                publish=PublishInfo(
                    state="published" if published else "draft", publishedAt=None, defaultView="standard"
                ),
                graphOutdated=False,
            )
            return carry_owner_choices(page, previous) if previous is not None else page

        self._send(emit, PortfolioDraftEvent(portfolio=assemble(content)))

        self._send(emit, PortfolioProgressEvent(step="copy", label=STEP_LABELS["copy"]))
        written = await self._write_slots(
            facts,
            person,
            graph,
            slot_ids,
            call_ctx,
            decision_ctx,
            emit=emit,
            offline=offline,
        )
        portfolio = assemble(content.model_copy(update={"slots": written.slots}))

        report = BuildReport(
            facts_kept=len(facts),
            facts_dropped=sum(1 for d in dropped if d.part == "fact"),
            parts_dropped=sum(1 for d in dropped if d.part != "fact"),
            facts_reused=reused,
            facts_partial=facts_partial,
            facts_failed=facts_failed,
            slots_model=sum(1 for s in written.slots if s.origin == "model"),
            slots_resume=sum(1 for s in written.slots if s.origin == "resume"),
            slots_missing=written.missing,
            reasked=written.reasked,
            fallbacks=written.fallbacks,
            copy_failed=written.failed,
            ungrounded=written.ungrounded,
            seconds=round(self._clock.monotonic() - started, 3),
            dropped=tuple(dropped),
        )
        log.info("portfolio_built", profile_id=profile_id, **_counts(report))
        return BuildResult(
            portfolio=portfolio,
            generated=[s.model_copy(deep=True) for s in written.slots],
            # an incomplete extraction is never cached: the next build reads every source again
            facts_hash=None if offline or facts_failed or facts_partial else source_hash,
            report=report,
        )

    @staticmethod
    def _send(emit: Emit | None, event: PortfolioProgressEvent | PortfolioDraftEvent) -> None:
        if emit is not None:
            emit(event)

    async def _model[T](
        self, what: str, call: Callable[[], Awaitable[T]], timeout_s: float, *, degrade: bool = True
    ) -> T | None:
        """One model call with a deadline. A failure (Claude error, timeout, even a bug) gives None and is logged,
        so the page keeps its fallback wording; the spend ceiling always propagates. ``degrade=False`` lets the
        failure out instead (a section rewrite has nothing to fall back on)."""
        try:
            async with asyncio.timeout(timeout_s):
                return await call()
        except SpendCeilingReached:
            raise
        except (ClaudeError, TimeoutError) as exc:
            if not degrade:
                raise
            log.warning(f"portfolio_{what}_failed", error=type(exc).__name__, kind=getattr(exc, "kind", None))
            return None
        except Exception:
            if not degrade:
                raise
            log.error(f"portfolio_{what}_crashed", exc_info=True)
            return None

    # ── facts ──

    async def _facts(
        self,
        profile: Profile,
        ctx: StoredContext,
        existing: StoredPortfolio | None,
        source_hash: str,
        call_ctx: CallContext,
        *,
        offline: bool,
    ) -> tuple[list[Fact], list[DroppedFact], bool, bool, bool]:
        """``(facts, dropped, reused, failed, partial)``: ``partial`` when some sources could not be read."""
        if offline:
            return [], [], False, False, False
        cached = self._cached_facts(existing, source_hash)
        if cached is not None and (cached or not usable_sources(ctx)):
            # an empty cached result is only trusted when there was nothing to read: otherwise try again
            return cached, [], True, False, False
        result = await self._model(
            "facts",
            lambda: extract_facts(
                self._claude,
                model=self._settings.reader_model,
                ctx=ctx,
                profile=profile,
                call_ctx=call_ctx,
            ),
            FACTS_TIMEOUT_S,
        )
        if result is None:
            return [], [], False, True, False
        return list(result.facts), list(result.dropped), False, False, bool(result.failed_sources)

    @staticmethod
    def _cached_facts(existing: StoredPortfolio | None, source_hash: str) -> list[Fact] | None:
        """The facts of the stored portfolio when it was built from the same source texts, else None."""
        if existing is None or existing.facts_hash != source_hash:
            return None
        content = existing.doc.get("content")
        raw = content.get("facts") if isinstance(content, Mapping) else None
        if not isinstance(raw, list):
            return None
        try:
            return [Fact.model_validate(f) for f in raw]
        except ValidationError:
            return None

    async def _first_read(
        self, ctx: StoredContext, decision_ctx: DecisionContext, today: date
    ) -> Mapping[str, Any] | None:
        """Jev's read of the resume (career stage and family, public proof ...), or None. Never raises."""
        resume = next((s.content for s in ctx.sources if s.kind == "resume" and s.content.strip()), None)
        notes = None
        if resume is None:
            notes = next((s.content for s in ctx.sources if s.kind == "notes" and s.content.strip()), None)
            if notes is None:
                return None
        try:
            async with asyncio.timeout(FIRST_READ_TIMEOUT_S):
                result = await self._decisions.first_read(FirstReadInput(resume, notes, today), decision_ctx)
        except Exception as exc:
            log.warning("portfolio_first_read_failed", error_type=type(exc).__name__)
            return None
        return dict(result.facts) if result is not None else None

    # ── design ──

    @staticmethod
    def _design(
        profile: Profile,
        facts: list[Fact],
        first_read: Mapping[str, Any] | None,
        content: PortfolioContent,
        graph: GraphSnapshot,
        profile_id: str,
    ) -> list[PortfolioSpec]:
        """Up to three valid looks, best first; the plain default page when the designer cannot produce one."""
        looks: list[PortfolioSpec] = []
        try:
            features = compute_features(profile, facts, first_read)
            looks = [
                look
                for look in compose(features, content, graph, person_seed(profile_id))
                if not spec_problems(look)
            ]
        except Exception:
            log.error("portfolio_compose_failed", exc_info=True)
        return looks or [default_spec(content, graph)]

    # ── copy ──

    def _writer(
        self,
        facts: list[Fact],
        person: PortfolioPerson,
        graph: GraphSnapshot,
        call_ctx: CallContext,
        ids: list[str],
        notes: Mapping[str, str] | None = None,
    ) -> Callable[[], Awaitable[list[CopySlot]]]:
        """One Sonnet call for ``ids`` (with the checker's ``notes`` on a re-ask), ready to run."""

        async def call() -> list[CopySlot]:
            return await write_copy(
                self._claude,
                model=self._settings.synth_model,
                facts=facts,
                person=person,
                graph=graph,
                slot_ids=ids,
                call_ctx=call_ctx,
                notes=notes,
            )

        return call

    async def _write_slots(
        self,
        facts: list[Fact],
        person: PortfolioPerson,
        graph: GraphSnapshot,
        slot_ids: list[str],
        call_ctx: CallContext,
        decision_ctx: DecisionContext,
        *,
        emit: Emit | None = None,
        offline: bool = False,
        degrade: bool = True,
    ) -> _Copy:
        """The final slots for ``slot_ids``: the model's wording where it grounds, the fallback elsewhere.

        Model (the fixed slots and the per-fact slots as two concurrent calls), verify, ONE re-ask of the slots that
        failed (with the checker's notes), verify again; what is still not kept is taken from the fallback; a last
        strict check leaves off anything that does not ground. ``failed`` is set when a call of the first round
        gave nothing (its slots then come from the fallback).
        """
        out = _Copy()
        fallback = {s.id: s for s in fallback_slots(facts, person, graph, slot_ids) if s.text.strip()}
        kept: dict[str, CopySlot] = {}
        asked = bool(facts) and bool(slot_ids) and not offline
        if asked:
            parts = await _gather(
                *(
                    self._model(
                        "copy",
                        self._writer(facts, person, graph, call_ctx, group),
                        COPY_TIMEOUT_S,
                        degrade=degrade,
                    )
                    for group in partition_slots(slot_ids)
                )
            )
            drafted = [s for part in parts if part is not None for s in part]
            out.failed = any(part is None for part in parts)
            self._send(emit, PortfolioProgressEvent(step="check", label=STEP_LABELS["check"]))
            if drafted:
                good, problems = await verify_slots(
                    drafted, facts, self._decisions, decision_ctx, person=person
                )
                kept.update((s.id, s) for s in good)
                retry = [sid for sid in slot_ids if sid in problems]
                if retry:
                    out.reasked = len(retry)
                    again = await self._model(
                        "reask",
                        self._writer(
                            facts,
                            person,
                            graph,
                            call_ctx,
                            retry,
                            notes={sid: _note(problems[sid]) for sid in retry},
                        ),
                        COPY_TIMEOUT_S,
                    )
                    if again is not None:
                        better, _ = await verify_slots(
                            again, facts, self._decisions, decision_ctx, person=person
                        )
                        kept.update((s.id, s) for s in better if s.id in retry)
        else:
            self._send(emit, PortfolioProgressEvent(step="check", label=STEP_LABELS["check"]))

        if asked:
            out.fallbacks = sum(1 for sid in slot_ids if sid not in kept)
        by_id = {f.id: f for f in facts}
        for sid in slot_ids:
            failed_check = False
            for candidate in (kept.get(sid), fallback.get(sid)):
                if candidate is None or not candidate.text.strip():
                    continue
                if candidate.origin != "user" and not check_slot(candidate, by_id, person=person).ok:
                    failed_check = True  # never reaches the page; the next candidate gets its turn
                    continue
                out.slots.append(candidate)
                break
            else:
                out.missing += 1
            if failed_check:
                out.ungrounded += 1
        return out

    # ───────────────────────── persistence helpers ─────────────────────────

    @staticmethod
    def _doc(portfolio: Portfolio) -> dict[str, Any]:
        """The JSON stored for a portfolio (no ``graphOutdated``: it is computed when the page is read)."""
        return portfolio.model_copy(update={"graphOutdated": None}).model_dump(mode="json", exclude_none=True)

    @staticmethod
    def _hydrate(stored: StoredPortfolio) -> Portfolio:
        """The `Portfolio` of a stored row; the row, not the document, owns the ids, version, times and state."""
        portfolio = Portfolio.model_validate(stored.doc)
        portfolio.profileId = stored.profile_id
        portfolio.publicId = stored.public_id
        portfolio.version = stored.version
        portfolio.createdAt = _iso(stored.created_at)
        portfolio.updatedAt = _iso(stored.updated_at)
        portfolio.publish.state = "published" if stored.published else "draft"
        portfolio.graphOutdated = None
        return portfolio

    def _try_hydrate(self, stored: StoredPortfolio | None) -> Portfolio | None:
        if stored is None:
            return None
        try:
            return self._hydrate(stored)
        except ValidationError:
            log.error("portfolio_document_unreadable", profile_id=stored.profile_id)
            return None

    @staticmethod
    def _generated(stored: StoredPortfolio) -> list[CopySlot]:
        out: list[CopySlot] = []
        for raw in stored.generated:
            try:
                out.append(CopySlot.model_validate(raw))
            except ValidationError:
                continue
        return out

    def _record(
        self,
        portfolio: Portfolio,
        generated: Sequence[CopySlot],
        facts_hash_: str | None,
        existing: StoredPortfolio | None,
        now: datetime,
        owner: str | None = None,
    ) -> StoredPortfolio:
        published = existing.published if existing is not None else False
        doc = portfolio.model_copy(deep=True)
        doc.publish.state = "published" if published else "draft"
        return StoredPortfolio(
            profile_id=portfolio.profileId,
            public_id=existing.public_id if existing is not None else portfolio.publicId,
            doc=self._doc(doc),
            facts_hash=facts_hash_,
            generated=[s.model_dump(mode="json", exclude_none=True) for s in generated],
            version=existing.version if existing is not None else 0,
            published=published,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
            user_id=owner or (existing.user_id if existing is not None else None),
        )

    async def _owner_of(self, profile_id: str) -> str | None:
        """The account that has claimed the profile, or None (nobody has, or no stored profile): the owner a page
        written for it carries."""
        access = await self._profiles.get_access(profile_id)
        return None if access is None else access.user_id

    async def _load(self, profile_id: str) -> tuple[Profile, StoredContext] | None:
        """The profile and its source texts; the bundled sample persona is served from its files when it is not stored."""
        found = await self._profiles.get_for_chat(profile_id)
        if found is not None:
            raw, ctx = found
        else:
            sample = self._sample.sample_profile
            if sample is None or not self._sample.is_sample_profile(profile_id):
                return None
            raw, ctx = sample.to_dict(), self._sample.sample_context
        try:
            return Profile.from_dict(raw), ctx
        except ValidationError:
            log.error("portfolio_profile_unreadable", profile_id=profile_id)
            return None

    async def _current_profile(self, profile_id: str) -> Profile | None:
        """Only the profile (no source texts): what "is the graph outdated" needs."""
        raw = await self._profiles.get_profile(profile_id)
        if raw is None:
            sample = self._sample.sample_profile
            if sample is None or not self._sample.is_sample_profile(profile_id):
                return None
            raw = sample.to_dict()
        try:
            return Profile.from_dict(raw)
        except ValidationError:
            return None

    async def _with_outdated(self, portfolio: Portfolio, profile: Profile | None = None) -> Portfolio:
        """``portfolio`` with ``graphOutdated`` set from the current profile (None when the profile is gone)."""
        current = profile if profile is not None else await self._current_profile(portfolio.profileId)
        if current is not None:
            portfolio.graphOutdated = graph_is_outdated(portfolio.graph, current, self._clock.now().date())
        return portfolio

    def _is_sample(self, profile_id: str) -> bool:
        return is_sample_id(profile_id) or self._sample.is_sample_profile(profile_id)

    # ───────────────────────── generate ─────────────────────────

    async def generate(
        self, profile_id: str, regenerate: bool, rc: RequestContext
    ) -> AsyncIterator[PortfolioEvent]:
        """The NDJSON stream of one generation: progress steps, a draft, then ``done`` (or one ``error``).

        An existing portfolio is returned as ``done`` at once unless ``regenerate`` is set. The stream never
        raises; closing it early cancels the work.
        """
        queue: asyncio.Queue[PortfolioEvent | None] = asyncio.Queue()
        worker = asyncio.create_task(self._work(profile_id, regenerate, rc, queue), name="portfolio-generate")
        self._workers.add(worker)
        worker.add_done_callback(self._workers.discard)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _work(
        self,
        profile_id: str,
        regenerate: bool,
        rc: RequestContext,
        queue: asyncio.Queue[PortfolioEvent | None],
    ) -> None:
        try:
            await self._generate(profile_id, regenerate, rc, queue)
        except Exception as exc:
            if isinstance(exc, SpendCeilingReached | AppError | ClaudeError):
                log.info("portfolio_generate_stopped", error=type(exc).__name__, request_id=rc.request_id)
            else:
                log.error("portfolio_generate_failed", request_id=rc.request_id, exc_info=True)
            queue.put_nowait(PortfolioErrorEvent(message=event_message(exc)))
        finally:
            queue.put_nowait(None)

    async def _generate(
        self,
        profile_id: str,
        regenerate: bool,
        rc: RequestContext,
        queue: asyncio.Queue[PortfolioEvent | None],
    ) -> None:
        stored = await self._portfolios.get(profile_id)
        existing = self._try_hydrate(stored)
        loaded = await self._load(profile_id)
        if existing is not None and not regenerate:
            done = await self._with_outdated(existing, loaded[0] if loaded else None)
            queue.put_nowait(PortfolioDoneEvent(portfolio=done))
            return
        if loaded is None:
            queue.put_nowait(PortfolioErrorEvent(message=PROFILE_NOT_FOUND))
            return
        profile, ctx = loaded

        def emit(event: PortfolioProgressEvent | PortfolioDraftEvent) -> None:
            queue.put_nowait(event)

        result = await self.build(
            profile,
            ctx,
            profile_id=profile_id,
            existing=stored,
            call_ctx=CallContext(
                purpose="portfolio", request_id=rc.request_id, profile_id=profile_id, ip_hash=rc.ip_hash
            ),
            decision_ctx=DecisionContext(
                profile_id, None, self._is_sample(profile_id), rc.request_id, purpose_tag="portfolio"
            ),
            emit=emit,
        )
        saved = await self._save_generated(profile_id, stored, result)
        final = self._hydrate(saved)
        final.graphOutdated = False
        queue.put_nowait(PortfolioDoneEvent(portfolio=final))

    async def _save_generated(
        self, profile_id: str, started_from: StoredPortfolio | None, result: BuildResult
    ) -> StoredPortfolio:
        """Save a finished build over the row as it is *now* (see the module docstring).

        The row is read again here: the model ran for a while, and in that time the owner may have published the
        page, switched a privacy flag or deleted it. Its published flag, public id and choices are taken from this
        read, and the write is checked against its version (one retry when the page moved between the two).
        """
        # The owner is asked now rather than when the build started: the person may have claimed the profile since.
        owner = await self._owner_of(profile_id)
        for _ in range(2):
            current = await self._portfolios.get(profile_id)
            if current is None and started_from is not None:
                raise NotFound(DELETED_MEANWHILE)
            page = result.portfolio
            if (previous := self._try_hydrate(current)) is not None:
                page = carry_owner_choices(page, previous)
            record = self._record(
                page, result.generated, result.facts_hash, current, self._clock.now(), owner=owner
            )
            try:
                return await self._portfolios.put(
                    record, expected_version=current.version if current is not None else None
                )
            except PortfolioConflict:
                continue
        raise PortfolioVersionConflict(detail="the page kept changing while it was being rebuilt")

    # ───────────────────────── reads and edits ─────────────────────────

    async def get(self, profile_id: str) -> Portfolio | None:
        """The owner's portfolio with ``graphOutdated`` filled in, or None when there is none."""
        portfolio = self._try_hydrate(await self._portfolios.get(profile_id))
        if portfolio is None:
            return None
        return await self._with_outdated(portfolio)

    async def has_readable(self, profile_id: str) -> bool:
        """Whether a stored portfolio exists *and* can be read back: the one case where POST /api/portfolio costs nothing.

        A row whose document no longer validates is rebuilt (and paid for) like a missing page.
        """
        return self._try_hydrate(await self._portfolios.get(profile_id)) is not None

    async def delete(self, profile_id: str) -> bool:
        """Remove the owner's portfolio (and with it the public page); True when there was one."""
        removed = await self._portfolios.delete(profile_id)
        log.info("portfolio_deleted", profile_id=profile_id, removed=removed)
        return removed

    async def patch(self, profile_id: str, patch: PortfolioPatch, rc: RequestContext) -> Portfolio:
        """Apply the owner's edit (validated by `edit.apply_patch`) and save it; 409 if the page moved meanwhile."""
        stored = await self._portfolios.get(profile_id)
        portfolio = self._try_hydrate(stored)
        if stored is None or portfolio is None:
            raise NotFound(PORTFOLIO_NOT_FOUND)
        now = self._clock.now()
        loaded: tuple[Profile, StoredContext] | None = None
        if patch.refreshGraph:
            loaded = await self._load(profile_id)
            if loaded is None:
                raise BadRequest(GRAPH_GONE)
            portfolio = self._refresh_graph(portfolio, loaded[0], loaded[1], now)
        generated = self._generated(stored)
        updated = apply_patch(portfolio, patch, now, generated)
        saved = await self._save_edit(updated, generated, stored, now)
        log.info("portfolio_patched", profile_id=profile_id, request_id=rc.request_id, version=saved.version)
        return await self._with_outdated(self._hydrate(saved), loaded[0] if loaded else None)

    async def rewrite(self, profile_id: str, slot_id: str, rc: RequestContext) -> Portfolio:
        """Write one slot again from the facts (model, check, one re-ask, then the fallback wording)."""
        stored = await self._portfolios.get(profile_id)
        portfolio = self._try_hydrate(stored)
        if stored is None or portfolio is None:
            raise NotFound(PORTFOLIO_NOT_FOUND)
        self._check_rewritable(portfolio, slot_id)
        content = portfolio.content
        if not content.facts:
            raise BadRequest(NOTHING_TO_REWRITE_FROM)
        try:
            written = await self._write_slots(
                content.facts,
                content.person,
                portfolio.graph,
                [slot_id],
                CallContext(
                    purpose="portfolio", request_id=rc.request_id, profile_id=profile_id, ip_hash=rc.ip_hash
                ),
                DecisionContext(
                    profile_id, None, self._is_sample(profile_id), rc.request_id, purpose_tag="portfolio"
                ),
                degrade=False,
            )
        except ClaudeError as exc:
            raise ServiceUnavailable(exc.user_message, code="rewrite_unavailable", detail=exc.kind) from exc
        except TimeoutError as exc:
            raise ServiceUnavailable(NETWORK_MESSAGE, code="rewrite_unavailable") from exc
        new_slot = next((s for s in written.slots if s.id == slot_id), None)
        if new_slot is None:
            raise BadRequest(CANNOT_REWRITE)
        now = self._clock.now()
        updated = portfolio.model_copy(deep=True)
        updated.content.slots = _with_slot(updated.content.slots, new_slot)
        updated.updatedAt = _iso(now)
        generated = _with_slot(self._generated(stored), new_slot)
        saved = await self._save_edit(updated, generated, stored, now)
        log.info("portfolio_rewrote", profile_id=profile_id, slot=slot_id, request_id=rc.request_id)
        return await self._with_outdated(self._hydrate(saved))

    async def public_preview(self, profile_id: str) -> PublicPortfolio:
        """What a visitor would see (the owner's route for "Preview public view"); 404 when there is no page."""
        portfolio = await self.get(profile_id)
        if portfolio is None:
            raise NotFound(PORTFOLIO_NOT_FOUND)
        return to_public(portfolio)

    async def public(self, public_id: str) -> PublicPortfolio | None:
        """The published page of ``public_id`` for a visitor, or None when it is unknown or not published."""
        portfolio = self._try_hydrate(await self._portfolios.get_published(public_id))
        return to_public(portfolio) if portfolio is not None else None

    # ── helpers for the edits ──

    async def _save_edit(
        self, updated: Portfolio, generated: Sequence[CopySlot], stored: StoredPortfolio, now: datetime
    ) -> StoredPortfolio:
        record = self._record(updated, generated, stored.facts_hash, stored, now)
        try:
            return await self._portfolios.put(record, expected_version=stored.version)
        except PortfolioConflict as exc:
            raise PortfolioVersionConflict(detail=str(exc)) from exc

    @staticmethod
    def _check_rewritable(portfolio: Portfolio, slot_id: str) -> None:
        if slot_limit(slot_id) is None:
            raise BadRequest(f"'{slot_id}' is not a piece of text that can be rewritten.")
        prefix, _, fact_id = slot_id.partition(":")
        if fact_id:
            kind = FACT_SLOT_PREFIXES[prefix]
            if not any(f.id == fact_id and f.kind == kind for f in portfolio.content.facts):
                raise BadRequest(f"There is no {kind} entry '{fact_id}' to write about.")

    @staticmethod
    def _refresh_graph(
        portfolio: Portfolio, profile: Profile, ctx: StoredContext, now: datetime
    ) -> Portfolio:
        """The portfolio with the graph snapshot (and the proof stats beside it) taken again from the profile."""
        graph = graph_snapshot(profile, now.date())
        known = {d.id for d in graph.domains}
        spare = max(0, len(known) - min(MIN_GRAPH_DOMAINS, len(known)))
        hidden = [h for h in portfolio.spec.graph.hiddenDomainIds if h in known][:spare]
        out = portfolio.model_copy(deep=True)
        out.graph = graph
        out.content.stats = proof_stats(profile, ctx)
        out.spec.graph = GraphSpec(hiddenDomainIds=hidden, stats=portfolio.spec.graph.stats)
        return out


def _with_slot(slots: Sequence[CopySlot], new: CopySlot) -> list[CopySlot]:
    """``slots`` with ``new`` in place of the slot of the same id (appended when there is none)."""
    out = [s.model_copy(deep=True) for s in slots]
    for i, s in enumerate(out):
        if s.id == new.id:
            out[i] = new.model_copy(deep=True)
            return out
    out.append(new.model_copy(deep=True))
    return out


def _counts(report: BuildReport) -> dict[str, Any]:
    return {
        "facts_kept": report.facts_kept,
        "facts_dropped": report.facts_dropped,
        "facts_reused": report.facts_reused,
        "facts_partial": report.facts_partial,
        "facts_failed": report.facts_failed,
        "slots_model": report.slots_model,
        "slots_resume": report.slots_resume,
        "slots_missing": report.slots_missing,
        "reasked": report.reasked,
        "fallbacks": report.fallbacks,
        "copy_failed": report.copy_failed,
        "ungrounded": report.ungrounded,
        "seconds": report.seconds,
    }


__all__ = [
    "CANNOT_REWRITE",
    "GENERIC_MESSAGE",
    "PAUSED_MESSAGE",
    "PORTFOLIO_NOT_FOUND",
    "PROFILE_NOT_FOUND",
    "STEP_LABELS",
    "BuildReport",
    "BuildResult",
    "PortfolioService",
    "PortfolioVersionConflict",
    "event_message",
    "graph_is_outdated",
]
