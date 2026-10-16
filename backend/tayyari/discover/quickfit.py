"""The quick fit: a word and counts, worked out in code from the skill names a posting uses against a person's profile.

It is never a score and never a percentage, and no model is involved. The posting is read for the skills it names (the
vocabulary, plus the person's own skill names as extra terms); the profile "shows" a skill when it holds one at a score
of at least 30, or holds a skill that plainly includes it (`vocab.IMPLIES`: TypeScript shows JavaScript).

What a posting asks for is counted in asks, not raw terms (calibrated on 1,377 real postings, 2026-10-16, where raw
counting rated every role "a stretch" for a strong frontend profile):

    - skills from `vocab.GENERAL` (Git, Agile, equal-opportunity boilerplate) are not counted at all;
    - alternatives from `vocab.ANY_OF` that the posting names ("React, Angular or Vue") are one ask, met by any of them.

From those asks:

    named      the asks the posting makes
    matched    of those, the ones the profile shows
    notFound   the rest, most prominent first (named in the title, then under a requirements heading, then most often),
               at most five; an unmet group reads "Angular or Vue.js"

and the word:

    cant_tell      the posting makes fewer than 3 asks, or the profile holds fewer than 3 skills
    stretch        the posting asks for 3 or more years than the profile shows
    otherwise by the share met (kept inside this module, never shown): 0.6 or more with at least 3 met is looks_close,
                   0.35 or more with at least 2 met is worth_a_look, below that stretch

Reasons are the two strongest matched skills, "You show {skill}", each citing the first line of evidence the profile holds
for it. A skill without evidence gives no reason.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Final

from tayyari.contract.discover import QuickFit, QuickFitWord, Reason

from .person import PersonIndex, Shown
from .textutil import in_spans, sections
from .vocab import TermMatcher, Vocabulary, prep

MIN_NAMED: Final = 3
YEARS_GAP_STRETCH: Final = 3
CLOSE_SHARE: Final = 0.6
CLOSE_MIN_MET: Final = 3
LOOK_SHARE: Final = 0.35
LOOK_MIN_MET: Final = 2
MAX_NOT_FOUND: Final = 5
MAX_REASONS: Final = 2


@dataclass(frozen=True, slots=True)
class Mention:
    """How a posting names a skill."""

    in_title: bool
    in_requirements: bool
    count: int


class PostingIndex:
    """A posting read once: its prepared text, where its requirement lines are, and the vocabulary skills it names.

    A skill named only in the parts about the company or what it offers (see `textutil.sections`) is not counted: a
    benefits section that talks about "compensation" or "learning budget" does not ask for those skills.
    """

    __slots__ = ("mentions", "noise", "spans", "text", "title")

    def __init__(self, title: str, text: str, vocab: Vocabulary) -> None:
        self.title = prep(title)
        self.text = prep(text)
        self.spans, self.noise = sections(text)
        self.mentions = self._tally(vocab.matcher)

    def _tally(self, matcher: TermMatcher) -> dict[str, Mention]:
        found: dict[str, list[int | bool]] = {}
        for term_id, _ in matcher.scan(self.title):
            entry = found.setdefault(term_id, [False, False, 0])
            entry[0] = True
            entry[2] = int(entry[2]) + 1
        for term_id, position in matcher.scan(self.text):
            if in_spans(self.noise, position):
                continue
            entry = found.setdefault(term_id, [False, False, 0])
            entry[1] = bool(entry[1]) or in_spans(self.spans, position)
            entry[2] = int(entry[2]) + 1
        return {k: Mention(bool(v[0]), bool(v[1]), int(v[2])) for k, v in found.items()}

    def extra_mentions(self, matcher: TermMatcher) -> dict[str, Mention]:
        """The same tally for a person's own extra terms."""
        if not len(matcher):
            return {}
        return self._tally(matcher)


class IndexCache:
    """A bounded cache of `PostingIndex` by ``(listing id, content hash)``: a posting is read once per process."""

    def __init__(self, capacity: int = 6000) -> None:
        self._capacity = capacity
        self._items: OrderedDict[tuple[str, str], PostingIndex] = OrderedDict()

    def get(self, listing_id: str, content_hash: str) -> PostingIndex | None:
        key = (listing_id, content_hash)
        found = self._items.get(key)
        if found is not None:
            self._items.move_to_end(key)
        return found

    def put(self, listing_id: str, content_hash: str, index: PostingIndex) -> None:
        self._items[(listing_id, content_hash)] = index
        self._items.move_to_end((listing_id, content_hash))
        while len(self._items) > self._capacity:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


@dataclass(frozen=True, slots=True)
class Fit:
    """A quick fit with what grouping needs beyond the contract: every unmatched skill, in order."""

    word: QuickFitWord
    named: int
    matched: int
    matched_ids: tuple[str, ...]
    """Matched canonical skill ids, strongest (highest profile score) first."""
    missing: tuple[tuple[str, str], ...]
    """Every named skill the profile does not show, ``(id, display name)``, most prominent first."""
    years_asked: int | None
    years_you: float | None
    reasons: tuple[Reason, ...]

    def to_model(self) -> QuickFit:
        return QuickFit(
            word=self.word,
            named=self.named,
            matched=self.matched,
            notFound=[name for _, name in self.missing[:MAX_NOT_FOUND]],
            yearsAsked=self.years_asked,
            yearsYou=self.years_you,
            reasons=list(self.reasons),
        )


def _word(named: int, matched: int, person: PersonIndex, years_asked: int | None) -> QuickFitWord:
    if named < MIN_NAMED or person.thin:
        return "cant_tell"
    if (
        years_asked is not None
        and person.years is not None
        and years_asked - person.years >= YEARS_GAP_STRETCH
    ):
        return "stretch"
    share = matched / named
    if share >= CLOSE_SHARE and matched >= CLOSE_MIN_MET:
        return "looks_close"
    if share >= LOOK_SHARE and matched >= LOOK_MIN_MET:
        return "worth_a_look"
    return "stretch"


def quick_fit(index: PostingIndex, years_asked: int | None, person: PersonIndex, vocab: Vocabulary) -> Fit:
    """The quick fit of one posting for one person (see the module docstring)."""
    mentions = dict(index.mentions)
    for term_id, extra in index.extra_mentions(person.extras).items():
        current = mentions.get(term_id)
        if current is None:
            mentions[term_id] = extra
        else:
            mentions[term_id] = Mention(
                current.in_title or extra.in_title,
                current.in_requirements or extra.in_requirements,
                current.count + extra.count,
            )

    def name_of(term_id: str) -> str:
        return vocab.name_of(term_id) or person.extra_names.get(term_id, term_id.removeprefix("x:"))

    def prominence(term_id: str) -> tuple[bool, bool, int, str]:
        m = mentions[term_id]
        return (not m.in_title, not m.in_requirements, -m.count, name_of(term_id).lower())

    def covering(term_id: str) -> str | None:
        """The id the profile covers that shows ``term_id``: the term itself, or a skill that plainly includes it."""
        if person.shows(term_id) is not None:
            return term_id
        implying = [src for src in vocab.implied_by.get(term_id, ()) if person.shows(src) is not None]
        return max(implying, key=lambda src: person.covered[src].skill.score) if implying else None

    def held_for(term_id: str) -> Shown | None:
        source = covering(term_id)
        return None if source is None else person.shows(source)

    # asks: general skills are not counted; named alternatives are one ask, met by any of them
    asks: dict[str, list[str]] = {}
    for term_id in mentions:
        if term_id in vocab.general:
            continue
        asks.setdefault(vocab.group_of.get(term_id, term_id), []).append(term_id)
    for members in asks.values():
        members.sort(key=prominence)

    def ask_prominence(key: str) -> tuple[bool, bool, int, str]:
        return prominence(asks[key][0])

    met = {key for key, members in asks.items() if any(covering(t) is not None for t in members)}
    # the profile's own ids that met the asks (an included skill is credited to the skill that includes it), so every
    # matched id is one the profile covers
    shown = {source for key in met for t in asks[key] if (source := covering(t)) is not None}

    def ask_name(key: str) -> str:
        names = [name_of(t) for t in asks[key]]
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]

    missing = tuple((key, ask_name(key)) for key in sorted(set(asks) - met, key=ask_prominence))

    def strength(term_id: str) -> tuple[int, tuple[bool, bool, int, str]]:
        held = held_for(term_id)
        mention = prominence(term_id) if term_id in mentions else (True, True, 0, name_of(term_id).lower())
        return (-(held.skill.score if held else 0), mention)

    matched_ids = tuple(sorted(shown, key=strength))
    reasons: list[Reason] = []
    used: set[str] = set()
    for term_id in matched_ids:
        held = held_for(term_id)
        if held is None or held.skill.id in used:
            continue
        cite = person.cite_for(held.skill)
        if cite is None:
            continue
        used.add(held.skill.id)
        reasons.append(Reason(text=f"You show {held.skill.name}", cite=cite))
        if len(reasons) == MAX_REASONS:
            break
    return Fit(
        word=_word(len(asks), len(met), person, years_asked),
        named=len(asks),
        matched=len(met),
        matched_ids=matched_ids,
        missing=missing,
        years_asked=years_asked,
        years_you=person.years,
        reasons=tuple(reasons),
    )


__all__ = [
    "Fit",
    "IndexCache",
    "Mention",
    "PostingIndex",
    "quick_fit",
]
