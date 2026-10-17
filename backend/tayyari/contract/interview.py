"""Pydantic mirror of shared/interview.ts: practice the interview (I1).

Same rules as `tayyari.contract.discover`: camelCase field names that are the JSON keys, a field is optional
(``X | None = None``) exactly when the TypeScript field is ``x?:``, and `tests/unit/contract/test_interview_parity.py`
fails when the two drift.

What this contract carries (project-soul.md): questions come only from the person's evidence, a posting's asks and gaps,
or a work-sample template; a verdict is a word worked out in code from a quote of the person's own answer that code
checked, never a score; "Say" lines hold only the person's words and cited evidence, with ``[brackets]`` for what only
they know; Not assessed never counts against anyone. Nothing here is shared with an employer.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/interview.ts)

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from tayyari.contract.models import _Model

InterviewKind = Literal["role", "resume", "stories", "work"]
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
SourceOrigin = Literal["posting", "evidence"]

PROFILE_ID_MAX_CHARS: Final = 64
RUN_ID_MAX_CHARS: Final = 96
SLOT_ID_MAX_CHARS: Final = 16
MAX_SKIP: Final = 12
ANSWER_MAX_CHARS: Final = 6000
"""About six minutes of speech."""
WORK_MAX_CHARS: Final = 20_000
TEST_NOTE_MAX_CHARS: Final = 2000
ASSISTANT_MESSAGE_MAX_CHARS: Final = 1000


class RoleSource(_Model):
    runId: str = Field(max_length=RUN_ID_MAX_CHARS)
    jobId: str = Field(max_length=RUN_ID_MAX_CHARS)


class SlotSource(_Model):
    origin: SourceOrigin
    text: str
    source: str | None = None


class PlanSlot(_Model):
    id: str
    kind: SlotKind
    topic: str
    sources: list[SlotSource]


class PlanRequest(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    kind: InterviewKind
    role: RoleSource | None = None
    length: InterviewLength | None = None
    focus: InterviewFocus | None = None


class InterviewPlan(_Model):
    kind: InterviewKind
    title: str
    slots: list[PlanSlot]
    minutes: int
    role: RoleSource | None = None
    note: str | None = None
    family: str | None = None


class StartRequest(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    kind: InterviewKind
    role: RoleSource | None = None
    length: InterviewLength | None = None
    focus: InterviewFocus | None = None
    skip: list[str] | None = Field(default=None, max_length=MAX_SKIP)


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


class CheckResult(_Model):
    check: str
    verdict: Verdict
    quote: str | None = None


class VerdictCounts(_Model):
    defended: int
    shaky: int
    couldnt: int
    notAssessed: int


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
    task: WorkTask | None = None
    work: str | None = None


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


__all__ = [
    "ANSWER_MAX_CHARS",
    "ASSISTANT_MESSAGE_MAX_CHARS",
    "PROFILE_ID_MAX_CHARS",
    "RUN_ID_MAX_CHARS",
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
    "FixStep",
    "InterviewFocus",
    "InterviewKind",
    "InterviewLength",
    "InterviewLine",
    "InterviewPlan",
    "InterviewSession",
    "LineKind",
    "MaterialKind",
    "PlanRequest",
    "PlanSlot",
    "PracticeFact",
    "PracticeHistory",
    "Quote",
    "RoleSource",
    "Rung",
    "SayLine",
    "SessionRequest",
    "SessionStatus",
    "SessionSummary",
    "SessionView",
    "SlotKind",
    "SlotResult",
    "SlotSource",
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
