"""What a stored session holds: everything needed to carry on, in the one JSON document of `StoredInterview.state`.

The public `InterviewSession` the browser sees is a view of it; the parts below never leave the server before the debrief:
the hidden notes of what a full answer covers, the easier and the follow-up questions, each slot's per-rung judgements,
and a work sample's checks.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they sit beside the contract's camelCase models in one JSON)

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tayyari.contract.interview import (
    AssistantTurn,
    InterviewKind,
    InterviewLine,
    InterviewSession,
    PlanSlot,
    Quote,
    RoleSource,
    Rung,
    StoryParts,
    Turn,
    Verdict,
    WorkTask,
)
from tayyari.core.logging import get_logger
from tayyari.storage.ports import StoredInterview

log = get_logger(__name__)

STATE_VERSION: Final = 1

RANK: Final[dict[str, int]] = {"defended": 3, "shaky": 2, "couldnt": 1, "not_assessed": 0}
"""Verdicts best first; Not assessed is the lowest and never counts against anyone."""


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RungResult(_Base):
    """What one answer to one rung came to, after the code checks."""

    verdict: Verdict
    quote: Quote | None = None
    why: str = ""
    story: StoryParts | None = None
    newFact: str | None = None
    factAdded: bool | None = None


class SlotState(_Base):
    """One slot as it runs: the phrased lines, the hidden notes, and how far it got."""

    id: str
    covers: list[str] = Field(default_factory=list)
    question: str
    push: str = ""
    floor: str = ""
    needsAssistant: bool = False
    reached: bool = False
    skipped: bool = False
    drifted: bool = False
    disputed: bool = False
    rungs: dict[Rung, RungResult] = Field(default_factory=dict)

    def best(self) -> tuple[Rung, RungResult] | None:
        """The rung that decides the slot: the best verdict, then one that stated a new fact, then the later rung (it adds the
        most). None when nothing was judged."""
        order: tuple[Rung, ...] = ("surface", "push", "floor")
        found = [
            (RANK[r.verdict], r.newFact is not None, i, rung, r)
            for i, rung in enumerate(order)
            if (r := self.rungs.get(rung))
        ]
        if not found:
            return None
        _, _, _, rung, result = max(found, key=lambda item: item[:3])
        return rung, result

    def verdict(self) -> Verdict:
        found = self.best()
        return found[1].verdict if found is not None else "not_assessed"

    def story_parts(self) -> StoryParts:
        """A story part counts when any rung had it."""
        parts = [r.story for r in self.rungs.values() if r.story is not None]
        return StoryParts(
            situation=any(p.situation for p in parts),
            action=any(p.action for p in parts),
            result=any(p.result for p in parts),
            reflection=any(p.reflection for p in parts),
        )


class SessionState(_Base):
    schemaVersion: int = STATE_VERSION
    kind: InterviewKind
    title: str
    minutes: int
    slots: list[PlanSlot]
    slotStates: list[SlotState]
    slotIndex: int = 0
    current: InterviewLine | None = None
    turns: list[Turn] = Field(default_factory=list)
    answered: int = 0
    role: RoleSource | None = None
    employer: str | None = None
    task: WorkTask | None = None
    checks: list[str] = Field(default_factory=list)
    work: str | None = None
    testNote: str | None = None
    assistant: list[AssistantTurn] = Field(default_factory=list)
    againOf: str | None = None

    def slot(self) -> tuple[PlanSlot, SlotState] | None:
        """The slot being asked, or None before a work sample's work is in (its first line is the task) and after the last."""
        current = self.current
        if current is None or current.kind == "done" or current.slotId is None:
            return None
        if 0 <= self.slotIndex < len(self.slots):
            return self.slots[self.slotIndex], self.slotStates[self.slotIndex]
        return None

    @property
    def over(self) -> bool:
        return self.current is None or self.current.kind == "done"

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def read_state(item: StoredInterview) -> SessionState | None:
    """The session state of a stored row, or None when it cannot be read (logged; the caller treats it as gone)."""
    try:
        return SessionState.model_validate(item.state)
    except ValidationError:
        log.warning("interview_state_unreadable", session=item.id)
        return None


def session_of(item: StoredInterview, state: SessionState) -> InterviewSession:
    """The session the browser sees: no hidden notes, no per-rung judgements."""
    return InterviewSession(
        id=item.id,
        profileId=item.profile_id,
        kind=state.kind,
        title=state.title,
        status="done" if item.status == "done" else "active",
        createdAt=as_utc(item.created_at).isoformat(),
        slots=state.slots,
        slotIndex=state.slotIndex,
        current=state.current if item.status != "done" else None,
        turns=state.turns,
        minutes=state.minutes,
        role=state.role,
        task=state.task,
        work=state.work,
        testNote=state.testNote,
        assistantTurns=state.assistant if state.kind == "work" else None,
    )


def as_utc(value: datetime) -> datetime:
    """``value`` as a timezone-aware UTC time (a naive one is taken to be UTC already)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def seconds_since(start: datetime, now: datetime) -> int:
    """Whole seconds from ``start`` to ``now`` (never negative)."""
    return max(0, int((as_utc(now) - as_utc(start)).total_seconds()))


__all__ = [
    "RANK",
    "STATE_VERSION",
    "RungResult",
    "SessionState",
    "SlotState",
    "as_utc",
    "read_state",
    "seconds_since",
    "session_of",
]
