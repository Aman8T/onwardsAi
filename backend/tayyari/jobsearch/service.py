"""One search with AI, streamed, stored, and what is built on it afterwards (the posting text, a prep page, a referral note).

A run has two halves that run at once, so the first roles show in a few seconds:

    plan      `brief.build_brief` (code)
    near      Tayyari's own employers, read from Discover's store: `narrow.select` (place, kind), their texts, then
              `narrow.narrow` (level, quick fit), at most ``PHASE_A_READS`` read by Haiku, while the scouts search
    search    one Claude Haiku scout per angle, at once, each with only the web search tool, restricted to employers'
              Greenhouse and Lever sites (`harvest.ALLOWED_DOMAINS`), told the place. A step per scout as it finishes,
              with its queries. A scout that fails is reported and the others go on; when every scout fails and the near
              half found nothing to show, the first error ends the search (a spend ceiling is an error like any other)
    boards    the boards the results led to (`harvest.harvest`) and that Discover does not already read, at most
              ``max_boards``, read by `boards.BoardReader`
    narrow    the web pool the same way, without the roles already read, at most ``max_judged`` to the read
    read      batches of ``judge_batch`` postings, one Haiku call each, at once; each batch's postings are checked by
              `judge.verify` and its jobs sent as soon as it is back, best first
    result    counts of what was shown and why the rest was not; the run is stored; ``done`` names it

The person's choices shape the run: ``remote=False`` leaves remote roles out (and the remote scout), and `AvoidPrefs`
("Not for me") keeps hidden roles and waved-off employers out of every pool and, with ``noStepUp``, roles a step up.

Every sentence of the trail is written here, from counts. Nothing a model writes is shown except quotes that code found
word for word in a posting, and evidence lines that are the profile's own. The route reserves the run's spend; every
Claude call goes through the guarded client with its own purpose.

After a run: `latest` gives the last run for a profile, city and remote choice (the page shows it until the next search);
`posting` gives a shown role's text for "See how you fit"; `prepare` and `referral` write (and keep) a role's prep page
and referral note, reading the posting the search read.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any, Final

from pydantic import ValidationError

from tayyari.contract.discover import DiscoverWorkMode, Place, Salary
from tayyari.contract.jobsearch import (
    AiFitWord,
    AiJob,
    AskCounts,
    AvoidPrefs,
    FunnelStep,
    JobPosting,
    JobSearchDoneEvent,
    JobSearchEvent,
    JobSearchJobEvent,
    JobSearchRun,
    JobSearchStepEvent,
    ReferralNote,
    RolePrep,
    TrailStage,
    TrailStep,
)
from tayyari.contract.models import Profile
from tayyari.core.clock import Clock
from tayyari.core.logging import get_logger
from tayyari.core.settings import JobSearchSettings
from tayyari.discover.person import PersonIndex
from tayyari.discover.places import City
from tayyari.discover.registry import Registry
from tayyari.discover.vocab import Vocabulary, vocabulary
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import ClaudeError
from tayyari.llm.types import CallContext, Purpose, SearchCall, TextBlock, Usage, WebSearchRun
from tayyari.prompts.jobsearch import (
    JUDGE_SYSTEM,
    PostingForJudge,
    build_judge_prompt,
    build_scout_prompt,
    scout_system,
)
from tayyari.storage.ports import (
    DiscoverListing,
    DiscoverRepo,
    JobSearchRepo,
    StoredSearchDoc,
    StoredSearchRun,
)

from .boards import BoardRead, BoardReader
from .brief import Angle, Brief, build_brief
from .errors import JobSearchDisabled
from .harvest import ALLOWED_DOMAINS, harvest
from .judge import JUDGE_SCHEMA, Verdict, order_key, verify
from .narrow import (
    SENIOR_STEP_YEARS,
    Candidate,
    Narrowed,
    Posting,
    Selection,
    Targets,
    narrow,
    select,
    senior_title,
)
from .prepare import prepare_role
from .referral import write_referral

log = get_logger(__name__)

SCOUT_MAX_TOKENS: Final = 1200
JUDGE_MAX_TOKENS: Final = 6000
PHASE_A_READS: Final = 6
"""Roles from Tayyari's own employers read while the web search runs (the first cards)."""
OPEN_LONG: Final = timedelta(days=365)
"""A posting first published longer ago than this is "open over a year"."""
POSTING_KEEP_CHARS: Final = 20_000
_MODES: Final[dict[str, DiscoverWorkMode]] = {"onsite": "onsite", "hybrid": "hybrid", "remote": "remote"}
_REASONS: Final[dict[str, str]] = {
    "different": "another kind of role on reading",
    "far_above": "asked for far more experience",
    "nothing_met": "named nothing your evidence shows",
    "instructions": "carried text aimed at an AI, so it was left out",
    "missing": "could not be read",
    "too_senior": "a step up, which you asked to skip",
}


@dataclass(frozen=True, slots=True)
class _Scouted:
    angle: Angle
    run: WebSearchRun | None
    error: BaseException | None = None


@dataclass(slots=True)
class _Run:
    """What one run gathers as it goes (the halves write here; the stream reads the queue)."""

    queue: asyncio.Queue[JobSearchEvent | BaseException | None]
    trail: list[TrailStep] = field(default_factory=list)
    jobs: list[AiJob] = field(default_factory=list)
    postings: dict[str, dict[str, Any]] = field(default_factory=dict)
    read_ids: set[str] = field(default_factory=set)
    shown: dict[str, int] = field(default_factory=lambda: {"strong": 0, "good": 0, "stretch": 0})
    dropped: dict[str, int] = field(default_factory=dict)
    quotes_dropped: int = 0
    spent: list[Usage] = field(default_factory=list)
    totals: list[int] = field(default_factory=lambda: [0, 0, 0, 0, 0])
    """Pool, in place, your kind, within reach, read: summed over both halves."""

    def emit(self, event: JobSearchEvent) -> None:
        if isinstance(event, JobSearchStepEvent):
            self.trail.append(event.step)
        self.queue.put_nowait(event)

    def count(self, selection: Selection, narrowed: Narrowed) -> None:
        for index, value in enumerate(
            (
                selection.total,
                len(selection.placed),
                len(selection.kinds),
                narrowed.level_count,
                len(narrowed.shortlist),
            )
        ):
            self.totals[index] += value


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _years(years: float | None) -> str:
    if years is None:
        return ""
    whole = round(years)
    return (
        f", at about {whole} year{'s' if whole != 1 else ''} of experience"
        if whole >= 1
        else ", early in a career"
    )


def _place(listing: DiscoverListing) -> Place:
    if listing.remote_india:
        return Place(label="Remote, India", precision="country")
    if listing.city and listing.locality:
        return Place(
            label=f"{listing.locality}, {listing.city}",
            city=listing.city,
            locality=listing.locality,
            precision="locality",
        )
    if listing.city:
        return Place(label=listing.city, city=listing.city, precision="city")
    return Place(label=listing.location_text[:60] or "India", precision="country")


def _with_purpose(ctx: CallContext, purpose: Purpose) -> CallContext:
    return replace(ctx, purpose=purpose)


def _avoided(avoid: AvoidPrefs | None) -> Callable[[Posting], bool]:
    if avoid is None:
        return lambda _posting: False
    ids = set(avoid.jobIds)
    employers = {name.strip().casefold() for name in avoid.employers if name.strip()}
    return lambda posting: posting.listing.id in ids or posting.employer.strip().casefold() in employers


class JobSearchService:
    def __init__(
        self,
        *,
        claude: ClaudeClient,
        settings: JobSearchSettings,
        repo: DiscoverRepo,
        runs: JobSearchRepo,
        registry: Callable[[], Registry],
        reader: BoardReader,
        clock: Clock,
        discover_enabled: Callable[[], bool] = lambda: True,
        vocab: Vocabulary | None = None,
    ) -> None:
        self._claude = claude
        self._settings = settings
        self._repo = repo
        self._runs = runs
        self._registry = registry
        self._reader = reader
        self._clock = clock
        self._discover_enabled = discover_enabled
        self._vocab = vocab if vocab is not None else vocabulary()

    @property
    def enabled(self) -> bool:
        return self._settings.enabled and self._discover_enabled()

    def require_enabled(self) -> None:
        if not self.enabled:
            raise JobSearchDisabled()

    # ───────────────────────── the run ─────────────────────────

    async def run(
        self,
        profile: Profile,
        city: City | None,
        ctx: CallContext,
        *,
        remote: bool = True,
        avoid: AvoidPrefs | None = None,
    ) -> AsyncIterator[JobSearchEvent]:
        """The search's events (see the module docstring). Closing the iteration cancels every call in flight."""
        state = _Run(queue=asyncio.Queue())
        producer = asyncio.create_task(
            self._produce(state, profile, city, ctx, remote, avoid), name="jobsearch-run"
        )
        try:
            while True:
                item = await state.queue.get()
                if item is None:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    async def _produce(
        self,
        state: _Run,
        profile: Profile,
        city: City | None,
        ctx: CallContext,
        remote: bool,
        avoid: AvoidPrefs | None,
    ) -> None:
        try:
            await self._search(state, profile, city, ctx, remote, avoid)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # handed to the stream, which raises it where the route can answer
            state.queue.put_nowait(exc)
        finally:
            state.queue.put_nowait(None)

    async def _search(
        self,
        state: _Run,
        profile: Profile,
        city: City | None,
        ctx: CallContext,
        remote: bool,
        avoid: AvoidPrefs | None,
    ) -> None:
        s = self._settings
        brief = build_brief(profile, city, scouts=s.scouts)
        angles = tuple(angle for angle in brief.angles if remote or not angle.remote)
        brief = replace(brief, angles=angles)
        person = PersonIndex(profile, self._vocab)
        targets = Targets(brief, person, self._vocab)
        skip = _avoided(avoid)
        no_step_up = bool(avoid and avoid.noStepUp)
        if not brief.angles:
            nothing = "Your profile names no role yet, so there is nothing to search for. Add your current role first."
            await self._finish(state, profile, city, remote, (), nothing)
            return
        state.emit(self._step("plan", self._plan_text(brief, s.searches_per_scout, remote)))

        scouts = asyncio.create_task(self._scout_all(state, brief, ctx), name="jobsearch-scouts")
        try:
            stored = await self._repo.list_open()
            near = [Posting(listing, self._employer_name(listing), "tayyari") for listing in stored]
            near = [p for p in near if not skip(p)]
            await self._read_pool(
                state, brief, person, targets, near, PHASE_A_READS, remote, no_step_up, ctx, "near"
            )
            searches, first_error = await scouts
        finally:
            if not scouts.done():
                scouts.cancel()
                await asyncio.gather(scouts, return_exceptions=True)

        if not searches and first_error is not None and not state.jobs:
            raise first_error
        registry = self._registry()
        leads = [
            lead
            for lead in harvest(searches, targets.strength)
            if self._reader.employer_for(lead, registry) is None
        ]
        reads = await self._reader.read(leads[: s.max_boards]) if leads else []
        web = [
            Posting(listing, read.employer.name, "web", read.lead.first_query, listing.posting_text)
            for read in reads
            for listing in read.listings
        ]
        web = [p for p in dict((p.listing.id, p) for p in web).values() if p.listing.id not in state.read_ids]
        web = [p for p in web if not skip(p)]
        state.emit(self._step("boards", self._boards_text(reads, len(stored)), count=len(web)))
        await self._read_pool(
            state, brief, person, targets, web, s.max_judged, remote, no_step_up, ctx, "web"
        )

        funnel = self._funnel(state, brief, remote)
        total = sum(state.shown.values())
        state.emit(self._step("result", self._result_text(state), count=total))
        note: str | None = (
            None
            if total
            else "Nothing that was read fits well enough to show. The funnel shows where roles dropped out."
        )
        await self._finish(state, profile, city, remote, funnel, note)

    async def _scout_all(
        self, state: _Run, brief: Brief, ctx: CallContext
    ) -> tuple[list[SearchCall], BaseException | None]:
        searches: list[SearchCall] = []
        first_error: BaseException | None = None
        async for scouted in self._scouts(brief, ctx):
            if scouted.run is None:
                first_error = first_error or scouted.error
                state.emit(self._step("search", f"The search for {scouted.angle.label} did not finish."))
                continue
            state.spent.append(scouted.run.usage)
            calls = [call for call in scouted.run.searches if call.query]
            searches.extend(calls)
            boards = len(harvest(calls))
            state.emit(
                self._step(
                    "search",
                    f"Searched for {scouted.angle.label}: {_plural(boards, 'employer board')} in the results.",
                    count=boards,
                    queries=[call.query for call in calls],
                )
            )
        return searches, first_error

    async def _read_pool(
        self,
        state: _Run,
        brief: Brief,
        person: PersonIndex,
        targets: Targets,
        pool: Sequence[Posting],
        limit: int,
        remote: bool,
        no_step_up: bool,
        ctx: CallContext,
        half: str,
    ) -> None:
        """Narrow one pool and read its shortlist, sending each checked role as its batch comes back."""
        s = self._settings
        selection = select(brief, targets, pool)
        if not remote:
            placed = tuple(p for p in selection.placed if not p.listing.remote_india)
            kinds = tuple((p, k) for p, k in selection.kinds if not p.listing.remote_india)
            selection = Selection(total=selection.total, placed=placed, kinds=kinds)
        if no_step_up and brief.years is not None and brief.years < SENIOR_STEP_YEARS:
            kinds = tuple((p, k) for p, k in selection.kinds if not senior_title(p.listing.title))
            selection = Selection(total=selection.total, placed=selection.placed, kinds=kinds)
        missing = [p.listing.id for p, _ in selection.kinds if p.text is None]
        texts = (
            {key: text for key, (_hash, text) in (await self._repo.texts(missing)).items()} if missing else {}
        )
        narrowed = narrow(
            brief, person, self._vocab, selection, texts, max_judged=limit, per_employer=s.per_employer
        )
        state.count(selection, narrowed)
        shortlist = narrowed.shortlist
        if half == "near":
            if shortlist:
                state.emit(
                    self._step(
                        "read",
                        f"Reading the {_plural(len(shortlist), 'closest role')} from Tayyari's own employers "
                        "while the web search runs.",
                        count=len(shortlist),
                    )
                )
        else:
            state.emit(self._step("narrow", self._narrow_text(brief, remote, narrowed), count=len(shortlist)))
            if shortlist:
                state.emit(
                    self._step(
                        "read",
                        f"Claude Haiku is reading the {_plural(len(shortlist), 'closest posting')} from the web "
                        "against your evidence.",
                        count=len(shortlist),
                    )
                )
        state.read_ids.update(c.posting.listing.id for c in shortlist)
        async for batch, verdicts, usage in self._reads(brief, shortlist, ctx):
            if usage is not None:
                state.spent.append(usage)
            ranked = sorted(zip(batch, verdicts, strict=True), key=lambda pair: order_key(pair[1]))
            for candidate, verdict in ranked:
                state.quotes_dropped += verdict.quotes_dropped
                if verdict.fit is not None and no_step_up and verdict.level == "step_up":
                    verdict.fit, verdict.dropped = None, "too_senior"
                if verdict.fit is None:
                    reason = verdict.dropped or "missing"
                    state.dropped[reason] = state.dropped.get(reason, 0) + 1
                    log.info(
                        "jobsearch_left_out",
                        listing=candidate.posting.listing.id,
                        title=candidate.posting.listing.title[:80],
                        reason=reason,
                    )
                    continue
                job = self._job(candidate, verdict, verdict.fit)
                state.shown[verdict.fit] += 1
                state.jobs.append(job)
                state.postings[job.id] = {
                    "title": job.title,
                    "employer": job.employer,
                    "applyUrl": job.applyUrl,
                    "text": candidate.text[:POSTING_KEEP_CHARS],
                }
                state.emit(JobSearchJobEvent(job=job))

    async def _finish(
        self,
        state: _Run,
        profile: Profile,
        city: City | None,
        remote: bool,
        funnel: Sequence[FunnelStep],
        note: str | None,
    ) -> None:
        now = self._clock.now()
        run_id = secrets.token_hex(16)
        result = JobSearchRun(
            runId=run_id,
            createdAt=now.isoformat(),
            city=city.name if city else None,
            remote=remote,
            jobs=state.jobs,
            funnel=list(funnel),
            trail=state.trail,
            note=note,
        )
        stored = StoredSearchRun(
            id=run_id,
            profile_id=profile.id,
            city=city.name if city else None,
            remote=remote,
            created_at=now,
            result=result.to_dict(),
            postings=state.postings,
        )
        try:
            await self._runs.save_run(stored)
        except Exception:  # the person still sees the roles; only "show the last results" loses this run
            log.error("jobsearch_run_not_saved", exc_info=True)
        self._log(state.spent, len(state.jobs), funnel)
        state.emit(
            JobSearchDoneEvent(
                jobs=len(state.jobs), funnel=list(funnel), note=note, runId=run_id, createdAt=now.isoformat()
            )
        )

    # ───────────────────────── stages ─────────────────────────

    async def _scouts(self, brief: Brief, ctx: CallContext) -> AsyncIterator[_Scouted]:
        """Every scout at once; each result as it finishes."""
        s = self._settings

        async def one(angle: Angle) -> _Scouted:
            location = {"country": "IN", "timezone": "Asia/Kolkata"}
            if not angle.remote and brief.city is not None:
                location = {"city": brief.city.name, **location}
            try:
                run = await self._claude.search_web(
                    model=s.model,
                    system=[TextBlock(scout_system(s.searches_per_scout))],
                    user=[TextBlock(build_scout_prompt(brief.scout_brief(angle)))],
                    max_searches=s.searches_per_scout,
                    allowed_domains=ALLOWED_DOMAINS,
                    user_location=location,
                    max_tokens=SCOUT_MAX_TOKENS,
                    effort="low",
                    ctx=_with_purpose(ctx, "jobsearch.scout"),
                )
            except ClaudeError as exc:
                log.warning("jobsearch_scout_failed", angle=angle.id, kind=exc.kind)
                return _Scouted(angle, None, exc)
            return _Scouted(angle, run)

        tasks = [
            asyncio.create_task(one(angle), name=f"jobsearch-scout-{angle.id}") for angle in brief.angles
        ]
        try:
            for done in asyncio.as_completed(tasks):
                yield await done
        finally:
            for task in tasks:
                task.cancel()

    async def _reads(
        self, brief: Brief, shortlist: Sequence[Candidate], ctx: CallContext
    ) -> AsyncIterator[tuple[list[Candidate], list[Verdict], Usage | None]]:
        """The shortlist read in batches at once; each batch, checked, as it comes back."""
        size = self._settings.judge_batch
        batches = [list(shortlist[i : i + size]) for i in range(0, len(shortlist), size)]
        evidence = brief.evidence_by_id()

        async def one(batch: list[Candidate]) -> tuple[list[Candidate], list[Verdict], Usage | None]:
            ids = {f"P{i + 1}": c for i, c in enumerate(batch)}
            prompt = build_judge_prompt(
                role=brief.role,
                years=brief.years,
                next_roles=brief.next_roles,
                evidence=[e.line() for e in brief.evidence],
                postings=[
                    PostingForJudge(
                        id=key,
                        title=c.posting.listing.title,
                        employer=c.posting.employer,
                        location=c.posting.listing.location_text or _place(c.posting.listing).label,
                        text=c.text[: self._settings.posting_chars],
                    )
                    for key, c in ids.items()
                ],
            )
            try:
                result = await self._claude.structured(
                    model=self._settings.model,
                    system=[TextBlock(JUDGE_SYSTEM)],
                    user=[TextBlock(prompt)],
                    schema=JUDGE_SCHEMA,
                    max_tokens=JUDGE_MAX_TOKENS,
                    effort="low",
                    ctx=_with_purpose(ctx, "jobsearch.judge"),
                )
            except ClaudeError as exc:
                log.warning("jobsearch_judge_failed", kind=exc.kind, postings=len(batch))
                return batch, [Verdict(dropped="missing") for _ in batch], None
            items: dict[str, Any] = {}
            raw = result.data.get("postings")
            for item in raw if isinstance(raw, list) else []:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    items.setdefault(item["id"], item)
            verdicts = [
                verify(
                    items.get(key),
                    text=c.text[: self._settings.posting_chars],
                    evidence=evidence,
                    asked=c.asked,
                    years=brief.years,
                    title=c.posting.listing.title,
                )
                for key, c in ids.items()
            ]
            return batch, verdicts, result.usage

        tasks = [asyncio.create_task(one(batch), name="jobsearch-read") for batch in batches]
        try:
            for done in asyncio.as_completed(tasks):
                yield await done
        finally:
            for task in tasks:
                task.cancel()

    def _employer_name(self, listing: DiscoverListing) -> str:
        employer = self._registry().get(listing.employer_id)
        return employer.name if employer is not None else listing.employer_id.replace("-", " ").title()

    # ───────────────────────── after a run ─────────────────────────

    async def latest(self, profile_id: str, city: City | None, remote: bool) -> JobSearchRun | None:
        """The last finished search for this profile, city and remote choice, or None."""
        stored = await self._runs.latest_run(profile_id, city.name if city else None, remote)
        return None if stored is None else self._run_of(stored)

    async def _owned_run(self, profile_id: str, run_id: str) -> StoredSearchRun | None:
        stored = await self._runs.get_run(run_id)
        return stored if stored is not None and stored.profile_id == profile_id else None

    async def posting(self, profile_id: str, run_id: str, job_id: str) -> JobPosting | None:
        """A shown role's posting as the search read it, or None (another profile's run, or no such role)."""
        stored = await self._owned_run(profile_id, run_id)
        held = stored.postings.get(job_id) if stored is not None else None
        if not isinstance(held, dict) or not isinstance(held.get("text"), str):
            return None
        return JobPosting(
            jobId=job_id,
            title=str(held.get("title", "")),
            employer=str(held.get("employer", "")),
            applyUrl=str(held.get("applyUrl", "")),
            postingText=held["text"],
        )

    async def prepare(
        self, profile: Profile, run_id: str, job_id: str, ctx: CallContext, *, fresh: bool = False
    ) -> RolePrep | None:
        """The prep page for one shown role (kept once written), or None when the run or role is not this profile's."""
        found = await self._role(profile.id, run_id, job_id)
        if found is None:
            return None
        job, text = found
        if not fresh:
            held = await self._runs.get_doc(profile.id, job_id, "prep")
            if held is not None:
                try:
                    return RolePrep.model_validate(held.body)
                except ValidationError:
                    log.warning("jobsearch_prep_unreadable", job=job_id)
        prep = await prepare_role(
            claude=self._claude,
            model=self._settings.model,
            profile=profile,
            job=job,
            posting_text=text,
            ctx=_with_purpose(ctx, "jobsearch.prepare"),
            now=self._clock.now(),
        )
        await self._keep(profile.id, job_id, "prep", prep.to_dict())
        return prep

    async def referral(
        self, profile: Profile, run_id: str, job_id: str, ctx: CallContext, *, fresh: bool = False
    ) -> ReferralNote | None:
        """A referral note for one shown role (kept once written; ``fresh`` writes a new one), or None."""
        found = await self._role(profile.id, run_id, job_id)
        if found is None:
            return None
        job, text = found
        if not fresh:
            held = await self._runs.get_doc(profile.id, job_id, "referral")
            if held is not None:
                try:
                    return ReferralNote.model_validate(held.body)
                except ValidationError:
                    log.warning("jobsearch_referral_unreadable", job=job_id)
        note = await write_referral(
            claude=self._claude,
            model=self._settings.model,
            profile=profile,
            job=job,
            posting_text=text,
            ctx=_with_purpose(ctx, "jobsearch.referral"),
            now=self._clock.now(),
        )
        await self._keep(profile.id, job_id, "referral", note.to_dict())
        return note

    async def _role(self, profile_id: str, run_id: str, job_id: str) -> tuple[AiJob, str] | None:
        stored = await self._owned_run(profile_id, run_id)
        if stored is None:
            return None
        run = self._run_of(stored)
        job = next((j for j in run.jobs if j.id == job_id), None) if run is not None else None
        held = stored.postings.get(job_id)
        if job is None or not isinstance(held, dict) or not isinstance(held.get("text"), str):
            return None
        return job, held["text"]

    async def _keep(self, profile_id: str, job_id: str, kind: str, body: dict[str, Any]) -> None:
        try:
            await self._runs.save_doc(
                StoredSearchDoc(
                    profile_id=profile_id, job_id=job_id, kind=kind, created_at=self._clock.now(), body=body
                )
            )
        except Exception:  # shown now, written again next time
            log.error("jobsearch_doc_not_saved", kind=kind, exc_info=True)

    @staticmethod
    def _run_of(stored: StoredSearchRun) -> JobSearchRun | None:
        try:
            return JobSearchRun.model_validate(
                {**stored.result, "runId": stored.id, "createdAt": stored.created_at.isoformat()}
            )
        except ValidationError:
            log.warning("jobsearch_run_unreadable", run=stored.id)
            return None

    # ───────────────────────── output ─────────────────────────

    def _job(self, candidate: Candidate, verdict: Verdict, fit: AiFitWord) -> AiJob:
        posting = candidate.posting
        listing = posting.listing
        now = self._clock.now()
        checked = now if posting.via == "web" and posting.text is not None else listing.last_seen_at
        open_long = listing.posted_at is not None and now - listing.posted_at > OPEN_LONG
        return AiJob(
            id=listing.id,
            title=listing.title,
            employer=posting.employer,
            place=_place(listing),
            workMode=_MODES.get(listing.work_mode, "unknown"),
            postedAt=listing.posted_at.isoformat() if listing.posted_at else None,
            checkedAt=checked.isoformat(),
            applyUrl=listing.apply_url,
            applyHost=listing.apply_host,
            foundVia=posting.via,
            foundBy=posting.found_by,
            fit=fit,
            level=verdict.level,
            levelQuote=verdict.level_quote,
            yearsAsked=listing.years_min,
            met=verdict.met,
            gaps=verdict.gaps,
            asks=AskCounts(met=verdict.met_count, partly=verdict.partly_count, notShown=verdict.gap_count),
            openLong=True if open_long else None,
            quickFit=candidate.fit.to_model(),
            salary=Salary(text=listing.salary_text) if listing.salary_text else None,
        )

    @staticmethod
    def _step(
        stage: TrailStage, text: str, *, count: int | None = None, queries: list[str] | None = None
    ) -> JobSearchStepEvent:
        return JobSearchStepEvent(
            step=TrailStep(stage=stage, text=text, count=count, queries=queries or None)
        )

    @staticmethod
    def _plan_text(brief: Brief, searches: int, remote: bool) -> str:
        roles = " and ".join(dict.fromkeys(title for angle in brief.angles for title in angle.titles))
        if brief.city:
            where = f"in {brief.city.name} and remote in India" if remote else f"in {brief.city.name}"
        else:
            where = "remote in India"
        scouts = _plural(len(brief.angles), "search", "searches")
        return (
            f"Looking for {roles} roles {where}{_years(brief.years)}: {scouts} on employers' own job sites, "
            f"up to {searches} queries each, while Tayyari's own employers are read first."
        )

    @staticmethod
    def _boards_text(reads: Sequence[BoardRead], stored: int) -> str:
        ok = [r for r in reads if r.error is None]
        failed = len(reads) - len(ok)
        found = sum(len(r.listings) for r in ok)
        text = (
            f"Read {_plural(len(ok), 'employer board')} found on the web: {_plural(found, 'open role')} in India."
            if reads
            else "The searches led to no board beyond Tayyari's own employers."
        )
        if failed:
            text += f" {_plural(failed, 'board')} could not be read."
        if stored:
            text += f" Tayyari's own employers: {_plural(stored, 'open role')}."
        return text

    @staticmethod
    def _narrow_text(brief: Brief, remote: bool, narrowed: Narrowed) -> str:
        if brief.city:
            where = f"in {brief.city.name} or remote in India" if remote else f"in {brief.city.name}"
        else:
            where = "remote in India"
        return (
            f"From the web: {_plural(narrowed.place_count, 'role')} {where}, {narrowed.kind_count:,} of your kind, "
            f"{narrowed.level_count:,} within reach of your experience."
        )

    @staticmethod
    def _funnel(state: _Run, brief: Brief, remote: bool) -> tuple[FunnelStep, ...]:
        if brief.city:
            where = f"In {brief.city.name} or remote in India" if remote else f"In {brief.city.name}"
        else:
            where = "Remote in India"
        pool, placed, kind, reach, read = state.totals
        return (
            FunnelStep(label="Open roles in India on the boards read", count=pool),
            FunnelStep(label=where, count=placed),
            FunnelStep(label="Your kind of role", count=kind),
            FunnelStep(label="Within reach of your experience", count=reach),
            FunnelStep(label="Read against your evidence", count=read),
            FunnelStep(label="Fit, after the checks", count=len(state.jobs)),
        )

    @staticmethod
    def _result_text(state: _Run) -> str:
        shown = state.shown
        total = sum(shown.values())
        parts = [f"{shown['strong']:,} strong", f"{shown['good']:,} good", f"{shown['stretch']:,} a stretch"]
        text = f"{_plural(total, 'role fits', 'roles fit')} after the checks: {', '.join(parts)}."
        left = [f"{n:,} {_REASONS[key]}" for key, n in state.dropped.items() if n and key in _REASONS]
        if left:
            text += " Left out: " + "; ".join(left) + "."
        if state.quotes_dropped:
            text += (
                f" {_plural(state.quotes_dropped, 'quote')} that could not be found word for word in a posting "
                "were dropped."
            )
        return text

    @staticmethod
    def _log(spent: Sequence[Usage], shown: int, funnel: Sequence[FunnelStep]) -> None:
        log.info(
            "jobsearch_done",
            shown=shown,
            funnel={step.label: step.count for step in funnel},
            searches=sum(u.web_searches for u in spent),
            cost_micro_usd=sum(u.cost_micro_usd for u in spent),
        )


__all__ = ["JobSearchService"]
