"""Narrowing the open roles to a shortlist, in code, the way both baseline runs did it by hand: place, kind, level.

    place   in the person's city, or remote in India (remote only when no city is known)
    kind    the title shares the distinctive words of the person's current role or a next role ("frontend" in
            "Frontend Engineer II"; "design" and "systems" in "Design Systems Engineer"), or names one of their strongest
            skills ("React Developer"). Seniority and generic words ("senior", "engineer", "software") never count, so
            "Senior DevRel Engineer" is not a frontend role, and a title with a word of another field ("UI Tester",
            "IC Design Engineer") is not their kind unless their own titles use that word.
    level   a title for leaders (Staff, Principal, Lead, Head, Director, Architect, Manager) needs six or more years; a
            "Senior" title needs two; an internship or a "0-1 years" role is left out from two years on; a posting asking
            for more than two years over the person's is left out. Unknown years keep the role.

The survivors are ranked by how well the title fits, then the quick fit (Discover's, code only), then freshness, and cut
to the shortlist with at most ``per_employer`` roles of one employer, so one big board cannot fill it. The ranking is
never shown; only its counts are.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from tayyari.contract.jobsearch import FoundVia, FunnelStep
from tayyari.discover.models import FIT_ORDER
from tayyari.discover.normalise import is_entry_level_title, normalise_title, title_tokens
from tayyari.discover.person import PersonIndex
from tayyari.discover.quickfit import Fit, PostingIndex, quick_fit
from tayyari.discover.textutil import YearsAsked, years_asked
from tayyari.discover.vocab import Vocabulary, prep
from tayyari.storage.ports import DiscoverListing

from .brief import Brief

GENERIC: Final = frozenset(
    {
        "engineer", "software", "senior", "junior", "lead", "staff", "principal", "head", "manager", "associate",
        "member", "technical", "team", "intern", "trainee", "director", "vp", "chief", "architect", "specialist",
        "consultant", "executive", "officer", "assistant", "level", "mts", "sde", "swe", "ii", "iii", "experienced",
        "expert", "fresher", "graduate", "new", "grad", "apprentice", "contract", "contractor", "remote", "india",
    }
)  # fmt: skip
"""Title words that say how senior or how generic a role is, never what kind of work it is."""
OTHER_FIELDS: Final = frozenset(
    {
        "designer", "tester", "testing", "qa", "sdet", "analyst", "recruiter", "recruiting", "sales", "ic", "analog",
        "mixed", "signal", "vlsi", "asic", "fpga", "rtl", "silicon", "mechanical", "electrical", "civil", "hardware",
        "layout", "verification", "writer", "support",
    }
)  # fmt: skip
"""Title words of another field that shares a word with the person's ("UI Tester", "UI/UX Designer", "IC Design Engineer"
for a frontend or design engineer). A word the person's own role titles use is never one of them."""
LEADER_YEARS: Final = 6.0
SENIOR_STEP_YEARS: Final = 4.0
"""Under this many years, a "Senior" title that states no years is read as a step up."""
SENIOR_YEARS: Final = 2.0
EXTRA_YEARS: Final = 2.0
"""A posting may ask for this many years over the person's and still be on the shortlist (as a step up)."""
_LEADER = re.compile(
    r"\b(?:staff|principal|lead|head|director|architect|vp|vice\s+president|chief|manager)\b", re.IGNORECASE
)
_SENIOR = re.compile(r"\b(?:senior|sr)\b\.?", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Posting:
    """An open role the search may show: the stored or freshly read listing, its text, who posts it, how it was found."""

    listing: DiscoverListing
    employer: str
    via: FoundVia
    found_by: str | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    posting: Posting
    text: str
    strength: int
    """2: the title carries all the distinctive words of a target role; 1: all but one of them, or a skill (`Targets`)."""
    asked: YearsAsked | None
    fit: Fit


@dataclass(frozen=True, slots=True)
class Narrowed:
    funnel: tuple[FunnelStep, ...]
    shortlist: tuple[Candidate, ...]
    place_count: int
    kind_count: int
    level_count: int


def _distinctive(title: str) -> frozenset[str]:
    return title_tokens(normalise_title(title)) - GENERIC


class Targets:
    """What the person's kind of role is: the distinctive words of their role titles, and their strongest skills as the
    vocabulary knows them (a title that names one, "React Developer", is their kind too)."""

    def __init__(self, brief: Brief, person: PersonIndex, vocab: Vocabulary) -> None:
        titles = [brief.role, *brief.next_roles, *(role.title for role in person.next_roles)]
        sets = [_distinctive(title) for title in titles if title]
        self.titles: tuple[frozenset[str], ...] = tuple(dict.fromkeys(s for s in sets if s))
        own = frozenset().union(*(title_tokens(normalise_title(title)) for title in titles if title))
        self.other_fields = OTHER_FIELDS - own
        strongest = set(brief.skills)
        self.skills = frozenset(
            skill_id
            for skill_id, held in person.covered.items()
            if held.shown
            and held.skill.name.strip() in strongest
            and not skill_id.startswith("x:")
            and skill_id not in vocab.general
        )
        self._vocab = vocab

    def strength(self, title: str) -> int:
        """2: the title carries every distinctive word of a role title; 1: all but one of them (and at least two, so a
        two-word title needs both: "People Systems Engineer" is not "Design Systems Engineer"), or names a strongest skill."""
        tokens = title_tokens(normalise_title(title))
        if tokens & self.other_fields:
            return 0
        best = 0
        for target in self.titles:
            shared = len(tokens & target)
            if shared == len(target):
                return 2
            if shared >= 2 and shared >= len(target) - 1:
                best = 1
        if not best and any(skill_id in self.skills for skill_id, _ in self._vocab.scan(prep(title))):
            best = 1
        return best


def senior_title(title: str) -> bool:
    return _SENIOR.search(title) is not None


def in_place(listing: DiscoverListing, city: str | None) -> bool:
    if listing.remote_india:
        return True
    return city is not None and listing.city == city


def at_level(title: str, asked: YearsAsked | None, years: float | None) -> bool:
    """Whether a role is within reach of someone with ``years`` (see the module docstring). Unknown years keep it."""
    if years is None:
        return True
    if _LEADER.search(title) and years < LEADER_YEARS:
        return False
    if _SENIOR.search(title) and years < SENIOR_YEARS:
        return False
    if years >= 2 and is_entry_level_title(title):
        return False
    if asked is not None:
        if asked.minimum > years + EXTRA_YEARS:
            return False
        if years >= 2 and asked.upper is not None and asked.upper < years - 1:
            return False
    return True


def _recency(posting: Posting) -> datetime:
    listing = posting.listing
    return listing.posted_at or listing.first_seen_at


@dataclass(frozen=True, slots=True)
class Selection:
    """The postings left after place and kind, read from the listing alone (no text needed yet)."""

    total: int
    placed: tuple[Posting, ...]
    kinds: tuple[tuple[Posting, int], ...]


def select(brief: Brief, targets: Targets, postings: Sequence[Posting]) -> Selection:
    """Place, then kind (see the module docstring). Cheap: titles and places only."""
    city = brief.city.name if brief.city is not None else None
    placed = tuple(p for p in postings if in_place(p.listing, city))
    scored = ((p, targets.strength(p.listing.title)) for p in placed)
    return Selection(total=len(postings), placed=placed, kinds=tuple((p, s) for p, s in scored if s > 0))


def narrow(
    brief: Brief,
    person: PersonIndex,
    vocab: Vocabulary,
    selection: Selection,
    texts: Mapping[str, str],
    *,
    max_judged: int,
    per_employer: int,
) -> Narrowed:
    """Level, quick fit and the shortlist. ``texts`` holds the posting text of every selected listing that came without
    one (Discover's stored listings); a listing with no text at all is left out."""
    levelled: list[Candidate] = []
    for posting, strength in selection.kinds:
        text = posting.text if posting.text is not None else texts.get(posting.listing.id)
        if not text:
            continue
        asked = years_asked(text)
        if not at_level(posting.listing.title, asked, brief.years):
            continue
        fit = quick_fit(
            PostingIndex(posting.listing.title, text, vocab), posting.listing.years_min, person, vocab
        )
        levelled.append(Candidate(posting=posting, text=text, strength=strength, asked=asked, fit=fit))
    ranked = sorted(
        levelled,
        key=lambda c: (
            -c.strength,
            FIT_ORDER.get(c.fit.word, 9),
            -c.fit.matched,
            -_recency(c.posting).timestamp(),
            c.posting.listing.id,
        ),
    )
    per: dict[str, int] = {}
    shortlist: list[Candidate] = []
    for candidate in ranked:
        employer = candidate.posting.listing.employer_id
        if per.get(employer, 0) >= per_employer:
            continue
        per[employer] = per.get(employer, 0) + 1
        shortlist.append(candidate)
        if len(shortlist) == max_judged:
            break
    city = brief.city.name if brief.city is not None else None
    where = f"In {city} or remote in India" if city else "Remote in India"
    funnel = (
        FunnelStep(label="Open roles in India on the boards read", count=selection.total),
        FunnelStep(label=where, count=len(selection.placed)),
        FunnelStep(label="Your kind of role", count=len(selection.kinds)),
        FunnelStep(label="Within reach of your experience", count=len(levelled)),
        FunnelStep(label="Read against your evidence", count=len(shortlist)),
    )
    return Narrowed(
        funnel=funnel,
        shortlist=tuple(shortlist),
        place_count=len(selection.placed),
        kind_count=len(selection.kinds),
        level_count=len(levelled),
    )


__all__ = [
    "GENERIC",
    "Candidate",
    "Narrowed",
    "Posting",
    "Selection",
    "Targets",
    "at_level",
    "in_place",
    "narrow",
    "select",
    "senior_title",
]
