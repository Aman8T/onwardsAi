"""A person's practice, newest first: their sessions, each claim at its best, and the facts they kept.

    sessions   one summary each: the verdicts in slot order (the pips) and the counts once it is done; none while active.
    claims     every evidence line asked about in an ask, claim or story slot of a finished session, keyed by its text, with
               a mark for each time (session, date, verdict). ``best`` counts: Not assessed is the lowest, so a claim that
               was Shaky can only get better; ``on`` is the first day the best verdict was reached.
    facts      what the person added from their answers ("From you"), with when it was said.

Given the profile's claim marks (I2, `claim_marks`), the claims are read from them instead, so a claim keeps its verdicts after
its session's transcript is purged; without them they are read from the stored debriefs, which are the same verdicts while
the sessions last. Either way no model is called.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from tayyari.contract.interview import (
    ClaimBest,
    ClaimMark,
    Debrief,
    PracticeFact,
    PracticeHistory,
    SessionSummary,
    Verdict,
)
from tayyari.core.logging import get_logger
from tayyari.storage.ports import StoredClaimMark, StoredInterview

from .state import RANK, as_utc, read_state
from .text import fold

log = get_logger(__name__)

CLAIM_KINDS = ("ask", "claim", "story")


@dataclass(slots=True)
class _Claim:
    text: str
    source: str
    marks: list[ClaimMark] = field(default_factory=list)


def debrief_of(item: StoredInterview) -> Debrief | None:
    """The debrief stored with a finished session, or None when there is none or it cannot be read (logged)."""
    if item.debrief is None:
        return None
    try:
        return Debrief.model_validate(item.debrief)
    except ValidationError:
        log.warning("interview_debrief_unreadable", session=item.id)
        return None


def best_mark(marks: Sequence[ClaimMark]) -> ClaimMark:
    """The mark with the best verdict; the earliest of them (marks are oldest first)."""
    best = marks[0]
    for mark in marks[1:]:
        if RANK[mark.verdict] > RANK[best.verdict]:
            best = mark
    return best


def _verdict(value: str) -> Verdict:
    return value if value in RANK else "not_assessed"  # type: ignore[return-value]


def build_history(
    items: Sequence[StoredInterview],
    *,
    marks: Sequence[StoredClaimMark] | None = None,
    left_today: int | None = None,
) -> PracticeHistory:
    """The history of ``items`` (a profile's sessions, newest first); the claims from ``marks`` (oldest first) when given."""
    sessions: list[SessionSummary] = []
    claims: dict[str, _Claim] = {}
    facts: list[PracticeFact] = []
    done: list[tuple[StoredInterview, Debrief]] = []
    for item in items:
        if item.status == "done":
            debrief = debrief_of(item)
            if debrief is None:
                continue
            done.append((item, debrief))
            sessions.append(
                SessionSummary(
                    id=item.id,
                    kind=debrief.kind,
                    title=debrief.title,
                    createdAt=as_utc(item.created_at).isoformat(),
                    status="done",
                    verdicts=[r.verdict for r in debrief.results],
                    counts=debrief.counts,
                    role=debrief.role,
                    graphRole=debrief.graphRole,
                )
            )
        else:
            state = read_state(item)
            if state is None:
                continue
            sessions.append(
                SessionSummary(
                    id=item.id,
                    kind=state.kind,
                    title=state.title,
                    createdAt=as_utc(item.created_at).isoformat(),
                    status="active",
                    verdicts=[],
                    role=state.role,
                    graphRole=state.graphRole,
                )
            )
    for item, debrief in reversed(done):
        date = as_utc(item.created_at).date().isoformat()
        for result in debrief.results:
            if result.factAdded and result.newFact:
                facts.append(
                    PracticeFact(
                        text=result.newFact,
                        sessionId=item.id,
                        slotId=result.slotId,
                        at=result.quote.at if result.quote is not None else 0,
                        date=date,
                    )
                )
            if marks is not None or result.kind not in CLAIM_KINDS:
                continue
            for source in result.sources:
                if source.origin != "evidence":
                    continue
                claim = claims.setdefault(fold(source.text), _Claim(source.text, source.source or ""))
                claim.marks.append(ClaimMark(sessionId=item.id, date=date, verdict=result.verdict))
    for stored in marks or ():
        claim = claims.setdefault(stored.claim_key, _Claim(stored.claim_text, stored.source))
        claim.marks.append(
            ClaimMark(
                sessionId=stored.session_id,
                date=stored.marked_on.isoformat(),
                verdict=_verdict(stored.verdict),
            )
        )
    ordered = sorted(claims.values(), key=lambda c: fold(c.text))
    ordered.sort(key=lambda c: c.marks[-1].date, reverse=True)
    best: list[ClaimBest] = []
    for claim in ordered:
        mark = best_mark(claim.marks)
        verdict: Verdict = mark.verdict
        best.append(
            ClaimBest(text=claim.text, source=claim.source, best=verdict, on=mark.date, marks=claim.marks)
        )
    facts.reverse()
    return PracticeHistory(sessions=sessions, claims=best, facts=facts, leftToday=left_today)


__all__ = ["build_history", "debrief_of"]
