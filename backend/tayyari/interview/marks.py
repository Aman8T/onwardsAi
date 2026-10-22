"""Claim marks (I2): what a finished session leaves behind for Progress and re-drill, and the Fix steps placed from them.

    marks_of      one `StoredClaimMark` per ``ask``, ``claim`` or ``story`` slot of a debrief whose source is an evidence
                  line (each such slot has exactly one): the verdict, the rung that decided it (from the stored state;
                  ``surface`` when the state cannot be read), the question as first asked, the verdict's quote, the story
                  parts, and the slot's Fix step (Shaky and Couldn't defend only). Lengths are clipped to the columns.
    ensure_marks  the marks of every finished session that has none yet (a session from before marks, or an ``end`` whose
                  mark write failed). Writing twice adds nothing (`ClaimMarkRepo.add` skips a stored slot).
    fix_task_of   the `FixTask` of a mark whose Fix step is placed, else None.

No model, and the only I/O is through `ClaimMarkRepo`.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Final

from tayyari.contract.interview import Debrief, FixTask
from tayyari.core.logging import get_logger
from tayyari.core.text import clip
from tayyari.storage.ports import ClaimMarkRepo, StoredClaimMark, StoredInterview

from .history import CLAIM_KINDS, debrief_of
from .state import as_utc, read_state
from .text import fold

log = get_logger(__name__)

KEY_MAX: Final = 400
TEXT_MAX: Final = 400
SOURCE_MAX: Final = 120
QUESTION_MAX: Final = 200
QUOTE_MAX: Final = 300
FIX_TEXT_MAX: Final = 200


def claim_key(text: str) -> str:
    """A claim's key: the folded text of its evidence line, at most 400 characters."""
    return fold(text)[:KEY_MAX]


def marks_of(
    item: StoredInterview,
    debrief: Debrief,
    *,
    now: datetime,
    new_id: Callable[[], str] = lambda: secrets.token_hex(16),
) -> list[StoredClaimMark]:
    """The marks a finished session leaves (see the module docstring)."""
    state = read_state(item)
    rungs: dict[str, str] = {}
    if state is not None:
        for slot, st in zip(state.slots, state.slotStates, strict=True):
            best = st.best()
            rungs[slot.id] = best[0] if best is not None else (slot.start or "surface")
    day = as_utc(item.created_at).date()
    marks: list[StoredClaimMark] = []
    for result in debrief.results:
        if result.kind not in CLAIM_KINDS:
            continue
        source = next((s for s in result.sources if s.origin == "evidence"), None)
        if source is None or not source.text.strip():
            continue
        fix = result.fix if result.verdict in ("shaky", "couldnt") else None
        marks.append(
            StoredClaimMark(
                id=new_id(),
                profile_id=item.profile_id,
                claim_key=claim_key(source.text),
                claim_text=clip(source.text, TEXT_MAX),
                source=clip(source.source or "", SOURCE_MAX),
                session_id=item.id,
                slot_id=result.slotId,
                session_kind=debrief.kind,
                slot_kind=result.kind,
                verdict=result.verdict,
                rung=rungs.get(result.slotId, "surface"),
                question=clip(result.question, QUESTION_MAX),
                marked_on=day,
                created_at=now,
                quote=clip(result.quote.text, QUOTE_MAX) if result.quote is not None else None,
                story=result.story.model_dump() if result.story is not None else None,
                fix_text=clip(fix.text, FIX_TEXT_MAX) if fix is not None else None,
                fix_minutes=fix.minutes if fix is not None else None,
            )
        )
    return marks


async def write_marks(repo: ClaimMarkRepo, item: StoredInterview, debrief: Debrief, *, now: datetime) -> int:
    """Store a finished session's marks; how many were new. A failure is logged, never raised: the next history read writes
    them (`ensure_marks`)."""
    try:
        return await repo.add(marks_of(item, debrief, now=now))
    except Exception:
        log.warning("interview_marks_failed", session=item.id, exc_info=True)
        return 0


async def ensure_marks(
    repo: ClaimMarkRepo, profile_id: str, items: Sequence[StoredInterview], *, now: datetime
) -> int:
    """Write the marks of the finished sessions in ``items`` that have none yet; how many were new."""
    have = await repo.session_ids(profile_id)
    added = 0
    for item in items:
        if item.status != "done" or item.id in have or item.profile_id != profile_id:
            continue
        debrief = debrief_of(item)
        if debrief is not None:
            added += await write_marks(repo, item, debrief, now=now)
    return added


def fix_task_of(mark: StoredClaimMark) -> FixTask | None:
    """The Fix step of a mark, when it is placed (in a plan or kept in Practice)."""
    if mark.fix_text is None or mark.fix_added_at is None or (mark.fix_plan_id is None and not mark.fix_kept):
        return None
    return FixTask(
        id=mark.id,
        sessionId=mark.session_id,
        slotId=mark.slot_id,
        claimText=mark.claim_text,
        text=mark.fix_text,
        minutes=mark.fix_minutes or 15,
        where="plan" if mark.fix_plan_id is not None else "practice",
        planId=mark.fix_plan_id,
        addedAt=as_utc(mark.fix_added_at).isoformat(),
        doneAt=as_utc(mark.fix_done_at).isoformat() if mark.fix_done_at is not None else None,
    )


__all__ = ["claim_key", "ensure_marks", "fix_task_of", "marks_of", "write_marks"]
