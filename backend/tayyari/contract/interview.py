"""Pydantic mirror of shared/interview.ts: practice the interview (I1), and re-drilling weak claims (I2).

Same rules as `tayyari.contract.discover`: camelCase field names that are the JSON keys, a field is optional
(``X | None = None``) exactly when the TypeScript field is ``x?:``, and `tests/unit/contract/test_interview_parity.py`
fails when the two drift.

What this contract carries (project-soul.md): questions come only from the person's evidence, a posting's asks and gaps,
the requirements of a role on the person's graph, or a work-sample template; a verdict is a word worked out in code from a quote of the person's own answer that code
checked, never a score; "Say" lines hold only the person's words and cited evidence, with ``[brackets]`` for what only
they know; Not assessed never counts against anyone. Nothing here is shared with an employer.

A re-drill (I2) brings back the claims whose best verdict is Shaky or Couldn't defend, each starting where it wobbled; code
picks them, orders them and sets where each starts. A Fix step from a debrief can go into a Prepare plan or stay in
Practice (`FixTask`); ticking it never changes a verdict.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/interview.ts)

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, field_validator

from tayyari.contract.models import _Model

InterviewKind = Literal["role", "resume", "stories", "work", "redrill"]
SlotKind = Literal["ask", "gap", "claim", "story", "task"]
Verdict = Literal["defended", "shaky", "couldnt", "not_assessed"]
Rung = Literal["surface", "push", "floor"]
AnswerMode = Literal["speech", "typed"]
AnswerAction = Literal["answer", "dont_know", "skip", "repeat"]
InterviewLength = Literal["short", "full"]
InterviewFocus = Literal["gaps", "claims", "stories"]
SessionStatus = Literal["active", "done"]
LineKind = Literal["question", "followup", "floor", "done"]
Speaker = Literal["interviewer", "you"]
StoryPartName = Literal["situation", "action", "result", "reflection"]
MaterialKind = Literal["code", "table", "text"]
SourceOrigin = Literal["posting", "evidence", "role"]
FixWhere = Literal["plan", "practice"]

PROFILE_ID_MAX_CHARS: Final = 64
RUN_ID_MAX_CHARS: Final = 96
ROLE_ID_MAX_CHARS: Final = 64
"""A role id of the person's graph (`Profile.roles`): a slug of at most about 44 characters."""
SLOT_ID_MAX_CHARS: Final = 16
MAX_SKIP: Final = 12
ANSWER_MAX_CHARS: Final = 6000
"""About six minutes of speech."""
WORK_MAX_CHARS: Final = 20_000
TEST_NOTE_MAX_CHARS: Final = 2000
ASSISTANT_MESSAGE_MAX_CHARS: Final = 1000
SESSION_ID_MAX_CHARS: Final = 64
PLAN_ID_MAX_CHARS: Final = 64
"""`contract.prepare.PLAN_ID_MAX_CHARS`."""
FIX_ID_MAX_CHARS: Final = 64
CLAIM_KEY_MAX_CHARS: Final = 400
"""A claim's key: the folded text of its evidence line (`interview.text.fold`)."""
REDRILL_MAX: Final = 6
"""The most claims one re-drill asks ("All")."""
REDRILL_SHORT: Final = 3
"""How many a short re-drill asks, and the default when no claims are picked."""


def _bounded_keys(value: list[str] | None) -> list[str] | None:
    if value is not None and any(len(key) > CLAIM_KEY_MAX_CHARS for key in value):
        raise ValueError(f"a claim key is at most {CLAIM_KEY_MAX_CHARS} characters")
    return value


class RoleSource(_Model):
    runId: str = Field(max_length=RUN_ID_MAX_CHARS)
    jobId: str = Field(max_length=RUN_ID_MAX_CHARS)


class GraphRoleSource(_Model):
    roleId: str = Field(max_length=ROLE_ID_MAX_CHARS)


class SlotSource(_Model):
    origin: SourceOrigin
    text: str
    source: str | None = None


class SlotLast(_Model):
    verdict: Verdict
    on: str
    question: str
    quote: str | None = None
    rung: Rung


class PlanSlot(_Model):
    id: str
    kind: SlotKind
    topic: str
    sources: list[SlotSource]
    start: Rung | None = None
    last: SlotLast | None = None


class PlanRequest(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    kind: InterviewKind
    role: RoleSource | None = None
    graphRole: GraphRoleSource | None = None
    length: InterviewLength | None = None
    focus: InterviewFocus | None = None
    claims: list[str] | None = Field(default=None, max_length=REDRILL_MAX)

    _claim_keys = field_validator("claims")(_bounded_keys)


class InterviewPlan(_Model):
    kind: InterviewKind
    title: str
    slots: list[PlanSlot]
    minutes: int
    role: RoleSource | None = None
    graphRole: GraphRoleSource | None = None
    note: str | None = None
    family: str | None = None


class StartRequest(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    kind: InterviewKind
    role: RoleSource | None = None
    graphRole: GraphRoleSource | None = None
    length: InterviewLength | None = None
    focus: InterviewFocus | None = None
    skip: list[str] | None = Field(default=None, max_length=MAX_SKIP)
    claims: list[str] | None = Field(default=None, max_length=REDRILL_MAX)

    _claim_keys = field_validator("claims")(_bounded_keys)


class InterviewLine(_Model):
    kind: LineKind
    text: str
    lead: str | None = None
    slotId: str | None = None
    rung: Rung | None = None


class Turn(_Model):
    speaker: Speaker
    text: str
    at: int
    slotId: str | None = None
    rung: Rung | None = None
    mode: AnswerMode | None = None


class WorkTask(_Model):
    family: str
    familyLabel: str
    task: str
    materialName: str
    material: str
    materialKind: MaterialKind
    minutes: int
    assistant: bool


class AssistantTurn(_Model):
    you: str
    assistant: str


class InterviewSession(_Model):
    id: str
    profileId: str
    kind: InterviewKind
    title: str
    status: SessionStatus
    createdAt: str
    slots: list[PlanSlot]
    slotIndex: int
    current: InterviewLine | None = None
    turns: list[Turn]
    minutes: int
    role: RoleSource | None = None
    graphRole: GraphRoleSource | None = None
    task: WorkTask | None = None
    work: str | None = None
    testNote: str | None = None
    assistantTurns: list[AssistantTurn] | None = None


class AnswerRequest(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    text: str = Field(max_length=ANSWER_MAX_CHARS)
    mode: AnswerMode
    action: AnswerAction | None = None


class WorkSubmit(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    work: str = Field(max_length=WORK_MAX_CHARS)
    testNote: str | None = Field(default=None, max_length=TEST_NOTE_MAX_CHARS)


class AssistantRequest(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    message: str = Field(max_length=ASSISTANT_MESSAGE_MAX_CHARS)
    work: str | None = Field(default=None, max_length=WORK_MAX_CHARS)


class AssistantReply(_Model):
    reply: str
    asked: int
    left: int


class SessionRequest(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)


class AgainRequest(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    slotId: str = Field(max_length=SLOT_ID_MAX_CHARS)


class FactRequest(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    slotId: str = Field(max_length=SLOT_ID_MAX_CHARS)
    add: bool


class Quote(_Model):
    text: str
    at: int


class SayLine(_Model):
    text: str
    placeholders: list[str]
    sources: list[str]


class AvoidLine(_Model):
    quote: str
    advice: str


class FixStep(_Model):
    text: str
    minutes: int


class StoryParts(_Model):
    situation: bool
    action: bool
    result: bool
    reflection: bool


class PreviousMark(_Model):
    verdict: Verdict
    on: str


class SlotResult(_Model):
    slotId: str
    kind: SlotKind
    topic: str
    question: str
    sources: list[SlotSource]
    verdict: Verdict
    quote: Quote | None = None
    why: str
    say: SayLine | None = None
    avoid: AvoidLine | None = None
    fix: FixStep | None = None
    story: StoryParts | None = None
    newFact: str | None = None
    factAdded: bool | None = None
    previous: PreviousMark | None = None
    softer: SayLine | None = None


class CheckResult(_Model):
    check: str
    verdict: Verdict
    quote: str | None = None


class VerdictCounts(_Model):
    defended: int
    shaky: int
    couldnt: int
    notAssessed: int


class RedrillMoved(_Model):
    moved: int
    of: int


class Debrief(_Model):
    sessionId: str
    kind: InterviewKind
    title: str
    createdAt: str
    minutes: int
    counts: VerdictCounts
    firstThing: str | None = None
    firstSlotId: str | None = None
    summary: str | None = None
    results: list[SlotResult]
    checks: list[CheckResult] | None = None
    assistantUse: str | None = None
    transcript: list[Turn]
    keptUntil: str
    role: RoleSource | None = None
    graphRole: GraphRoleSource | None = None
    task: WorkTask | None = None
    work: str | None = None
    redrill: RedrillMoved | None = None


class SessionView(_Model):
    session: InterviewSession
    debrief: Debrief | None = None


class SessionSummary(_Model):
    id: str
    kind: InterviewKind
    title: str
    createdAt: str
    status: SessionStatus
    verdicts: list[Verdict]
    counts: VerdictCounts | None = None
    role: RoleSource | None = None
    graphRole: GraphRoleSource | None = None


class ClaimMark(_Model):
    sessionId: str
    date: str
    verdict: Verdict


class ClaimBest(_Model):
    text: str
    source: str
    best: Verdict
    on: str
    marks: list[ClaimMark]


class PracticeFact(_Model):
    text: str
    sessionId: str
    slotId: str
    at: int
    date: str


class PracticeHistory(_Model):
    sessions: list[SessionSummary]
    claims: list[ClaimBest]
    facts: list[PracticeFact]
    leftToday: int | None = None


# ───────────────────────── re-drill and Fix steps (I2) ─────────────────────────


class FixTask(_Model):
    id: str
    sessionId: str
    slotId: str
    claimText: str
    text: str
    minutes: int
    where: FixWhere
    planId: str | None = None
    addedAt: str
    doneAt: str | None = None


class LastMark(_Model):
    verdict: Verdict
    on: str
    rung: Rung


class RedrillClaim(_Model):
    key: str
    text: str
    source: str
    best: Verdict
    last: LastMark
    times: int
    start: Rung
    fix: FixTask | None = None


class RedrillView(_Model):
    claims: list[RedrillClaim]
    kept: list[FixTask]
    leftToday: int | None = None


class FixAdd(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    sessionId: str = Field(max_length=SESSION_ID_MAX_CHARS)
    slotId: str = Field(max_length=SLOT_ID_MAX_CHARS)
    planId: str | None = Field(default=None, max_length=PLAN_ID_MAX_CHARS)


class FixUpdate(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    done: bool


__all__ = [
    "ANSWER_MAX_CHARS",
    "ASSISTANT_MESSAGE_MAX_CHARS",
    "CLAIM_KEY_MAX_CHARS",
    "FIX_ID_MAX_CHARS",
    "PLAN_ID_MAX_CHARS",
    "PROFILE_ID_MAX_CHARS",
    "REDRILL_MAX",
    "REDRILL_SHORT",
    "ROLE_ID_MAX_CHARS",
    "RUN_ID_MAX_CHARS",
    "SESSION_ID_MAX_CHARS",
    "SLOT_ID_MAX_CHARS",
    "TEST_NOTE_MAX_CHARS",
    "WORK_MAX_CHARS",
    "AgainRequest",
    "AnswerAction",
    "AnswerMode",
    "AnswerRequest",
    "AssistantReply",
    "AssistantRequest",
    "AssistantTurn",
    "AvoidLine",
    "CheckResult",
    "ClaimBest",
    "ClaimMark",
    "Debrief",
    "FactRequest",
    "FixAdd",
    "FixStep",
    "FixTask",
    "FixUpdate",
    "FixWhere",
    "GraphRoleSource",
    "InterviewFocus",
    "InterviewKind",
    "InterviewLength",
    "InterviewLine",
    "InterviewPlan",
    "InterviewSession",
    "LastMark",
    "LineKind",
    "MaterialKind",
    "PlanRequest",
    "PlanSlot",
    "PracticeFact",
    "PracticeHistory",
    "PreviousMark",
    "Quote",
    "RedrillClaim",
    "RedrillMoved",
    "RedrillView",
    "RoleSource",
    "Rung",
    "SayLine",
    "SessionRequest",
    "SessionStatus",
    "SessionSummary",
    "SessionView",
    "SlotKind",
    "SlotLast",
    "SlotResult",
    "SlotSource",
    "SourceOrigin",
    "Speaker",
    "StartRequest",
    "StoryPartName",
    "StoryParts",
    "Turn",
    "Verdict",
    "VerdictCounts",
    "WorkSubmit",
    "WorkTask",
]
