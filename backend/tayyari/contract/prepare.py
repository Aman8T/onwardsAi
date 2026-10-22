"""Pydantic mirror of shared/prepare.ts: "How do I prepare for this role?" answered with typed blocks.

Same rules as the other contracts: camelCase field names that are the JSON keys, a field is optional
(``X | None = None``) exactly when the TypeScript field is ``x?:``, and `tests/unit/contract/test_prepare_parity.py`
fails when the two drift. Code supplies every number (the gaps from ``Profile.roles``, the weeks from the hours a week);
the model writes the headline, the "why" lines and the moves, each citing ``PrepareEvidence`` ids, and code checks them.

A plan also shows the Fix steps from practice placed in it (I2, ``practice``): they are stored on the claim's mark
(`claim_marks`), not in `PrepareState`, so a tick has one home wherever the step went.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/prepare.ts)

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field, field_validator

from tayyari.contract.interview import FixTask
from tayyari.contract.models import _Model

PrepareScope = Literal["role", "requirement"]

PROFILE_ID_MAX_CHARS: Final = 64
PLAN_ID_MAX_CHARS: Final = 64
ROLE_ID_MAX_CHARS: Final = 120
REQUIREMENT_MAX_CHARS: Final = 120
LINK_MAX_CHARS: Final = 500
MAX_DONE: Final = 200
TASK_KEY_MAX_CHARS: Final = 24
MAX_LINKS: Final = 20
MIN_HOURS: Final = 1
MAX_HOURS: Final = 40
MAX_MOVES: Final = 3
DEFAULT_HOURS_PER_WEEK: Final = 6


class PrepareRequest(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    roleId: str = Field(max_length=ROLE_ID_MAX_CHARS)
    requirement: str | None = Field(default=None, max_length=REQUIREMENT_MAX_CHARS)
    fresh: bool | None = None


class PrepareEvidence(_Model):
    id: str
    sourceId: str
    sourceKind: str
    sourceLabel: str
    skill: str
    text: str


class PrepareGap(_Model):
    name: str
    skillId: str | None = None
    current: int
    target: int
    biggest: bool
    why: str | None = None
    evidenceIds: list[str] | None = None


class PrepareStrong(_Model):
    name: str
    current: int
    target: int


class PrepareCited(_Model):
    text: str
    evidenceIds: list[str]


class PreparePhase(_Model):
    title: str
    hours: int = Field(ge=MIN_HOURS, le=MAX_HOURS)
    tasks: list[str]
    deliverable: str


class PrepareMove(_Model):
    id: str
    title: str
    body: str
    have: list[PrepareCited]
    works: list[str]
    phases: list[PreparePhase]


class PrepareLink(_Model):
    moveId: str = Field(max_length=8)
    phaseIndex: int = Field(ge=0, le=9)
    url: str = Field(max_length=LINK_MAX_CHARS)
    addedAt: str = Field(max_length=40)


class PrepareState(_Model):
    hoursPerWeek: int = Field(ge=MIN_HOURS, le=MAX_HOURS)
    moveIndex: int = Field(ge=0, lt=MAX_MOVES)
    done: list[str] = Field(max_length=MAX_DONE)
    links: list[PrepareLink] = Field(max_length=MAX_LINKS)

    @field_validator("done")
    @classmethod
    def _short_task_keys(cls, value: list[str]) -> list[str]:
        if any(len(key) > TASK_KEY_MAX_CHARS for key in value):
            raise ValueError(f"a done task key is at most {TASK_KEY_MAX_CHARS} characters")
        return value


class PreparePlan(_Model):
    id: str
    profileId: str
    roleId: str
    roleTitle: str
    scope: PrepareScope
    requirement: str | None = None
    rank: int
    roleCount: int
    eta: str
    createdAt: str
    headline: str | None = None
    gaps: list[PrepareGap]
    strong: list[PrepareStrong]
    evidence: list[PrepareEvidence]
    moves: list[PrepareMove]
    state: PrepareState
    version: int
    practice: list[FixTask] | None = None


class PreparePlanSummary(_Model):
    id: str
    roleId: str
    roleTitle: str
    requirement: str | None = None
    createdAt: str


# ───────────────────────── stream events ─────────────────────────


class PrepareGapsEvent(_Model):
    type: Literal["gaps"] = "gaps"
    plan: PreparePlan


class PrepareStandingEvent(_Model):
    type: Literal["standing"] = "standing"
    headline: str | None = None
    gaps: list[PrepareGap]


class PrepareMovesEvent(_Model):
    type: Literal["moves"] = "moves"
    moves: list[PrepareMove]


class PrepareDoneEvent(_Model):
    type: Literal["done"] = "done"
    plan: PreparePlan


class PrepareErrorEvent(_Model):
    type: Literal["error"] = "error"
    message: str
    code: str | None = None


PrepareEvent = Annotated[
    PrepareGapsEvent | PrepareStandingEvent | PrepareMovesEvent | PrepareDoneEvent | PrepareErrorEvent,
    Field(discriminator="type"),
]
PREPARE_TERMINAL: Final = (PrepareDoneEvent, PrepareErrorEvent)


# ───────────────────────── the other routes ─────────────────────────


class PrepareStateUpdate(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    version: int = Field(ge=0)
    state: PrepareState


class PrepareStateSaved(_Model):
    state: PrepareState
    version: int


class PrepareRepo(_Model):
    name: str
    url: str | None = None
    language: str | None = None
    description: str | None = None
    tests: bool
    ci: bool
    readme: bool
    reasons: list[str]
    suggested: bool


class PrepareRepos(_Model):
    github: bool
    repos: list[PrepareRepo]


class PrepareHeadlineRequest(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)


class PrepareHeadline(_Model):
    current: str
    proposed: str | None = None
    evidenceIds: list[str]
    note: str | None = None


__all__ = [
    "DEFAULT_HOURS_PER_WEEK",
    "LINK_MAX_CHARS",
    "MAX_DONE",
    "MAX_HOURS",
    "MAX_LINKS",
    "MAX_MOVES",
    "MIN_HOURS",
    "PLAN_ID_MAX_CHARS",
    "PREPARE_TERMINAL",
    "PROFILE_ID_MAX_CHARS",
    "REQUIREMENT_MAX_CHARS",
    "ROLE_ID_MAX_CHARS",
    "TASK_KEY_MAX_CHARS",
    "PrepareCited",
    "PrepareDoneEvent",
    "PrepareErrorEvent",
    "PrepareEvent",
    "PrepareEvidence",
    "PrepareGap",
    "PrepareGapsEvent",
    "PrepareHeadline",
    "PrepareHeadlineRequest",
    "PrepareLink",
    "PrepareMove",
    "PrepareMovesEvent",
    "PreparePhase",
    "PreparePlan",
    "PreparePlanSummary",
    "PrepareRepo",
    "PrepareRepos",
    "PrepareRequest",
    "PrepareScope",
    "PrepareStandingEvent",
    "PrepareState",
    "PrepareStateSaved",
    "PrepareStateUpdate",
]
