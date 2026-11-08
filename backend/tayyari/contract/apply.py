"""Pydantic mirror of shared/apply.ts: auto-apply (match -> apply -> track -> learn).

Same rules as the other contracts: camelCase field names that are the JSON keys, a field is optional (``X | None = None``)
exactly when the TypeScript field is ``x?:`` (absent on output, never ``null``), and
`tests/unit/contract/test_apply_parity.py` fails when the two drift.

The whole feature sits behind ``AUTO_APPLY_ENABLED`` (off by default). Nothing is ever submitted to an employer without the
person's explicit approval of the exact values shown to them (`ApproveRequest.digest` must equal `ApplicationReview.digest`).

Signed-in only: every application, saved answer and resume belongs to one account (``u:<user id>``).
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

NEVER_STORED_KEYS: Final[frozenset[str]] = frozenset(
    {
        "pan",
        "aadhaar",
        "passportNumber",
        "idNumber",
        "bankAccount",
        "ifsc",
        "casteCategory",
        "religion",
        "maritalStatus",
        "fatherName",
        "signature",
        "declaration",
        "consent",
        "knowsSomeoneHere",
        "referral",
        "previousApplication",
    }
)
"""Question keys whose answers are never kept in the answer bank (spec 2.2 "What we leave to you"). ID numbers, caste or
category, religion, marital status and father's name are typed by the person on the employer's page (``onSite`` needs);
signatures, declarations and consent ticks are typed by the person in Review for one form only (``perOffer``). Python only
(the frontend gets the labels in `YourDetails.neverStored`); not ``APPLY_``-prefixed, because those are int limits."""

ApplicationStage = Literal[
    "queued",
    "filling",
    "waiting_for_you",
    "needs_signin",
    "approved",
    "submitting",
    "sent",
    "check_this_one",
    "failed",
    "closed",
    "cancelled",
    "viewed",
    "interviewing",
    "offer",
    "rejected",
]
"""Where an application stands (spec 3). ``waiting_for_you``: questions or the review wait for the person.
``needs_signin``: a login wall, a CAPTCHA or an emailed code (`StopReason` says which). ``approved``: the person approved
the review and the worker will submit it. ``submitting``: the send was committed before the click; only the engine leaves
it. ``check_this_one``: no clear outcome after the click, never retried."""

AtsKind = Literal["greenhouse", "lever", "ashby", "workday", "unknown"]
"""The applicant tracking system behind a job link."""

ApplySource = Literal["match", "discover"]
"""How the application started: a posting match (a pasted application link included), or a Discover listing (which runs
the match first)."""

StageSource = Literal["engine", "user", "email", "system"]
"""Who moved a stage: the apply engine, the person, an email they received, or the app itself."""

FieldKind = Literal[
    "text",
    "email",
    "tel",
    "url",
    "number",
    "date",
    "textarea",
    "select",
    "radio",
    "checkbox",
    "file",
    "combobox",
    "checkboxGroup",
]

FieldSource = Literal["details", "resume", "fact", "answers", "model", "user", "default"]
"""Where a filled value came from: the person's saved details (Your details or the first check), their resume, one of their
facts, an answer a form asked and they kept, a model answer grounded in their facts, what they typed just now, or a safe
default (an optional field, or "prefer not to say" for self-ID)."""

Confidence = Literal["high", "medium", "low"]

RunMode = Literal["playbook", "loop", "manual"]
"""How a run filled the form: a deterministic playbook (no model), the field-list loop (Haiku, then Sonnet), or not at all
(manual apply)."""

RunOutcome = Literal[
    "success", "questions", "needs_signin", "closed", "failed", "check_this_one", "interrupted", "deferred"
]
"""How one engine run ended: a review with nothing open (or a send that went through), a review with questions, a login
wall or a CAPTCHA, a closed posting, a failure before any click, no clear outcome after the click, a fill that stopped half
way (queued again or failed), or a run put back for capacity (a Browser Use 429 or the spend ceiling)."""

PlaybookStatus = Literal["active", "draft", "retired"]
PlaybookOrigin = Literal["builtin", "recorded", "manual"]

EmailStage = Literal["applied", "viewed", "interviewing", "offer", "rejected", "none"]
"""What an email from an employer says about an application (``none``: nothing that moves a stage)."""

StopReason = Literal[
    "questions", "signin", "challenge", "form_changed", "verification_code", "fill_error", "guard_probe"
]
"""Why a run stopped short of a review or a send: questions only the person can answer, a login wall, a CAPTCHA, a form
that changed since the review, an emailed verification code, a fill step that failed, or a guard probe that did not come
back blocked (nothing was filled)."""

ResumeChoice = Literal["tailored", "saved"]
"""Which resume goes with the application: the match's tailored resume (exported to PDF) or the person's saved file."""

CreditState = Literal["held", "charged", "released"]
"""The application's credit: held at start, charged when the application is sent, released when it was not."""

ApplyBlockReason = Literal[
    "signed_out",
    "no_credit",
    "closed",
    "not_fillable",
    "own_account_ats",
    "blocked_by_requirement",
    "already_applied",
    "manual_only",
    "unknown_liveness",
]
"""Why "Apply for me" is not offered (spec 2.1). ``unknown_liveness``: the posting could not be checked, so the card
offers the employer's site and never says closed."""

AnswerSource = Literal["resume", "first_check", "form", "you", "default"]
"""Where a kept answer came from: the resume, the 30-second first check, a form's question, Your details, or a default."""

AnswerGroup = Literal["you", "work", "education", "registrations", "where_how", "self_id"]
"""The Your details section an answer is shown under."""

ResolveOutcome = Literal["sent", "not_sent"]
"""The person's word on a ``check_this_one`` application: it went through (charged) or it did not (released)."""

ScriptOp = Literal["fill", "select", "check", "choose", "group"]
"""One step of a fill script: type into a field, pick a select option, tick a box, pick from a combobox or typeahead, or
pick in a radio or checkbox group. There is no upload step: the review leaves the file input empty, and the submit pass
attaches the resume itself after the ``submitting`` marker, once the guard is lifted for the form's upload hosts."""

ProgressState = Literal["done", "now", "todo"]


class ApplyStatus(_Model):
    enabled: bool
    signedIn: bool
    firstCheckDone: bool | None = None
    """The person did (or skipped) the 30-second first check."""
    consentAt: str | None = None
    """When the person agreed to what auto-apply sends and who processes it (spec 5); absent until they did."""
    price: int | None = None
    """Credits charged when an application is sent; absent while the price is not set."""
    freeLeft: int | None = None
    """Free applications this account has left."""


class FilledField(_Model):
    """One field of the form as it will be sent."""

    key: str
    label: str
    kind: FieldKind
    required: bool
    selector: str
    """The field's stable selector (id or name, never an nth-of-type path)."""
    value: str | None = None
    source: FieldSource | None = None
    profileKey: str | None = None
    confidence: Confidence | None = None
    factIds: list[str] | None = None
    optionValue: str | None = None
    """For a select, radio or combobox: the option's own value (``value`` is its visible text)."""
    options: list[str] | None = None
    """The visible options, so Review can show the chosen one among the others."""
    group: str | None = None
    """The form's own section heading (Personal information, Eligibility, ...)."""
    order: int | None = None
    """Position in the form, for showing fields in the form's order."""
    locked: bool | None = None
    """A value the person cannot change in Review (their name and email from the resume, a default)."""
    questionKey: str | None = None
    """The answer bank key this field was filled from or will be kept under."""
    attestation: bool | None = None
    """A signature, declaration or consent tick: typed by the person in Review, never kept."""


class AlsoWaiting(_Model):
    """Another waiting application that asks the same question."""

    applicationId: str
    company: str


class NeedsInput(_Model):
    """A field the engine will not fill on its own: no saved answer, a custom question, or a low-confidence answer."""

    key: str
    label: str
    kind: FieldKind
    required: bool
    reason: str
    questionKey: str
    """The answer bank key; one answer fills every waiting form that asks it."""
    options: list[str] | None = None
    suggestion: str | None = None
    factIds: list[str] | None = None
    perOffer: bool | None = None
    """Answered for this form only (expected CTC for this role, a declaration): the save box starts unticked."""
    onSite: bool | None = None
    """Typed by the person on the employer's page (an ID number, caste or category): never stored, and the application
    cannot be approved while a required one is open."""
    attestation: bool | None = None
    """A signature, declaration or consent tick the person gives in Review for this form only (never kept)."""
    requiredBy: str | None = None
    """The employer whose form asks it."""
    unit: str | None = None
    """The unit shown beside the input (``%``, ``lakh a year``)."""
    alsoWaiting: list[AlsoWaiting] | None = None
    """Other waiting applications that ask the same question."""


class CitedFact(_Model):
    """The text of a fact a model answer cites, copied at fill time (the application outlives the match)."""

    id: str
    text: str


class ReviewResume(_Model):
    """The resume that goes with the application. It is attached only when the person approves; ``sha256`` is part of the
    digest, so a changed resume needs a new approval."""

    name: str
    sha256: str
    choice: ResumeChoice


class ScriptStep(_Model):
    """One executable step of the review's fill script; the submit pass replays it with no model."""

    selector: str
    op: ScriptOp
    frame: int
    value: str | None = None
    optionValue: str | None = None


class ApplicationReview(_Model):
    """What will be sent, captured after the form was filled and before anything was submitted. ``digest`` is the sha256
    over the read-back form (stable key and value per field) and the resume's sha256; approving signs exactly this digest."""

    digest: str
    capturedAt: str
    mode: RunMode
    fields: list[FilledField]
    skipped: list[FilledField]
    warnings: list[str]
    playbookId: str | None = None
    submitLabel: str | None = None
    resume: ReviewResume | None = None
    facts: list[CitedFact] | None = None
    script: list[ScriptStep] | None = None
    model: str | None = None
    """The model that answered custom questions or ran the field-list loop, when one did."""


class ApplicationEvent(_Model):
    at: str
    kind: str
    source: StageSource
    fromStage: ApplicationStage | None = None
    toStage: ApplicationStage | None = None
    note: str | None = None


class ProgressSection(_Model):
    """One section of the form while it is being filled."""

    label: str
    state: ProgressState
    filled: int
    total: int
    waiting: int | None = None
    """Fields in this section that wait for the person."""
    note: str | None = None


class ApplyProgress(_Model):
    """How far a fill has got, written by the worker while the row is ``filling``."""

    sections: list[ProgressSection]
    filled: int
    total: int
    fields: list[FilledField] | None = None
    """The fields filled so far."""


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
    resumeChoice: ResumeChoice | None = None
    listingId: str | None = None
    creditState: CreditState | None = None
    stopReason: StopReason | None = None
    hasConfirmation: bool | None = None
    progress: ApplyProgress | None = None
    sentAt: str | None = None
    """When it went to the employer (the click that went through, or the person's word on it); absent before."""


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
    resumeChoice: ResumeChoice | None = None
    listingId: str | None = None
    creditState: CreditState | None = None
    stopReason: StopReason | None = None
    hasConfirmation: bool | None = None
    progress: ApplyProgress | None = None
    sentAt: str | None = None
    """When it went to the employer (as on `ApplicationSummary`)."""


class ApplicationList(_Model):
    applications: list[ApplicationSummary]


class ApplyCheckRequest(_Model):
    """The cheap, uncharged pre-check behind "Apply for me" (``POST /api/apply/check``)."""

    matchId: str | None = None
    listingId: str | None = None
    runId: str | None = None
    applyUrl: str | None = None


class ApplyCheck(_Model):
    """Whether "Apply for me" is offered for a posting, and why not."""

    offered: bool
    reason: ApplyBlockReason | None = None
    host: str | None = None
    ats: AtsKind | None = None
    note: str | None = None
    applicationId: str | None = None
    """The earlier application to the same posting (``already_applied``)."""
    gateReasons: list[str] | None = None
    """The match's hard requirements that blocked (``blocked_by_requirement``)."""
    applyUrl: str | None = None
    """The employer's own page for the posting, for "Open on <host>'s site" when it is not offered."""


class StartApplicationRequest(_Model):
    """Start an application (``POST /api/apply/applications``). The posting match always runs first, so ``matchId`` is
    required; ``listingId`` and ``runId`` name a Discover listing, ``applyUrl`` the form link of a pasted match."""

    matchId: str
    resume: ResumeChoice
    listingId: str | None = None
    runId: str | None = None
    applyUrl: str | None = None


class QuestionAnswer(_Model):
    questionKey: str
    value: str
    save: bool
    """Keep it in the answer bank for later forms."""


class AnswerRequest(_Model):
    answers: list[QuestionAnswer]


class AnswerResult(_Model):
    application: ApplicationDetail
    requeued: int
    """Other waiting applications this answer filled and queued again."""


class ApproveRequest(_Model):
    digest: str


class ResolveRequest(_Model):
    outcome: ResolveOutcome


class StageChangeRequest(_Model):
    stage: ApplicationStage
    note: str | None = None


class WorkAuthorization(_Model):
    """Work authorisation for one country (ISO 3166-1 alpha-2); never inferred from another country."""

    country: str
    authorised: str
    needsSponsorship: str | None = None


class SavedAnswer(_Model):
    """One answer in the answer bank, as Your details shows it."""

    questionKey: str
    question: str
    source: AnswerSource
    askEachTime: bool
    answeredAt: str
    value: str | None = None
    """Absent for an ask-each-time question with no answer yet, and for a private value."""
    group: AnswerGroup | None = None
    answeredOn: str | None = None
    """The employer whose form asked it."""
    confirmedAt: str | None = None
    stale: bool | None = None
    """Older than 90 days and not confirmed since: "Still true?"."""
    country: str | None = None
    private: bool | None = None
    """Saved but never shown back (current CTC)."""


class BankQuestion(_Model):
    """A common question not asked yet."""

    questionKey: str
    question: str
    group: AnswerGroup
    kind: FieldKind
    options: list[str] | None = None


class YourDetails(_Model):
    """Your details: every kept answer with its source and date, the questions not asked yet, and what is never stored."""

    answers: list[SavedAnswer]
    notAskedYet: list[BankQuestion]
    workAuthorization: list[WorkAuthorization]
    selfIdOnForms: bool
    neverStored: list[str]
    """The labels of what we leave to the person (ID numbers, caste or category, ...)."""
    hasResume: bool
    firstCheckDone: bool
    resumeName: str | None = None
    updatedAt: str | None = None


class SaveAnswerRequest(_Model):
    questionKey: str
    askEachTime: bool
    value: str | None = None


class FirstCheckRequest(_Model):
    """The 30-second first check. ``workAuthorization`` is for India only (the first check stores IN and nothing else);
    ``edits`` are corrections to what was read from the resume."""

    skipped: bool
    noticePeriod: str | None = None
    workAuthorization: WorkAuthorization | None = None
    edits: list[QuestionAnswer] | None = None


class SelfIdRequest(_Model):
    selfIdOnForms: bool


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


class StillTrueRequest(_Model):
    """ "Still true?" on a saved answer (``POST /api/apply/details/still-true``)."""

    questionKey: str


class MetricsRow(_Model):
    """One ATS's engine runs (fills and submits) in the window, by how they ended (`RunOutcome`)."""

    ats: str
    runs: int
    playbookRuns: int
    loopRuns: int
    """Runs the field-list loop filled (a model chose which saved value goes where)."""
    successes: int
    questions: int
    needsSignin: int
    closed: int
    failures: int
    checkThisOne: int
    interrupted: int
    deferred: int
    loopShare: float
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
    "NEVER_STORED_KEYS",
    "AlsoWaiting",
    "AnswerGroup",
    "AnswerRequest",
    "AnswerResult",
    "AnswerSource",
    "ApplicationDetail",
    "ApplicationEvent",
    "ApplicationList",
    "ApplicationReview",
    "ApplicationStage",
    "ApplicationSummary",
    "ApplyBlockReason",
    "ApplyCheck",
    "ApplyCheckRequest",
    "ApplyMetrics",
    "ApplyProgress",
    "ApplySource",
    "ApplyStatus",
    "ApproveRequest",
    "AtsKind",
    "BankQuestion",
    "CitedFact",
    "Confidence",
    "CreditState",
    "EmailClassification",
    "EmailClassifyRequest",
    "EmailStage",
    "FailureCount",
    "FieldKind",
    "FieldSource",
    "FilledField",
    "FirstCheckRequest",
    "MetricsRow",
    "NeedsInput",
    "PlaybookOrigin",
    "PlaybookStatus",
    "PlaybookSummary",
    "ProgressSection",
    "ProgressState",
    "QuestionAnswer",
    "ResolveOutcome",
    "ResolveRequest",
    "ResumeChoice",
    "ReviewResume",
    "RunMode",
    "RunOutcome",
    "SaveAnswerRequest",
    "SavedAnswer",
    "ScriptOp",
    "ScriptStep",
    "SelfIdRequest",
    "StageChangeRequest",
    "StageSource",
    "StartApplicationRequest",
    "StillTrueRequest",
    "StopReason",
    "WorkAuthorization",
    "YourDetails",
]
