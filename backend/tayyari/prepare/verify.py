"""The code checks on what the three calls return. Nothing a model wrote reaches a plan without passing them.

The rule for every sentence is the same: the ids it cites must exist, and every digit sequence in it must be in the
evidence it cites, in the gaps' levels or in the role's eta. What fails is dropped, never rewritten:

- standing: a why without a valid citation, or with a number from nowhere, is dropped (the gap keeps its numbers); the
  headline is kept only when its numbers are in the evidence or the levels;
- moves: a have item is dropped like a why; a task or a deliverable that fails is dropped; a move whose title or body
  fails is dropped; ``works`` is filtered to the plan's gap names and a move with none left is dropped; hours outside
  1..40 drop the phase; at most 5 phases of at most 4 tasks each; a phase with no task or no deliverable left is dropped,
  and a move with no phase left; the first three moves that survive are ``m1``..``m3``;
- headline: kept only when it is at most 120 characters, cites at least one valid id, and every number and every
  capitalised word after the first appears in the cited evidence, the role title or the current headline.

Lengths are clipped at a word boundary (titles 90, body 200, tasks 160, deliverable 120, why 180, headline 160).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from tayyari.contract.prepare import (
    MAX_HOURS,
    MAX_MOVES,
    MIN_HOURS,
    PrepareCited,
    PrepareEvidence,
    PrepareGap,
    PrepareHeadline,
    PrepareMove,
    PreparePhase,
)
from tayyari.prepare.schemas import HeadlineOut, MoveOut, PhaseOut, StandingGapOut, rows_of

TITLE_CHARS: Final = 90
BODY_CHARS: Final = 200
TASK_CHARS: Final = 160
DELIVERABLE_CHARS: Final = 120
WHY_CHARS: Final = 180
HEADLINE_CHARS: Final = 160
HAVE_CHARS: Final = 160
PROPOSED_HEADLINE_CHARS: Final = 120
MAX_PHASES: Final = 5
MAX_TASKS: Final = 4
MAX_CITES: Final = 3

_NUMBER: Final = re.compile(r"\d(?:[\d,.]*\d)?")
_WORD: Final = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
_EDGE_PUNCT: Final = ".,;:!?()\"'“”‘’[]{}<>·|/\\-–—"

HEADLINE_FAILED_NOTE: Final = (
    "The headline the model wrote named something your evidence does not show, so it was not kept."
)
HEADLINE_UNAVAILABLE_NOTE: Final = "A headline could not be written right now. Try again in a moment."


# ───────────────────────── small helpers ─────────────────────────


def clip(text: object, limit: int) -> str:
    """``text`` on one line, cut at a word boundary to ``limit`` characters (with an ellipsis); "" for non-text."""
    if not isinstance(text, str):
        return ""
    one = " ".join(text.split())
    if len(one) <= limit:
        return one
    head = one[: limit - 1]
    space = head.rfind(" ")
    return f"{(head[:space] if space > limit * 0.5 else head).rstrip(' ,;:-')}…"


_CLAUSE_BREAKS: Final = (
    "; ",
    ": ",
    " — ",
    " - ",
    ", but ",
    ", and ",
    ", so ",
    ", with ",
    ", where ",
    ", which ",
)


def clip_sentence(text: object, limit: int) -> str:
    """A sentence on one line within ``limit``: when it is longer, cut at the last clause break that fits (so it still
    ends as a whole statement, with a full stop), else at a word boundary with an ellipsis (`clip`)."""
    if not isinstance(text, str):
        return ""
    one = " ".join(text.split())
    if len(one) <= limit:
        return one
    head = one[: limit - 1]
    cut = max(head.rfind(mark) for mark in _CLAUSE_BREAKS)
    if cut > limit * 0.4:
        return head[:cut].rstrip(" ,;:-—") + "."
    return clip(one, limit)


def numbers_in(text: str) -> set[str]:
    """Every digit sequence in ``text`` (``4.2``, ``10,000+`` -> ``10000``, ``2026-09`` -> ``2026`` and ``09``)."""
    return {m.group().replace(",", "") for m in _NUMBER.finditer(text)}


def numbers_of(texts: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for text in texts:
        out |= numbers_in(text)
    return out


def words_of(texts: Iterable[str]) -> set[str]:
    """Every word of ``texts``, case-folded and without a possessive ending, for the capitalised-word check."""
    out: set[str] = set()
    for text in texts:
        for match in _WORD.finditer(text):
            word = match.group().casefold()
            out.add(word)
            for ending in ("'s", "’s"):
                if word.endswith(ending):
                    out.add(word[: -len(ending)])
    return out


def capitalised_words(text: str) -> list[str]:
    """The words of ``text`` after the first that start with a capital letter (names, tools, acronyms)."""
    found = [m.group() for m in _WORD.finditer(text)]
    return [w for w in found[1:] if w[:1].isupper()]


def valid_ids(raw: Any, known: Mapping[str, PrepareEvidence], *, limit: int = MAX_CITES) -> list[str]:
    """The cited ids that exist, in order, without repeats, at most ``limit``."""
    if not isinstance(raw, list):
        return []
    return [i for i in dict.fromkeys(x for x in raw if isinstance(x, str)) if i in known][:limit]


def _plan_numbers(gaps: Sequence[PrepareGap], eta: str) -> set[str]:
    out = {str(g.current) for g in gaps} | {str(g.target) for g in gaps}
    return out | numbers_in(eta)


def _cited_texts(ids: Iterable[str], known: Mapping[str, PrepareEvidence]) -> list[str]:
    return [known[i].text for i in ids if i in known]


def _passes(text: str, allowed: set[str]) -> bool:
    """Every number of ``text`` is in ``allowed`` (a text with no number always passes)."""
    return numbers_in(text) <= allowed


# ───────────────────────── standing ─────────────────────────


@dataclass(frozen=True, slots=True)
class StandingChecked:
    """What survived of the standing call: the headline (None when dropped) and the gaps with their whys."""

    headline: str | None
    gaps: list[PrepareGap]

    @property
    def why_count(self) -> int:
        return sum(1 for g in self.gaps if g.why)


def check_standing(
    data: Mapping[str, Any],
    *,
    gaps: Sequence[PrepareGap],
    evidence: Sequence[PrepareEvidence],
    eta: str,
) -> StandingChecked:
    """The standing answer checked: see the module docstring."""
    known = {e.id: e for e in evidence}
    levels = _plan_numbers(gaps, eta)
    headline = clip_sentence(data.get("headline"), HEADLINE_CHARS)
    if not headline or not _passes(headline, levels | numbers_of(e.text for e in evidence)):
        headline_kept: str | None = None
    else:
        headline_kept = headline

    by_name = {g.name.strip().casefold(): g for g in gaps}
    whys: dict[str, tuple[str, list[str]]] = {}
    for row in rows_of(data, "gaps", StandingGapOut):
        gap = by_name.get(row.name.strip().casefold())
        if gap is None or gap.name in whys:
            continue
        ids = valid_ids(row.evidenceIds, known)
        why = clip_sentence(row.why, WHY_CHARS)
        if not ids or not why:
            continue
        if not _passes(why, levels | numbers_of(_cited_texts(ids, known))):
            continue
        whys[gap.name] = (why, ids)
    checked = [
        g.model_copy(update={"why": whys[g.name][0], "evidenceIds": whys[g.name][1]}) if g.name in whys else g
        for g in gaps
    ]
    return StandingChecked(headline=headline_kept, gaps=checked)


# ───────────────────────── moves ─────────────────────────


def _hours_ok(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and MIN_HOURS <= value <= MAX_HOURS


def _check_phase(raw: PhaseOut, allowed: set[str]) -> PreparePhase | None:
    title = clip(raw.title, TITLE_CHARS)
    deliverable = clip(raw.deliverable, DELIVERABLE_CHARS)
    if not title or not _passes(title, allowed) or not _hours_ok(raw.hours):
        return None
    if not deliverable or not _passes(deliverable, allowed):
        return None
    tasks: list[str] = []
    for text in raw.tasks:
        task = clip(text, TASK_CHARS)
        if task and _passes(task, allowed) and task not in tasks:
            tasks.append(task)
        if len(tasks) >= MAX_TASKS:
            break
    if not tasks:
        return None
    return PreparePhase(title=title, hours=raw.hours, tasks=tasks, deliverable=deliverable)


def _check_move(
    raw: MoveOut,
    *,
    known: Mapping[str, PrepareEvidence],
    gap_names: Mapping[str, str],
    levels: set[str],
) -> PrepareMove | None:
    have: list[PrepareCited] = []
    for claim in raw.have:
        ids = valid_ids(claim.evidenceIds, known)
        text = clip_sentence(claim.text, HAVE_CHARS)
        if ids and text and _passes(text, levels | numbers_of(_cited_texts(ids, known))):
            have.append(PrepareCited(text=text, evidenceIds=ids))
    cited: set[str] = set()
    for kept in have:
        cited.update(kept.evidenceIds)
    allowed = levels | numbers_of(_cited_texts(cited, known))

    title = clip(raw.title, TITLE_CHARS)
    body = clip_sentence(raw.body, BODY_CHARS)
    if not title or not body or not _passes(title, allowed) or not _passes(body, allowed):
        return None
    works = list(
        dict.fromkeys(
            gap_names[w.strip().casefold()]
            for w in raw.works
            if isinstance(w, str) and w.strip().casefold() in gap_names
        )
    )
    if not works:
        return None
    phases: list[PreparePhase] = []
    for raw_phase in raw.phases:
        phase = _check_phase(raw_phase, allowed)
        if phase is not None:
            phases.append(phase)
        if len(phases) >= MAX_PHASES:
            break
    if not phases:
        return None
    return PrepareMove(id="", title=title, body=body, have=have, works=works, phases=phases)


def check_moves(
    data: Mapping[str, Any],
    *,
    gaps: Sequence[PrepareGap],
    evidence: Sequence[PrepareEvidence],
    eta: str,
) -> list[PrepareMove]:
    """The moves that survive the checks, best first, with ids ``m1``..``m3``."""
    known = {e.id: e for e in evidence}
    gap_names = {g.name.strip().casefold(): g.name for g in gaps}
    levels = _plan_numbers(gaps, eta)
    out: list[PrepareMove] = []
    for raw in rows_of(data, "moves", MoveOut):
        move = _check_move(raw, known=known, gap_names=gap_names, levels=levels)
        if move is not None:
            out.append(move.model_copy(update={"id": f"m{len(out) + 1}"}))
        if len(out) >= MAX_MOVES:
            break
    return out


# ───────────────────────── headline ─────────────────────────


def check_headline(
    data: Mapping[str, Any] | None,
    *,
    evidence: Sequence[PrepareEvidence],
    role_title: str,
    current: str,
) -> PrepareHeadline:
    """The headline answer checked; ``data`` None means the call failed (a note says so, nothing is proposed)."""
    if data is None:
        return PrepareHeadline(current=current, evidenceIds=[], note=HEADLINE_UNAVAILABLE_NOTE)
    try:
        answer = HeadlineOut.model_validate(data)
    except ValueError:
        return PrepareHeadline(current=current, evidenceIds=[], note=HEADLINE_FAILED_NOTE)
    known = {e.id: e for e in evidence}
    ids = valid_ids(answer.evidenceIds, known, limit=4)
    headline = " ".join(answer.headline.split())
    if not ids or not headline or len(headline) > PROPOSED_HEADLINE_CHARS:
        return PrepareHeadline(current=current, evidenceIds=ids, note=HEADLINE_FAILED_NOTE)
    texts = [*_cited_texts(ids, known), role_title, current]
    if not _passes(headline, numbers_of(texts)):
        return PrepareHeadline(current=current, evidenceIds=ids, note=HEADLINE_FAILED_NOTE)
    allowed_words = words_of(texts)
    for word in capitalised_words(headline):
        folded = word.casefold()
        bare = folded.removesuffix("'s").removesuffix("’s")
        if folded not in allowed_words and bare not in allowed_words:
            return PrepareHeadline(current=current, evidenceIds=ids, note=HEADLINE_FAILED_NOTE)
    return PrepareHeadline(current=current, proposed=headline, evidenceIds=ids)


def gap_numbers(gaps: Sequence[PrepareGap], eta: str) -> set[str]:
    """The numbers a sentence may use without citing a line: the gaps' levels and the role's eta (for tests)."""
    return _plan_numbers(gaps, eta)


__all__ = [
    "BODY_CHARS",
    "DELIVERABLE_CHARS",
    "HAVE_CHARS",
    "HEADLINE_CHARS",
    "HEADLINE_FAILED_NOTE",
    "HEADLINE_UNAVAILABLE_NOTE",
    "MAX_CITES",
    "MAX_PHASES",
    "MAX_TASKS",
    "PROPOSED_HEADLINE_CHARS",
    "TASK_CHARS",
    "TITLE_CHARS",
    "WHY_CHARS",
    "StandingChecked",
    "capitalised_words",
    "check_headline",
    "check_moves",
    "check_standing",
    "clip",
    "clip_sentence",
    "gap_numbers",
    "numbers_in",
    "numbers_of",
    "valid_ids",
    "words_of",
]
