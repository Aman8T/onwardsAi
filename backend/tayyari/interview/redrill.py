"""Re-drill (I2): the claims that wobbled, chosen, ordered and started in code. No model, no I/O.

candidates  every claim (an evidence line asked in an ``ask``, ``claim`` or ``story`` slot) whose best verdict across
            its marks is Shaky or Couldn't defend, and whose folded text is still one of the profile's evidence lines.
            A reworded line is a new claim; a removed one is never asked again (its marks stay in Progress).
order       a Fix step ticked since the claim's last mark first, then Couldn't defend before Shaky (by the best
            verdict), then the oldest last mark first, then by text.
start       where the claim opens: Couldn't defend (the last mark's verdict, or the best one when the last was not
            judged) starts at ``surface``, asked again in new words; Shaky starts at ``push``. A story that starts at
            ``push`` asks for its first missing part.
plan        the top ``REDRILL_SHORT`` (3) by default, up to ``REDRILL_MAX`` (6) for ``full``, or exactly the claims the
            person picked (in code's order). An ``ask`` comes back as a ``claim``: the posting it was asked from is not
            part of a re-drill. Each slot carries its previous mark (``last``), for the room's line and for phrasing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from tayyari.contract.interview import (
    REDRILL_MAX,
    REDRILL_SHORT,
    InterviewLength,
    InterviewPlan,
    LastMark,
    PlanSlot,
    RedrillClaim,
    Rung,
    SlotKind,
    SlotLast,
    SlotSource,
    StoryParts,
    Verdict,
)
from tayyari.contract.models import Profile
from tayyari.jobsearch.brief import Evidence, evidence_lines
from tayyari.storage.ports import StoredClaimMark

from .errors import BadPlan
from .marks import claim_key, fix_task_of
from .phrase import STORY_PARTS
from .state import RANK, as_utc

REDRILL_TITLE: Final = "Re-drill what wobbled"
NOTHING_TO_REDRILL: Final = "Nothing to re-drill: every claim you've been asked about is Defended."
CLAIM_GONE: Final = "That claim isn't one to re-drill any more. Open Re-drill again to see the list."
WOBBLED: Final = ("shaky", "couldnt")
STORY_ORDER: Final = ("situation", "action", "result", "reflection")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One claim that can come back: its marks (oldest first), the current evidence line, and what code worked out."""

    key: str
    evidence: Evidence
    marks: tuple[StoredClaimMark, ...]
    best: Verdict
    best_on: str
    ready: bool
    start: Rung

    @property
    def last(self) -> StoredClaimMark:
        return self.marks[-1]

    @property
    def fix_mark(self) -> StoredClaimMark | None:
        """The latest mark whose Fix step is placed."""
        placed = [m for m in self.marks if fix_task_of(m) is not None]
        return max(placed, key=lambda m: (as_utc(m.fix_added_at), m.id)) if placed else None  # type: ignore[arg-type]


def _verdict(value: str) -> Verdict:
    return value if value in RANK else "not_assessed"  # type: ignore[return-value]


def best_of(marks: Sequence[StoredClaimMark]) -> StoredClaimMark:
    """The mark with the best verdict, the earliest of them (marks are oldest first)."""
    best = marks[0]
    for mark in marks[1:]:
        if RANK[_verdict(mark.verdict)] > RANK[_verdict(best.verdict)]:
            best = mark
    return best


def start_of(last: StoredClaimMark, best: Verdict) -> Rung:
    """Where a wobbled claim opens (see the module docstring)."""
    verdict = _verdict(last.verdict)
    deciding = verdict if verdict in WOBBLED else best
    return "surface" if deciding == "couldnt" else "push"


def story_push(last: StoredClaimMark) -> str:
    """The push a story re-drill opens with: the first part its last answer was missing (reflection when none was)."""
    parts = StoryParts.model_validate(last.story) if last.story else None
    for name in STORY_ORDER:
        if parts is None or not getattr(parts, name):
            return STORY_PARTS[name]
    return STORY_PARTS["reflection"]


def _ordered(marks: Iterable[StoredClaimMark]) -> list[StoredClaimMark]:
    return sorted(marks, key=lambda m: (m.marked_on, as_utc(m.created_at), m.id))


def candidates(profile: Profile, marks: Sequence[StoredClaimMark]) -> list[Candidate]:
    """The claims a re-drill can bring back, in re-drill order."""
    lines: dict[str, Evidence] = {}
    for line in evidence_lines(profile):
        lines.setdefault(claim_key(line.cite.text), line)
    by_key: dict[str, list[StoredClaimMark]] = {}
    for mark in marks:
        if mark.profile_id == profile.id:
            by_key.setdefault(mark.claim_key, []).append(mark)
    found: list[Candidate] = []
    for key, group in by_key.items():
        evidence = lines.get(key)
        if evidence is None:
            continue
        ordered = _ordered(group)
        best_mark = best_of(ordered)
        best = _verdict(best_mark.verdict)
        if best not in WOBBLED:
            continue
        last = ordered[-1]
        placed = [m for m in ordered if fix_task_of(m) is not None]
        latest = max(placed, key=lambda m: (as_utc(m.fix_added_at), m.id)) if placed else None  # type: ignore[arg-type]
        ready = (
            latest is not None
            and latest.fix_done_at is not None
            and as_utc(latest.fix_done_at) >= as_utc(last.created_at)
        )
        found.append(
            Candidate(
                key=key,
                evidence=evidence,
                marks=tuple(ordered),
                best=best,
                best_on=best_mark.marked_on.isoformat(),
                ready=ready,
                start=start_of(last, best),
            )
        )
    found.sort(
        key=lambda c: (
            0 if c.ready else 1,
            0 if c.best == "couldnt" else 1,
            c.last.marked_on,
            as_utc(c.last.created_at),
            c.key,
        )
    )
    return found


def claim_view(cand: Candidate) -> RedrillClaim:
    last = cand.last
    fix_mark = cand.fix_mark
    return RedrillClaim(
        key=cand.key,
        text=cand.evidence.cite.text,
        source=cand.evidence.cite.source or "",
        best=cand.best,
        last=LastMark(
            verdict=_verdict(last.verdict),
            on=last.marked_on.isoformat(),
            rung=last.rung if last.rung in ("surface", "push", "floor") else "surface",  # type: ignore[arg-type]
        ),
        times=len({m.session_id for m in cand.marks}),
        start=cand.start,
        fix=fix_task_of(fix_mark) if fix_mark is not None else None,
    )


def _slot_kind(kind: str) -> SlotKind:
    return "story" if kind == "story" else "claim"


def pick(
    cands: Sequence[Candidate], *, claims: Sequence[str] | None, length: InterviewLength | None
) -> list[Candidate]:
    """The claims of one re-drill: the ones picked (in code's order; an unknown key is refused), or the top 3 or 6."""
    if not cands:
        raise BadPlan(NOTHING_TO_REDRILL)
    if claims:
        wanted = list(dict.fromkeys(claims))
        known = {c.key for c in cands}
        if any(key not in known for key in wanted):
            raise BadPlan(CLAIM_GONE)
        chosen = set(wanted)
        return [c for c in cands if c.key in chosen][:REDRILL_MAX]
    return list(cands[: REDRILL_MAX if length == "full" else REDRILL_SHORT])


def slot_of(index: int, cand: Candidate) -> PlanSlot:
    last = cand.last
    return PlanSlot(
        id=f"s{index + 1}",
        kind=_slot_kind(last.slot_kind),
        topic=cand.evidence.skill,
        sources=[
            SlotSource(origin="evidence", text=cand.evidence.cite.text, source=cand.evidence.cite.source),
        ],
        start=cand.start,
        last=SlotLast(
            verdict=_verdict(last.verdict),
            on=last.marked_on.isoformat(),
            question=last.question,
            quote=last.quote,
            rung=last.rung if last.rung in ("surface", "push", "floor") else "surface",  # type: ignore[arg-type]
        ),
    )


def redrill_plan(
    profile: Profile,
    marks: Sequence[StoredClaimMark],
    *,
    claims: Sequence[str] | None,
    length: InterviewLength | None,
    minutes_per_slot: int,
) -> tuple[InterviewPlan, list[Candidate]]:
    """The plan of a re-drill and the candidates it asks (`BadPlan` when nothing wobbled or a picked claim is gone)."""
    chosen = pick(candidates(profile, marks), claims=claims, length=length)
    slots = [slot_of(i, c) for i, c in enumerate(chosen)]
    plan = InterviewPlan(
        kind="redrill", title=REDRILL_TITLE, slots=slots, minutes=minutes_per_slot * len(slots)
    )
    return plan, chosen


__all__ = [
    "CLAIM_GONE",
    "NOTHING_TO_REDRILL",
    "REDRILL_TITLE",
    "Candidate",
    "best_of",
    "candidates",
    "claim_view",
    "pick",
    "redrill_plan",
    "slot_of",
    "start_of",
    "story_push",
]
