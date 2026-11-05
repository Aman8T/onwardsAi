"""Pydantic mirror of shared/apply.ts: auto-apply (match -> apply -> track -> learn).

Same rules as the other contracts: camelCase field names that are the JSON keys, a field is optional (``X | None = None``)
exactly when the TypeScript field is ``x?:`` (absent on output, never ``null``), and
`tests/unit/contract/test_apply_parity.py` fails when the two drift.

The whole feature sits behind ``AUTO_APPLY_ENABLED`` (off by default). Nothing is ever submitted to an employer without the
person's explicit approval of the exact values shown to them (`ApproveRequest.digest` must equal `ApplicationReview.digest`).
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/apply.ts)

from __future__ import annotations

from typing import Final, Literal

from tayyari.contract.models import _Model

APPLY_URL_MAX_CHARS: Final = 2000
APPLY_ANSWER_MAX_CHARS: Final = 4000
APPLY_NOTE_MAX_CHARS: Final = 500
APPLY_EMAIL_MAX_CHARS: Final = 20000
APPLY_DETAIL_MAX_CHARS: Final = 300
APPLY_RESUME_MAX_BYTES: Final = 5 * 1024 * 1024

ApplicationStage = Literal[
    "queued",
    "filling",
    "needs_user",
    "ready_to_submit",
    "applied",
    "viewed",
    "interviewing",
    "offer",
    "rejected",
    "failed",
]
"""Where an application stands. ``ready_to_submit`` with ``approvedAt`` set means the person approved and the worker is
submitting it; nothing reaches the employer before that."""

AtsKind = Literal["greenhouse", "lever", "ashby", "workday", "unknown"]
"""The applicant tracking system behind a job link. Workday and unknown go straight to the AI fallback."""

ApplySource = Literal["match", "link"]
"""How the application started: approving a posting match, or pasting a job link."""

StageSource = Literal["engine", "user", "email", "system"]
"""Who moved a stage: the apply engine, the person, an email they received, or the app itself."""

FieldKind = Literal[
    "text", "email", "tel", "url", "number", "date", "textarea", "select", "radio", "checkbox", "file"
]

FieldSource = Literal["details", "resume", "answers", "model", "agent", "user", "default"]
"""Where a filled value came from: the person's saved details, their resume file, an answer they saved before, a model
answer grounded in their facts, the AI browser agent, what they typed just now, or a safe default (an optional field)."""

Confidence = Literal["high", "medium", "low"]

RunMode = Literal["playbook", "ai", "manual"]
"""How a run filled the form: a deterministic playbook (no model), the AI browser agent, or not at all (manual apply)."""

RunOutcome = Literal["success", "needs_user", "failed", "blocked"]

PlaybookStatus = Literal["active", "draft", "retired"]
PlaybookOrigin = Literal["builtin", "recorded", "manual"]

EmailStage = Literal["applied", "viewed", "interviewing", "offer", "rejected", "none"]
"""What an email from an employer says about an application (``none``: nothing that moves a stage)."""


class ApplyStatus(_Model):
    enabled: bool
    signedIn: bool
    anonymous: bool
    """True when this visitor may use auto-apply without an account (no sign-in provider is configured)."""
    agents: list[str]
    """The AI fallback adapters this server has keys for (``gemini``, ``claude``), in the order they are tried."""


class ApplicantDetails(_Model):
    """What the person saved once for every application. Every field is theirs, typed by them; nothing here is inferred."""

    firstName: str | None = None
    lastName: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    currentCompany: str | None = None
    currentTitle: str | None = None
    linkedin: str | None = None
    github: str | None = None
    website: str | None = None
    workAuthorization: str | None = None
    needsSponsorship: str | None = None
    willingToRelocate: str | None = None
    noticePeriod: str | None = None
    salaryExpectation: str | None = None
    startDate: str | None = None
    howDidYouHear: str | None = None
    pronouns: str | None = None
    eeoGender: str | None = None
    eeoRace: str | None = None
    eeoVeteran: str | None = None
    eeoDisability: str | None = None


class ApplicantProfile(_Model):
    details: ApplicantDetails
    hasResume: bool
    savedAnswers: int
    resumeName: str | None = None
    updatedAt: str | None = None


class ApplicantProfileUpdate(_Model):
    details: ApplicantDetails


class FilledField(_Model):
    """One field of the form as it will be sent."""

    key: str
    label: str
    kind: FieldKind
    required: bool
    value: str | None = None
    source: FieldSource | None = None
    profileKey: str | None = None
    confidence: Confidence | None = None
    factIds: list[str] | None = None


class NeedsInput(_Model):
    """A field the engine will not fill on its own: no saved answer, a custom question, or a low-confidence answer."""

    key: str
    label: str
    kind: FieldKind
    required: bool
    reason: str
    options: list[str] | None = None
    suggestion: str | None = None
    factIds: list[str] | None = None


class ApplicationReview(_Model):
    """What will be sent, captured after the form was filled and before anything was submitted. ``digest`` is the sha256
    of the fill plan (selectors and values); approving signs exactly this digest."""

    digest: str
    capturedAt: str
    mode: RunMode
    fields: list[FilledField]
    skipped: list[FilledField]
    warnings: list[str]
    playbookId: str | None = None
    agent: str | None = None
    submitLabel: str | None = None


class ApplicationEvent(_Model):
    at: str
    kind: str
    source: StageSource
    fromStage: ApplicationStage | None = None
    toStage: ApplicationStage | None = None
    note: str | None = None


class ApplicationSummary(_Model):
    id: str
    stage: ApplicationStage
    source: ApplySource
    ats: AtsKind
    domain: str
    jobUrl: str
    createdAt: str
    updatedAt: str
    needsCount: int
    hasScreenshot: bool
    company: str | None = None
    title: str | None = None
    location: str | None = None
    matchId: str | None = None
    approvedAt: str | None = None
    stageReason: str | None = None


class ApplicationDetail(_Model):
    id: str
    stage: ApplicationStage
    source: ApplySource
    ats: AtsKind
    domain: str
    jobUrl: str
    createdAt: str
    updatedAt: str
    needsCount: int
    hasScreenshot: bool
    needs: list[NeedsInput]
    events: list[ApplicationEvent]
    company: str | None = None
    title: str | None = None
    location: str | None = None
    matchId: str | None = None
    approvedAt: str | None = None
    stageReason: str | None = None
    applyUrl: str | None = None
    manualUrl: str | None = None
    description: str | None = None
    submittedAt: str | None = None
    review: ApplicationReview | None = None


class ApplicationList(_Model):
    applications: list[ApplicationSummary]


class CreateApplicationRequest(_Model):
    url: str
    matchId: str | None = None
    profileId: str | None = None


class NeedAnswer(_Model):
    key: str
    value: str


class AnswerNeedsRequest(_Model):
    answers: list[NeedAnswer]
    remember: bool


class ApproveRequest(_Model):
    digest: str


class StageChangeRequest(_Model):
    stage: ApplicationStage
    note: str | None = None


class EmailClassifyRequest(_Model):
    """An email the person pasted (or, later, one read from their mailbox). The body is classified and dropped: it is
    never stored or logged."""

    subject: str
    sender: str
    body: str
    applicationId: str | None = None


class EmailClassification(_Model):
    stage: EmailStage
    confidence: Confidence
    applied: bool
    """True when an application's stage was changed because of this email."""
    quote: str | None = None
    """The sentence of the email the classification rests on (checked to be in the email)."""
    applicationId: str | None = None
    note: str | None = None


class MetricsRow(_Model):
    ats: str
    runs: int
    playbookRuns: int
    aiRuns: int
    successes: int
    needsUser: int
    failures: int
    blocked: int
    aiShare: float
    successRate: float
    avgSteps: float
    costMicroUsd: int


class FailureCount(_Model):
    reason: str
    count: int


class PlaybookSummary(_Model):
    id: str
    ats: str
    status: PlaybookStatus
    origin: PlaybookOrigin
    version: int
    successes: int
    failures: int
    updatedAt: str
    domain: str | None = None


class ApplyMetrics(_Model):
    since: str
    total: MetricsRow
    byAts: list[MetricsRow]
    failureReasons: list[FailureCount]
    playbooks: list[PlaybookSummary]


__all__ = [
    "APPLY_ANSWER_MAX_CHARS",
    "APPLY_DETAIL_MAX_CHARS",
    "APPLY_EMAIL_MAX_CHARS",
    "APPLY_NOTE_MAX_CHARS",
    "APPLY_RESUME_MAX_BYTES",
    "APPLY_URL_MAX_CHARS",
    "AnswerNeedsRequest",
    "ApplicantDetails",
    "ApplicantProfile",
    "ApplicantProfileUpdate",
    "ApplicationDetail",
    "ApplicationEvent",
    "ApplicationList",
    "ApplicationReview",
    "ApplicationStage",
    "ApplicationSummary",
    "ApplyMetrics",
    "ApplySource",
    "ApplyStatus",
    "ApproveRequest",
    "AtsKind",
    "Confidence",
    "CreateApplicationRequest",
    "EmailClassification",
    "EmailClassifyRequest",
    "EmailStage",
    "FailureCount",
    "FieldKind",
    "FieldSource",
    "FilledField",
    "MetricsRow",
    "NeedAnswer",
    "NeedsInput",
    "PlaybookOrigin",
    "PlaybookStatus",
    "PlaybookSummary",
    "RunMode",
    "RunOutcome",
    "StageChangeRequest",
    "StageSource",
]
