"""The openings section: code chooses, one model read phrases the lead.

window     open listings of Tayyari's own employers first seen in the last 7 days; if fewer than 3 survive the steps below,
           the last 14 days
narrow     the job search's own steps (`jobsearch.narrow`): the reader's city (from their graph) or remote in India, the
           reader's kind of role, their level; the code-only quick fit (`discover.quickfit`) of each posting against the
           reader's graph
rank       the quick-fit word (`FIT_ORDER`: looks close, worth a look, stretch, can't tell), then the newest first; at most
           two of one employer; the top 3, leaving out "can't tell" whenever anything better came in. A posting with text aimed at an AI is left out and logged as an anomaly.
lead       the first of the three gets one read (`jobsearch.judge`'s prompt, schema and `verify`, purpose ``weekly.judge``):
           the asks it meets, each a verbatim quote of the posting with the reader's own evidence line, and the asks
           nothing shows, in the posting's words. Every quote is checked in code. The other two carry the quick fit's own
           facts only (no `met`; `notShown` is the skills the posting names that the graph does not show).
note       one plain line built by code from the counts ("You meet 2 of 3 asks."), never a score
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from tayyari.contract.models import Profile
from tayyari.contract.weekly import WeeklyCite, WeeklyJob, WeeklyMet
from tayyari.core.logging import get_logger
from tayyari.core.settings import WeeklySettings
from tayyari.discover.person import PersonIndex
from tayyari.discover.places import find_city
from tayyari.discover.quickfit import FIT_ORDER
from tayyari.discover.registry import Registry
from tayyari.discover.vocab import Vocabulary, vocabulary
from tayyari.jobsearch.boards import pretty_name
from tayyari.jobsearch.brief import Brief, build_brief
from tayyari.jobsearch.judge import JUDGE_SCHEMA, Verdict, has_injection, verify
from tayyari.jobsearch.narrow import Candidate, Posting, Targets, narrow, select
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext, TextBlock
from tayyari.prompts.jobsearch import JUDGE_SYSTEM, PostingForJudge, build_judge_prompt
from tayyari.storage.ports import DiscoverListing, DiscoverRepo

log = get_logger(__name__)

JOBS_SHOWN: Final = 3
WINDOWS: Final = (timedelta(days=7), timedelta(days=14))
PER_EMPLOYER: Final = 2
POOL: Final = 60
"""Candidates kept after narrowing, before the final ranking."""
POSTING_CHARS: Final = 6000
JUDGE_MAX_TOKENS: Final = 3000


@dataclass(frozen=True, slots=True)
class Picked:
    """The openings chosen for one reader, with what the lead's read needs."""

    brief: Brief
    person: PersonIndex
    candidates: tuple[Candidate, ...]


def place_of(listing: DiscoverListing) -> str:
    """City or "Remote", from the listing."""
    if listing.remote_india:
        return "Remote"
    if listing.city and listing.locality:
        return f"{listing.locality}, {listing.city}"
    if listing.city:
        return listing.city
    return listing.location_text[:60] or "India"


def met_note(met: int, partly: int, checked: int) -> str | None:
    """ "You meet 2 of 3 asks." from the read's counts (None when it checked nothing)."""
    if checked <= 0:
        return None
    asks = "ask" if checked == 1 else "asks"
    note = f"You meet {met} of {checked} {asks}"
    if partly:
        note += f" and part of {partly} more"
    return f"{note}."


def shown_note(named: int, matched: int, word: str) -> str | None:
    """ "You show 3 of the 5 skills it names." from the quick fit's counts."""
    if word == "cant_tell" or named <= 0:
        return "It names too few skills to tell."
    skills = "skill" if named == 1 else "skills"
    return f"You show {matched} of the {named} {skills} it names."


def _recency(candidate: Candidate) -> datetime:
    listing = candidate.posting.listing
    return listing.posted_at or listing.first_seen_at


class JobDesk:
    def __init__(
        self,
        *,
        claude: ClaudeClient,
        settings: WeeklySettings,
        repo: DiscoverRepo,
        registry: Callable[[], Registry],
        vocab: Vocabulary | None = None,
    ) -> None:
        self._claude = claude
        self._settings = settings
        self._repo = repo
        self._registry = registry
        self._vocab = vocab or vocabulary()

    def _employer(self, listing: DiscoverListing) -> str:
        employer = self._registry().get(listing.employer_id)
        return employer.name if employer is not None else pretty_name(listing.employer_id)

    async def pick(self, profile: Profile, now: datetime) -> Picked:
        """The reader's top openings (at most three), best first. Reads the store and nothing else: no model, no cost."""
        person = PersonIndex(profile, self._vocab)
        brief = build_brief(profile, find_city(profile.person.location), scouts=1)
        targets = Targets(brief, person, self._vocab)
        chosen: list[Candidate] = []
        for window in WINDOWS:
            listings = await self._repo.list_open_since(now - window)
            postings = [Posting(listing, self._employer(listing), "tayyari") for listing in listings]
            selection = select(brief, targets, postings)
            texts = await self._repo.texts([p.listing.id for p, _ in selection.kinds])
            narrowed = narrow(
                brief,
                person,
                self._vocab,
                selection,
                {key: text for key, (_, text) in texts.items()},
                max_judged=POOL,
                per_employer=PER_EMPLOYER,
            )
            safe: list[Candidate] = []
            for candidate in narrowed.shortlist:
                if has_injection(candidate.text):
                    log.warning("weekly_anomaly", where="posting", listing=candidate.posting.listing.id)
                    continue
                safe.append(candidate)
            ranked = sorted(
                safe,
                key=lambda c: (FIT_ORDER.get(c.fit.word, 9), -_recency(c).timestamp(), c.posting.listing.id),
            )
            # a "can't tell" opening only fills the page when nothing better came in: shown beside a fit it reads as noise
            told = [c for c in ranked if c.fit.word != "cant_tell"]
            chosen = (told or ranked)[:JOBS_SHOWN]
            if len(told) >= JOBS_SHOWN:
                break
        return Picked(brief=brief, person=person, candidates=tuple(chosen))

    async def read_lead(self, picked: Picked, ctx: CallContext) -> Verdict | None:
        """One read of the first opening against the reader's evidence, checked in code. None when there is nothing to read
        or the call failed (the opening then carries its quick-fit facts only)."""
        if not picked.candidates or not picked.brief.evidence:
            return None
        lead = picked.candidates[0]
        brief = picked.brief
        listing = lead.posting.listing
        text = lead.text[:POSTING_CHARS]
        prompt = build_judge_prompt(
            role=brief.role,
            years=brief.years,
            next_roles=brief.next_roles,
            evidence=[e.line() for e in brief.evidence],
            postings=[
                PostingForJudge(
                    id="P1",
                    title=listing.title,
                    employer=lead.posting.employer,
                    location=listing.location_text or place_of(listing),
                    text=text,
                )
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
                ctx=replace(ctx, purpose="weekly.judge"),
            )
        except Exception as exc:
            log.warning("weekly_judge_failed", kind=type(exc).__name__)
            return None
        items = result.data.get("postings")
        item = next(
            (
                i
                for i in (items if isinstance(items, list) else [])
                if isinstance(i, dict) and i.get("id") == "P1"
            ),
            None,
        )
        return verify(
            item,
            text=text,
            evidence=brief.evidence_by_id(),
            asked=lead.asked,
            years=brief.years,
            title=listing.title,
        )


def job_of(candidate: Candidate, verdict: Verdict | None) -> WeeklyJob:
    """One opening as the edition shows it: the quick-fit word always, the read's facts when this is the lead and its read held."""
    listing = candidate.posting.listing
    fit = candidate.fit
    if verdict is not None and verdict.dropped is None and verdict.checked > 0:
        met = [
            WeeklyMet(ask=m.ask, cite=WeeklyCite(source=m.cite.source, text=m.cite.text))
            for m in verdict.met
            if not m.partly
        ]
        not_shown = list(verdict.gaps)
        note = met_note(verdict.met_count, verdict.partly_count, verdict.checked)
    else:
        met = []
        not_shown = [name for _, name in fit.missing[:5]]
        note = shown_note(fit.named, fit.matched, fit.word)
    return WeeklyJob(
        listingId=listing.id,
        employer=candidate.posting.employer,
        title=listing.title,
        place=place_of(listing),
        applyUrl=listing.apply_url,
        fit=fit.word,
        met=met,
        notShown=not_shown,
        note=note,
    )


__all__ = ["JOBS_SHOWN", "JobDesk", "Picked", "job_of", "met_note", "place_of", "shown_note"]
