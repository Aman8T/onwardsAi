"""One search with AI, streamed: trail steps as it moves, a job for each role that survives the checks, then done.

    plan     `brief.build_brief` (code)
    search   one Claude Haiku scout per angle, at once, each with only the web search tool, restricted to employers'
             Greenhouse and Lever sites (`harvest.ALLOWED_DOMAINS`), told the place in ``user_location``. A step per scout
             as it finishes, with its queries. A scout that fails is reported and the others go on; when all fail the
             first error ends the search (a spend ceiling is an error like any other).
    boards   the boards the results led to (`harvest.harvest`), at most ``max_boards``, read by `boards.BoardReader`;
             boards Discover already reads are taken from its store instead, together with every other open listing of
             Tayyari's own employers
    narrow   `narrow.select` (place, kind), then the stored listings' texts, then `narrow.narrow` (level, quick fit,
             shortlist)
    read     the shortlist in batches of ``judge_batch``, one Haiku call each, at once; each batch's postings are
             checked by `judge.verify` and its jobs sent as soon as it is back, best first
    result   counts of what was shown and why the rest was not, then ``done`` with the funnel

Every sentence of the trail is written here, from counts. Nothing a model writes is shown except quotes that code found
word for word in a posting, and evidence lines that are the profile's own. The spend for a run is reserved by the route;
every Claude call goes through the guarded client with its own purpose.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final

from tayyari.contract.discover import DiscoverWorkMode, Place, Salary
from tayyari.contract.jobsearch import (
    AiFitWord,
    AiJob,
    FunnelStep,
    JobSearchDoneEvent,
    JobSearchEvent,
    JobSearchJobEvent,
    JobSearchStepEvent,
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
from tayyari.storage.ports import DiscoverListing, DiscoverRepo

from .boards import BoardRead, BoardReader
from .brief import Angle, Brief, build_brief
from .errors import JobSearchDisabled
from .harvest import ALLOWED_DOMAINS, BoardLead, harvest
from .judge import JUDGE_SCHEMA, Verdict, order_key, verify
from .narrow import Candidate, Posting, Targets, narrow, select

log = get_logger(__name__)

SCOUT_MAX_TOKENS: Final = 1200
JUDGE_MAX_TOKENS: Final = 6000
_MODES: Final[dict[str, DiscoverWorkMode]] = {"onsite": "onsite", "hybrid": "hybrid", "remote": "remote"}


@dataclass(frozen=True, slots=True)
class _Scouted:
    angle: Angle
    run: WebSearchRun | None
    error: BaseException | None = None


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


class JobSearchService:
    def __init__(
        self,
        *,
        claude: ClaudeClient,
        settings: JobSearchSettings,
        repo: DiscoverRepo,
        registry: Callable[[], Registry],
        reader: BoardReader,
        clock: Clock,
        discover_enabled: Callable[[], bool] = lambda: True,
        vocab: Vocabulary | None = None,
    ) -> None:
        self._claude = claude
        self._settings = settings
        self._repo = repo
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
        self, profile: Profile, city: City | None, ctx: CallContext
    ) -> AsyncIterator[JobSearchEvent]:
        """The search's events (see the module docstring). Cancelling the iteration cancels every call in flight."""
        s = self._settings
        brief = build_brief(profile, city, scouts=s.scouts)
        person = PersonIndex(profile, self._vocab)
        targets = Targets(brief, person, self._vocab)
        spent: list[Usage] = []
        if not brief.angles:
            yield self._done(
                (),
                0,
                "Your profile names no role yet, so there is nothing to search for. Add your current role first.",
            )
            return
        yield self._step("plan", self._plan_text(brief, s.searches_per_scout))

        searches: list[SearchCall] = []
        first_error: BaseException | None = None
        async for scouted in self._scouts(brief, ctx):
            if scouted.run is None:
                first_error = first_error or scouted.error
                yield self._step("search", f"The search for {scouted.angle.label} did not finish.")
                continue
            spent.append(scouted.run.usage)
            calls = [call for call in scouted.run.searches if call.query]
            searches.extend(calls)
            boards = len(harvest(calls))
            yield self._step(
                "search",
                f"Searched for {scouted.angle.label}: {_plural(boards, 'employer board')} in the results.",
                count=boards,
                queries=[call.query for call in calls],
            )
        if not searches and first_error is not None:
            raise first_error

        registry = self._registry()
        leads = harvest(searches, targets.strength)
        known = [lead for lead in leads if self._reader.employer_for(lead, registry) is not None]
        fresh = [lead for lead in leads if self._reader.employer_for(lead, registry) is None][: s.max_boards]
        reads = await self._reader.read(fresh)
        stored = await self._repo.list_open()
        postings = self._postings(reads, stored, registry, known)
        yield self._step("boards", self._boards_text(reads, stored, registry), count=len(postings))

        selection = select(brief, targets, postings)
        missing = [p.listing.id for p, _ in selection.kinds if p.text is None]
        texts = (
            {key: text for key, (_hash, text) in (await self._repo.texts(missing)).items()} if missing else {}
        )
        narrowed = narrow(
            brief,
            person,
            self._vocab,
            selection,
            texts,
            max_judged=s.max_judged,
            per_employer=s.per_employer,
        )
        yield self._step("narrow", self._narrow_text(brief, narrowed.funnel), count=len(narrowed.shortlist))
        if not narrowed.shortlist:
            self._log(spent, 0, narrowed.funnel)
            yield self._done(
                narrowed.funnel, 0, "Nothing on the boards we read is near enough to your role and level."
            )
            return

        yield self._step(
            "read",
            f"Claude Haiku is reading the {_plural(len(narrowed.shortlist), 'closest posting')} against your evidence.",
            count=len(narrowed.shortlist),
        )
        shown: dict[str, int] = {"strong": 0, "good": 0, "stretch": 0}
        dropped: dict[str, int] = {}
        quotes_dropped = 0
        async for batch, verdicts, usage in self._reads(brief, narrowed.shortlist, ctx):
            if usage is not None:
                spent.append(usage)
            ranked = sorted(zip(batch, verdicts, strict=True), key=lambda pair: order_key(pair[1]))
            for candidate, verdict in ranked:
                quotes_dropped += verdict.quotes_dropped
                if verdict.fit is None:
                    reason = verdict.dropped or "missing"
                    dropped[reason] = dropped.get(reason, 0) + 1
                    log.info(
                        "jobsearch_left_out",
                        listing=candidate.posting.listing.id,
                        title=candidate.posting.listing.title[:80],
                        reason=reason,
                    )
                    continue
                shown[verdict.fit] += 1
                yield JobSearchJobEvent(job=self._job(candidate, verdict, verdict.fit))

        total = sum(shown.values())
        funnel = (*narrowed.funnel, FunnelStep(label="Fit, after the checks", count=total))
        yield self._step("result", self._result_text(shown, dropped, quotes_dropped), count=total)
        self._log(spent, total, funnel)
        note = (
            None
            if total
            else "Nothing that was read fits well enough to show. The funnel shows where roles dropped out."
        )
        yield self._done(funnel, total, note)

    # ───────────────────────── stages ─────────────────────────

    async def _scouts(self, brief: Brief, ctx: CallContext) -> AsyncIterator[_Scouted]:
        """Every scout at once; each result as it finishes."""
        s = self._settings

        async def one(angle: Angle) -> _Scouted:
            location = None
            if not angle.remote and brief.city is not None:
                location = {"city": brief.city.name, "country": "IN", "timezone": "Asia/Kolkata"}
            try:
                run = await self._claude.search_web(
                    model=s.model,
                    system=[TextBlock(scout_system(s.searches_per_scout))],
                    user=[TextBlock(build_scout_prompt(brief.scout_brief(angle)))],
                    max_searches=s.searches_per_scout,
                    allowed_domains=ALLOWED_DOMAINS,
                    user_location=location or {"country": "IN", "timezone": "Asia/Kolkata"},
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

    def _postings(
        self,
        reads: Sequence[BoardRead],
        stored: Sequence[DiscoverListing],
        registry: Registry,
        known: Sequence[BoardLead],
    ) -> list[Posting]:
        """The pool: listings of the boards read for this search, then every stored listing of Tayyari's employers (a
        stored one whose board a search also found keeps that search as how it was found)."""
        out: list[Posting] = []
        seen: set[str] = set()
        for read in reads:
            for listing in read.listings:
                if listing.id not in seen:
                    seen.add(listing.id)
                    out.append(
                        Posting(
                            listing, read.employer.name, "web", read.lead.first_query, listing.posting_text
                        )
                    )
        found_by: dict[str, str] = {}
        for lead in known:
            employer = self._reader.employer_for(lead, registry)
            if employer is not None:
                found_by.setdefault(employer.id, lead.first_query)
        for listing in stored:
            if listing.id in seen:
                continue
            seen.add(listing.id)
            employer = registry.get(listing.employer_id)
            name = employer.name if employer is not None else listing.employer_id.replace("-", " ").title()
            query = found_by.get(listing.employer_id)
            out.append(Posting(listing, name, "web" if query else "tayyari", query))
        return out

    # ───────────────────────── output ─────────────────────────

    def _job(self, candidate: Candidate, verdict: Verdict, fit: AiFitWord) -> AiJob:
        posting = candidate.posting
        listing = posting.listing
        checked = (
            self._clock.now() if posting.via == "web" and posting.text is not None else listing.last_seen_at
        )
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
    def _done(funnel: Sequence[FunnelStep], jobs: int, note: str | None) -> JobSearchDoneEvent:
        return JobSearchDoneEvent(jobs=jobs, funnel=list(funnel), note=note)

    @staticmethod
    def _plan_text(brief: Brief, searches: int) -> str:
        roles = " and ".join(dict.fromkeys(title for angle in brief.angles for title in angle.titles))
        where = f"in {brief.city.name} and remote in India" if brief.city else "remote in India"
        scouts = _plural(len(brief.angles), "search", "searches")
        return (
            f"Looking for {roles} roles {where}{_years(brief.years)}: {scouts} on employers' own job sites, "
            f"up to {searches} queries each."
        )

    @staticmethod
    def _boards_text(
        reads: Sequence[BoardRead], stored: Sequence[DiscoverListing], registry: Registry
    ) -> str:
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
        employers = len({listing.employer_id for listing in stored})
        if stored:
            text += f" Tayyari's own {_plural(employers, 'employer')}: {_plural(len(stored), 'open role')}."
        return text

    @staticmethod
    def _narrow_text(brief: Brief, funnel: Sequence[FunnelStep]) -> str:
        counts = {step.label: step.count for step in funnel}
        where = f"in {brief.city.name} or remote in India" if brief.city else "remote in India"
        placed = funnel[1].count if len(funnel) > 1 else 0
        kind = counts.get("Your kind of role", 0)
        level = counts.get("Within reach of your experience", 0)
        short = counts.get("Read against your evidence", 0)
        return (
            f"{_plural(placed, 'role')} {where}, {kind:,} of your kind, {level:,} within reach of your experience. "
            f"The closest {short:,} go to the read."
        )

    @staticmethod
    def _result_text(shown: dict[str, int], dropped: dict[str, int], quotes_dropped: int) -> str:
        total = sum(shown.values())
        parts = [f"{shown['strong']:,} strong", f"{shown['good']:,} good", f"{shown['stretch']:,} a stretch"]
        text = f"{_plural(total, 'role fits', 'roles fit')} after the checks: {', '.join(parts)}."
        reasons = {
            "different": "another kind of role on reading",
            "far_above": "asked for far more experience",
            "nothing_met": "named nothing your evidence shows",
            "instructions": "carried text aimed at an AI, so it was left out",
            "missing": "could not be read",
        }
        left = [f"{n:,} {reasons[key]}" for key, n in dropped.items() if n and key in reasons]
        if left:
            text += " Left out: " + "; ".join(left) + "."
        if quotes_dropped:
            text += f" {_plural(quotes_dropped, 'quote')} that could not be found word for word in a posting were dropped."
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


def _with_purpose(ctx: CallContext, purpose: Purpose) -> CallContext:
    return replace(ctx, purpose=purpose)


__all__ = ["JobSearchService"]
