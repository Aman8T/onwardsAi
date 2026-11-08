"""The application stage machine: which source may move an application from which stage to which (spec 3).

    engine   queued -> filling -> waiting_for_you | needs_signin | closed | failed | queued (an expired lease);
             approved -> submitting | waiting_for_you (the form changed) | failed (before the click);
             submitting -> sent | check_this_one | failed. Nothing but the engine (and a system repair) leaves
             submitting.
    user     cancel before submitting (queued, filling, waiting_for_you, approved, needs_signin, failed, closed ->
             cancelled); try again (failed, needs_signin, closed -> queued); answer the questions (waiting_for_you ->
             queued, the answered row and every other waiting row that asks the same question); approve
             (waiting_for_you -> approved); settle a check_this_one (-> sent, or -> failed); "I applied there"
             (needs_signin -> sent); track by hand from sent on (sent, viewed, interviewing, offer, rejected -> viewed,
             interviewing, offer, rejected)
    email    check_this_one -> sent; forward only: sent -> viewed -> interviewing -> offer; sent, viewed,
             interviewing -> rejected
    system   anything (data repair; reopening a cancelled, closed or failed row on a fresh start)

Approve (digest), resolve (check_this_one -> sent | failed) and "I applied there" have their own routes, so the generic
stage route refuses those targets (`ROUTED_TARGETS`).

`move` writes the change and its history entry together; a stage that changed under it (``expected_stage``) is a no-op.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any, Final, get_args

from tayyari.contract.apply import ApplicationStage, StageSource
from tayyari.storage.ports import ApplyRepo, StoredApplication, StoredApplicationEvent

STAGES: Final[tuple[str, ...]] = get_args(ApplicationStage)

SENT_AND_LATER: Final = frozenset({"sent", "viewed", "interviewing", "offer", "rejected"})
"""Stages the application reaches only after it went to the employer; tracking by hand starts here."""
TRACKING: Final = SENT_AND_LATER
"""Stages that track an application after it was sent (moving into one clears the approval)."""
USER_CANCELLABLE: Final = frozenset(
    {"queued", "filling", "waiting_for_you", "approved", "needs_signin", "failed", "closed"}
)
"""Stages the person may cancel from: everything before submitting that is not already over (a fill in progress
included: the worker's later move out of filling is a stale compare-and-set and does nothing)."""
USER_RETRYABLE: Final = frozenset({"failed", "needs_signin", "closed"})
"""Stages the person may queue again ("Try again"). An answered waiting_for_you row is also queued again by the
person, but that is the answers route, not "Try again"; and a waiting_for_you row whose only open needs are files
goes through the retry route as "Fill it again" (`apply.service._waits_only_for_files`)."""
ROUTED_TARGETS: Final = frozenset({"approved", "failed", "sent", "waiting_for_you"})
"""User moves only their own route makes: approve (with its digest), resolve and "I applied there". The generic stage
route refuses these targets so it cannot go around them."""
REOPENABLE: Final = frozenset({"cancelled", "closed", "failed"})
"""Stages a fresh start on the same posting reopens (a ``system`` move to queued with a new hold); any other stage means
the person already has that application."""

_ORDER: Final = {"sent": 0, "viewed": 1, "interviewing": 2, "offer": 3}

ENGINE: Final[dict[str, frozenset[str]]] = {
    "queued": frozenset({"filling"}),
    "filling": frozenset({"waiting_for_you", "needs_signin", "closed", "failed", "queued"}),
    "approved": frozenset({"submitting", "waiting_for_you", "failed"}),
    "submitting": frozenset({"sent", "check_this_one", "failed"}),
}


def _user(frm: str, to: str) -> bool:
    if to == "cancelled":
        return frm in USER_CANCELLABLE
    if to == "queued":
        # waiting_for_you -> queued is the answers requeue (spec 3.1 step 3), not "Try again"
        return frm in USER_RETRYABLE or frm == "waiting_for_you"
    if to == "approved":
        return frm == "waiting_for_you"
    if to == "sent":
        return frm in ("check_this_one", "needs_signin")
    if to == "failed":
        # resolve: "it did not go through" (the hold is given back, never a resend)
        return frm == "check_this_one"
    if to in SENT_AND_LATER:
        return frm in SENT_AND_LATER
    return False


def _email(frm: str, to: str) -> bool:
    if to == "sent":
        return frm == "check_this_one"
    if to == "rejected":
        return frm in ("sent", "viewed", "interviewing")
    return frm in _ORDER and to in _ORDER and _ORDER[to] > _ORDER[frm]


def allowed(frm: str, to: str, source: StageSource) -> bool:
    if to not in STAGES or frm not in STAGES or frm == to:
        return False
    if source == "system":
        return True
    if source == "engine":
        return to in ENGINE.get(frm, frozenset())
    if (
        frm == "submitting"
    ):  # only the engine leaves submitting: the send may already have reached the employer
        return False
    if source == "user":
        return _user(frm, to)
    return _email(frm, to)


class StageNotAllowed(ValueError):
    pass


def new_event_id() -> str:
    return "ev_" + secrets.token_hex(12)


async def move(
    repo: ApplyRepo,
    app: StoredApplication,
    to: str,
    *,
    source: StageSource,
    now: Any,
    note: str | None = None,
    changes: Mapping[str, Any] | None = None,
    kind: str = "stage",
) -> StoredApplication | None:
    """Move ``app`` to ``to`` (raises `StageNotAllowed` for a move ``source`` may not make), with ``changes`` in the
    same update and a history entry; None when the stage changed under us (another worker or request got there
    first)."""
    if not allowed(app.stage, to, source):
        raise StageNotAllowed(f"{source} cannot move an application from {app.stage} to {to}")
    updated = await repo.update_application(
        app.id, {**dict(changes or {}), "stage": to}, now=now, expected_stage=app.stage
    )
    if updated is not None:
        await repo.add_event(
            StoredApplicationEvent(
                id=new_event_id(), application_id=app.id, at=now, kind=kind, source=source, from_stage=app.stage,
                to_stage=to, detail={"note": note[:300]} if note else None,
            )
        )  # fmt: skip
    return updated


async def note(
    repo: ApplyRepo, app_id: str, text: str, *, source: StageSource, now: Any, kind: str = "note"
) -> None:
    await repo.add_event(
        StoredApplicationEvent(
            id=new_event_id(),
            application_id=app_id,
            at=now,
            kind=kind,
            source=source,
            detail={"note": text[:300]},
        )
    )


__all__ = [
    "ENGINE",
    "REOPENABLE",
    "ROUTED_TARGETS",
    "SENT_AND_LATER",
    "STAGES",
    "TRACKING",
    "USER_CANCELLABLE",
    "USER_RETRYABLE",
    "StageNotAllowed",
    "allowed",
    "move",
    "new_event_id",
    "note",
]
