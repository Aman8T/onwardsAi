"""After a release whose ``credit_state`` write-back missed: the row may have been reopened or tried again meanwhile.

A release runs after the move to failed, closed or cancelled has committed, and its write of ``credit_state='released'``
is a compare-and-set on that stage. When the write misses, the person pressed "Try again" or started the posting again
in between: that request saw ``credit_state='held'`` (or replayed the still-live hold, which is idempotent per ref), and
the release then gave that very hold back. `retake_if_reopened` takes it again, so the row never runs without a hold.
When the hold is refused (no credits left), a row that has not reached the click is moved to failed with nothing held;
one already sending is only logged (it is never stopped half way).
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from tayyari.apply import stages
from tayyari.apply.seams import ApplyCredits
from tayyari.core.logging import get_logger
from tayyari.storage.ports import ApplyRepo

log = get_logger(__name__)

HOLDING_STAGES: Final = frozenset(
    {"queued", "filling", "waiting_for_you", "approved", "needs_signin", "submitting"}
)
"""Stages that count on a live hold (the application is not over yet)."""
STOPPABLE_STAGES: Final = HOLDING_STAGES - {"submitting"}
NO_HOLD_NOTE: Final = "You have no applications left, so this one was stopped. Nothing was sent."


async def retake_if_reopened(
    repo: ApplyRepo, credits: ApplyCredits, application_id: str, user_id: str, ref: str, now: datetime
) -> bool:
    """Take the hold ``ref`` again when the row is live again and still says ``held`` under the same ref; True when the
    row now has its hold (or never needed one again)."""
    current = await repo.get_application(application_id, None)
    if (
        current is None
        or current.stage not in HOLDING_STAGES
        or current.credit_state != "held"
        or (current.credit_ref or f"apply:{current.id}") != ref
    ):
        return True
    try:
        await credits.hold(user_id, ref)
    except Exception as exc:
        log.error("apply_hold_retake_failed", application_id=application_id, error=type(exc).__name__)
        if current.stage in STOPPABLE_STAGES:
            await stages.move(
                repo, current, "failed", source="system", now=now, note=NO_HOLD_NOTE,
                changes={"credit_state": "released", "stage_reason": NO_HOLD_NOTE, "progress": None,
                         "approved_at": None, "approved_digest": None, "stop_reason": "fill_error"},
            )  # fmt: skip
        return False
    log.warning("apply_hold_retaken", application_id=application_id)
    return True


__all__ = ["HOLDING_STAGES", "NO_HOLD_NOTE", "STOPPABLE_STAGES", "retake_if_reopened"]
