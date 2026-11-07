"""The application stage machine: which source may move an application from which stage to which.

    engine   queued -> filling -> ready_to_submit | needs_user | failed;  ready_to_submit -> applied | needs_user | failed
    user     re-queue (needs_user, failed, ready_to_submit -> queued) and track by hand: any stage -> applied, viewed,
             interviewing, offer, rejected (the person knows best; they may have applied on the site themselves)
    email    forward only: (needs_user, failed, ready_to_submit) -> applied; applied -> viewed -> interviewing -> offer;
             applied, viewed, interviewing -> rejected
    system   anything (data repair)

`move` writes the change and its history entry together; a stage that changed under it (``expected_stage``) is a no-op.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any, Final, get_args

from tayyari.contract.apply import ApplicationStage, StageSource
from tayyari.storage.ports import ApplyRepo, StoredApplication, StoredApplicationEvent

STAGES: Final[tuple[str, ...]] = get_args(ApplicationStage)
TRACKING: Final = frozenset({"applied", "viewed", "interviewing", "offer", "rejected"})
_ORDER: Final = {"applied": 0, "viewed": 1, "interviewing": 2, "offer": 3}

ENGINE: Final[dict[str, frozenset[str]]] = {
    "queued": frozenset({"filling", "needs_user", "failed"}),
    "filling": frozenset({"ready_to_submit", "needs_user", "failed", "queued"}),
    "ready_to_submit": frozenset({"applied", "needs_user", "failed"}),
}


def allowed(frm: str, to: str, source: StageSource) -> bool:
    if to not in STAGES or frm not in STAGES or frm == to:
        return False
    if source == "system":
        return True
    if source == "engine":
        return to in ENGINE.get(frm, frozenset())
    if source == "user":
        return to in TRACKING or (to == "queued" and frm in ("needs_user", "failed", "ready_to_submit"))
    # email
    if to == "applied":
        return frm in ("needs_user", "failed", "ready_to_submit")
    if to == "rejected":
        return frm in ("applied", "viewed", "interviewing")
    return frm in _ORDER and to in _ORDER and _ORDER[to] > _ORDER[frm]


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
    """Move ``app`` to ``to`` (raises `StageNotAllowed` for a move ``source`` may not make), with ``changes`` in the same
    update and a history entry; None when the stage changed under us (another worker or request got there first)."""
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


__all__ = ["ENGINE", "STAGES", "TRACKING", "StageNotAllowed", "allowed", "move", "new_event_id", "note"]
