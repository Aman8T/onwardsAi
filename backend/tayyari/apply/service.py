"""What the auto-apply routes call (spec 3.1): the start checks, the start with its credit hold, the shared answer bank,
approve, cancel, resolve and "I applied there", manual tracking, and Your details.

Signed-in only (CLAUDE.md rule 9): every method takes the owner key ``u:<user id>`` and refuses anything else, and
every read is scoped by it, so another account's application reads as unknown.

    check     the start checks in `apply.checks.evaluate_start`'s order, with no side effect: no row, no hold, no
              charge. A manual-only site and an ATS we do not fill are never fetched; the liveness read runs only once
              those pass.
    create    the same checks again, then the daily cap, then the hold ``apply:<id>`` (unlimited accounts skip it),
              then the row ``queued`` (or a cancelled, closed or failed row of the same posting reopened). A failed
              insert gives the hold back; one that lost the race to the same posting raises `PostingTaken`.
    answer    a batch. ID numbers (by key or by shape) are refused. A saved answer goes into the answer bank, and every
              other waiting application of the person that asks the same question gets it and is queued again
              (per-offer questions and work-authorisation questions about an unknown country are never shared). Only a
              question the application asks (a need, or a field of its review) is taken. Allowlisted answers become
              facts "from you" through `FactSink`.
    approve   only the review's digest, with no required question open (one typed on the employer's page included).
    cancel    any stage before submitting; the hold is given back.
    resolve   a ``check_this_one``: sent charges the hold; not sent gives it back and marks the row failed.

Code computes, models classify (CLAUDE.md rule 2): nothing here calls a model, and every sentence written into the
person's facts is built in code.
"""

from __future__ import annotations

import hashlib
import io
import re
import secrets
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final, Literal, get_args

from pydantic import ValidationError

from tayyari.apply import stages, wake
from tayyari.apply.answers import COUNTRY_NAMES
from tayyari.apply.checks import OWN_ACCOUNT_ATS, evaluate_start, fillable, reopens
from tayyari.apply.holds import retake_if_reopened
from tayyari.apply.intake import (
    Fingerprint,
    JobInfo,
    Liveness,
    PageFetcher,
    StatusFetcher,
    check_live,
    fingerprint_url,
    host_of,
    intake,
    is_manual_only,
    normalize_url,
    posting_key,
    same_posting,
    site_key,
)
from tayyari.apply.playbook import COUNTRY_KEYS, ON_SITE_KEYS, SELF_ID_KEYS
from tayyari.apply.seams import ApplyCredits, FactSink
from tayyari.contract.apply import (
    APPLY_ANSWER_MAX_CHARS,
    APPLY_DETAIL_MAX_CHARS,
    APPLY_NOTE_MAX_CHARS,
    APPLY_RESUME_MAX_BYTES,
    APPLY_URL_MAX_CHARS,
    AlsoWaiting,
    AnswerGroup,
    AnswerRequest,
    AnswerResult,
    AnswerSource,
    ApplicationDetail,
    ApplicationEvent,
    ApplicationList,
    ApplicationReview,
    ApplicationSummary,
    ApplyCheck,
    ApplyProgress,
    ApplyStatus,
    AtsKind,
    BankQuestion,
    FieldKind,
    FirstCheckRequest,
    NeedsInput,
    QuestionAnswer,
    ResolveOutcome,
    ResumeChoice,
    SaveAnswerRequest,
    SavedAnswer,
    StartApplicationRequest,
    WorkAuthorization,
    YourDetails,
)
from tayyari.core.clock import Clock
from tayyari.core.errors import (
    AppError,
    BadRequest,
    Conflict,
    NotFound,
    PayloadTooLarge,
    RateLimited,
    Unauthorized,
)
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings
from tayyari.storage.ports import (
    ACCOUNT_OWNER_PREFIX,
    ApplyRepo,
    PostingTaken,
    StoredAnswer,
    StoredApplication,
    is_never_stored,
)

log = get_logger(__name__)

NOT_FOUND: Final = "Application not found"
MATCH_NOT_FOUND: Final = "Match not found"
CHANGED: Final = "The application changed; reload it."
ALREADY_APPLIED: Final = "You already have an application for this posting."
NOT_SAME_POSTING: Final = "That application link does not look like the posting you matched. Check the link, or apply on the employer's site."
ID_REFUSED: Final = (
    "That looks like an ID or bank number. We never keep those: type it yourself on the employer's page."
)
APPLY_REF_PREFIX: Final = "apply:"
"""The ledger ref of an application's hold (mirrors `billing.entitlements.APPLY_REF_PREFIX`; a test keeps them equal)."""

ATS_KINDS: Final[frozenset[str]] = frozenset(get_args(AtsKind))
ANSWER_SOURCES: Final[frozenset[str]] = frozenset(get_args(AnswerSource))
ANSWERABLE_STAGES: Final = frozenset({"waiting_for_you"})
"""Stages whose questions the person may answer (the review's edits included)."""
UNDELETABLE_STAGES: Final = frozenset({"submitting", "check_this_one"})
"""The send may have reached the employer: settle it first (resolve), then delete."""
TRACK_TARGETS: Final = stages.SENT_AND_LATER - {"sent"}
"""What the generic stage route may move to: tracking by hand, only from sent on."""

STALE_AFTER: Final = timedelta(days=90)
STALE_KEYS: Final = frozenset({"noticePeriod", "currentCtc", "location"})
"""Answers that go stale (spec 2.2): a one-tap "Still true?" after 90 days."""
PRIVATE_KEYS: Final = frozenset({"currentCtc"})
"""Saved but never shown back (sent only into a form that requires it)."""
RESUME_KEYS: Final = frozenset({"fullName", "email", "phone", "location", "currentTitle", "totalExperience"})
"""What `ApplyService.read_resume` may read from the resume into the answer bank."""
QUESTION_KEY_MAX: Final = 64
"""The longest question key the answer bank keeps (``answer_bank.question_key``)."""
NO_DIGIT_CHECK_KEYS: Final = frozenset({"phone"})
"""Keys whose value is a long number by nature: a phone number is not refused as an ID."""

RESUME_DOCX: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
RESUME_EXT: Final = {"application/pdf": ".pdf", RESUME_DOCX: ".docx"}

ShotKind = Literal["review", "confirmation"]
_UNAPPROVED: Final[dict[str, Any]] = {"approved_at": None, "approved_digest": None}


# ───────────────────────── the question bank (spec 2.2) ─────────────────────────


@dataclass(frozen=True, slots=True)
class _Q:
    key: str
    question: str
    group: AnswerGroup
    kind: FieldKind = "text"
    options: tuple[str, ...] = ()


NOTICE_OPTIONS: Final = ("Immediate", "15 days", "30 days", "60 days", "90 days", "Serving notice")
YES_NO: Final = ("Yes", "No")

BANK_QUESTIONS: Final[tuple[_Q, ...]] = (
    _Q("fullName", "Full name as on your ID", "you"),
    _Q("email", "Email", "you", "email"),
    _Q("phone", "Mobile number", "you", "tel"),
    _Q("location", "Current city", "you"),
    _Q("preferredCities", "Cities you'd work in", "you"),
    _Q("languages", "Languages you can work in", "you"),
    _Q("dob", "Date of birth (sent only where a form requires it)", "you", "date"),
    _Q("currentCompany", "Current company", "work"),
    _Q("currentTitle", "Current designation", "work"),
    _Q("totalExperience", "Total experience", "work"),
    _Q("noticePeriod", "Notice period", "work", "select", NOTICE_OPTIONS),
    _Q("currentCtc", "Current CTC (lakh a year)", "work", "number"),
    _Q("expectedCtc", "Expected CTC (lakh a year)", "work"),
    _Q("highestQualification", "Highest qualification", "education"),
    _Q("college", "College", "education"),
    _Q("graduationYear", "Year of passing", "education", "number"),
    _Q("graduationScore", "Graduation score (as given: CGPA or %)", "education"),
    _Q("class12Score", "Class 12 score", "education"),
    _Q("class10Score", "Class 10 score", "education"),
    _Q("activeBacklogs", "Active backlogs", "education", "number"),
    _Q("backlogsEver", "Backlogs ever (cleared)", "education", "number"),
    _Q("gap6m", "A gap of 6 months or more", "education", "radio", YES_NO),
    _Q("relocation", "Relocation", "where_how", "select", ("Yes", "Within my cities", "Ask each time")),
    _Q("workMode", "Work mode", "where_how", "select", ("Office", "Hybrid", "Remote")),
    _Q("shifts", "Shifts", "where_how", "select", ("Day", "Rotational", "Night")),
    _Q("bond", "Service bond", "where_how", "select", ("Up to 1 year", "No bonds", "Ask each time")),
    _Q("pronouns", "Pronouns", "self_id"),
    _Q("eeoGender", "Gender", "self_id", "select"),
)
"""The common questions Your details offers as "Not asked yet" until the person or a form answers them."""

_BANK: Final[dict[str, _Q]] = {q.key: q for q in BANK_QUESTIONS}

NEVER_STORED_LABELS: Final[tuple[str, ...]] = (
    "PAN",
    "Aadhaar",
    "Passport number",
    "Bank account and IFSC",
    "Any other ID number",
    "Caste or category",
    "Religion",
    "Marital status",
    "Father's name",
    "Signatures and declarations",
    "Do you know anyone here, referrals",
    "Earlier applications to this employer",
)
"""What we leave to the person (spec 2.2), as Your details lists it."""

FIRST_CHECK_DEFAULTS: Final[tuple[tuple[str, str | None], ...]] = (
    ("expectedCtc", "Open to discussion"),
    ("relocation", None),
    ("bond", None),
)
"""The safe defaults the first check sets when nothing is saved yet: expected CTC "open to discussion"; relocation and
bonds asked per offer (``None``: ask each time). Self-ID needs none: it is declined unless "use on forms" is on."""


# ───────────────────────── facts "from you" (an allowlist, sentences built in code) ─────────────────────────

_FACT_SENTENCES: Final[dict[str, str]] = {
    "noticePeriod": "My notice period is {v}.",
    "languages": "I can work in {v}.",
    "highestQualification": "My highest qualification is {v}.",
    "college": "I studied at {v}.",
    "graduationYear": "I graduated in {v}.",
    "graduationScore": "My graduation score is {v}.",
    "class12Score": "I scored {v} in class 12.",
    "class10Score": "I scored {v} in class 10.",
}
_YES: Final = frozenset({"yes", "y", "true"})
_NO: Final = frozenset({"no", "n", "false"})


def fact_allowed(key: str) -> bool:
    """May an answer under ``key`` become a fact "from you"? Notice period, languages, education and marks, and work
    authorisation for one named country. Never CTC, date of birth, gender, self-ID or anything never stored."""
    if is_never_stored(key):
        return False
    base, _, country = key.partition(":")
    if base in COUNTRY_KEYS:
        return len(country) == 2
    return key in _FACT_SENTENCES


def fact_sentence(key: str, value: str) -> str | None:
    """The sentence a saved answer adds to the person's facts, or None when ``key`` is not on the allowlist."""
    text = " ".join(value.split())[:APPLY_DETAIL_MAX_CHARS].rstrip(".")
    if not text or not fact_allowed(key):
        return None
    base, _, country = key.partition(":")
    if base not in COUNTRY_KEYS:
        return _FACT_SENTENCES[key].format(v=text)
    name = COUNTRY_NAMES.get(country.upper(), country.upper())
    said = text.lower()
    if base == "workAuthorization":
        if said in _YES:
            return f"I am authorised to work in {name}."
        if said in _NO:
            return f"I am not authorised to work in {name}."
        return f"On working in {name}: {text}."
    if said in _YES:
        return f"I need visa sponsorship to work in {name}."
    if said in _NO:
        return f"I do not need visa sponsorship to work in {name}."
    return f"On visa sponsorship in {name}: {text}."


# ───────────────────────── ID numbers, by shape ─────────────────────────

_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.I)
_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b", re.I)
_AADHAAR = re.compile(r"\b[2-9][0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}\b")
_PASSPORT = re.compile(r"\b[A-Z][0-9]{7}\b", re.I)
_LONG_NUMBER = re.compile(r"\b[0-9]{11,18}\b")


def id_shaped(value: str, key: str = "") -> bool:
    """Does ``value`` look like a PAN, Aadhaar, IFSC, passport number or long bank account number, anywhere in the text
    ("Aadhaar: 2345 6789 0123" too)? The digit-only shapes (Aadhaar, an account number) are never checked in a phone
    number field."""
    text = value.strip()
    if _PAN.search(text) or _IFSC.search(text) or _PASSPORT.search(text):
        return True
    if key.split(":", 1)[0] in NO_DIGIT_CHECK_KEYS:
        return False
    return bool(_AADHAAR.search(text) or _LONG_NUMBER.search(text))


def own_email(value: str, account_email: str | None) -> bool:
    """The account's email, or a ``+tag`` of it (``name+leafout@gmail.com``)."""
    if not account_email:
        return False
    given, mine = value.strip().lower(), account_email.strip().lower()
    if given == mine:
        return True
    local, at, domain = given.partition("@")
    mine_local, _, mine_domain = mine.partition("@")
    base, plus, tag = local.partition("+")
    return bool(at and plus and tag) and base == mine_local and domain == mine_domain


# ───────────────────────── the resume file ─────────────────────────


def sniff_resume(data: bytes) -> str | None:
    """The resume's media type from its content: a PDF (``%PDF-``) or a DOCX (a zip holding ``word/document.xml``);
    None for anything else, whatever its name or Content-Type says."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" in archive.namelist():
                    return RESUME_DOCX
        except (zipfile.BadZipFile, ValueError, OSError):
            return None
    return None


# ───────────────────────── what a match lends ─────────────────────────


@dataclass(frozen=True, slots=True)
class MatchInfo:
    """What a posting match lends a start (read by the api layer through the match's own code: apply never imports
    match). ``posting_url`` is the employer's link resolved server-side (a Discover listing proved to be the matched
    posting) or, with ``pasted``, the application link the person gave for a pasted match, which must be the same
    posting (`intake.same_posting`)."""

    match_id: str
    profile_id: str
    user_id: str | None = None
    blocked: bool = False
    gate_reasons: tuple[str, ...] = ()
    title: str | None = None
    company: str | None = None
    location: str | None = None
    posting_url: str | None = None
    listing_id: str | None = None
    resume_choice: ResumeChoice = "saved"
    pasted: bool = False
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Inspected:
    """The start checks' verdict, with what they read on the way (for the row a start creates)."""

    check: ApplyCheck
    url: str | None = None
    domain: str = ""
    fp: Fingerprint = field(default_factory=lambda: Fingerprint("unknown"))
    job: JobInfo = field(default_factory=JobInfo)
    key: str | None = None
    existing: StoredApplication | None = None


def _with_url(inspected: Inspected) -> ApplyCheck:
    """The verdict with the employer's own page for the posting (``applyUrl``), for "Open on <host>'s site"."""
    if inspected.url is None or inspected.check.applyUrl is not None:
        return inspected.check
    return inspected.check.model_copy(update={"applyUrl": inspected.url})


class StartRefused(AppError):
    """A start check failed: nothing was created, held or charged. ``check`` is the verdict (the route sends its reason,
    the gate reasons and the earlier application with the error). A 402 for no credit, else a 409."""

    code = "apply_refused"

    def __init__(self, check: ApplyCheck) -> None:
        self.check = check
        status = 402 if check.reason == "no_credit" else 409
        super().__init__(check.note or "Apply for me is not offered for this posting.", status=status)

    def to_body(self) -> dict[str, str]:
        body = {"error": self.user_message, "reason": self.check.reason or ""}
        if self.check.applicationId:
            body["existingApplicationId"] = self.check.applicationId
        return body


# ───────────────────────── the wire shapes ─────────────────────────


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def apply_ref(application_id: str) -> str:
    """``apply:<application id>``: the ledger ref of the application's hold."""
    return f"{APPLY_REF_PREFIX}{application_id}"


def _progress(app: StoredApplication) -> ApplyProgress | None:
    if not app.progress:
        return None
    try:
        return ApplyProgress.model_validate(app.progress)
    except ValidationError:
        return None


_CLAIMED_HOLD: Final = frozenset({"capturing", "releasing"})
"""``credit_state`` values the maintenance sweep claims a row with while it calls the ledger
(`storage.maintenance.CLAIM_STATES`): still an open hold to the person."""


def _credit_state(state: str | None) -> str | None:
    return "held" if state in _CLAIMED_HOLD else state


def summary_of(app: StoredApplication) -> ApplicationSummary:
    return ApplicationSummary(
        id=app.id, stage=app.stage, source=app.source, ats=app.ats if app.ats in ATS_KINDS else "unknown",  # type: ignore[arg-type]
        domain=app.domain, jobUrl=app.job_url, createdAt=app.created_at.isoformat(), updatedAt=app.updated_at.isoformat(),
        needsCount=sum(1 for n in app.needs or [] if n.get("required")), hasScreenshot=app.has_screenshot,
        company=app.company, title=app.title, location=app.location, matchId=app.match_id,
        approvedAt=_iso(app.approved_at), stageReason=app.stage_reason, resumeChoice=app.resume_choice,  # type: ignore[arg-type]
        listingId=app.listing_id, creditState=_credit_state(app.credit_state), stopReason=app.stop_reason,  # type: ignore[arg-type]
        hasConfirmation=True if app.has_confirmation else None, progress=_progress(app),
        sentAt=_iso(app.submitted_at) if app.stage in stages.SENT_AND_LATER else None,
    )  # fmt: skip


def user_of(owner: str) -> str:
    """The user id behind an owner key; only an account (``u:<id>``) may use auto-apply."""
    if not owner.startswith(ACCOUNT_OWNER_PREFIX) or len(owner) == len(ACCOUNT_OWNER_PREFIX):
        raise Unauthorized()
    return owner[len(ACCOUNT_OWNER_PREFIX) :]


def _country_unknown(key: str) -> bool:
    """A work-authorisation key without its country: never stored, never shared between forms."""
    base, _, country = key.partition(":")
    return base in COUNTRY_KEYS and len(country) != 2


def _need_key(need: Mapping[str, Any]) -> str | None:
    found = need.get("questionKey")
    return str(found) if found else None


def _shared(need: Mapping[str, Any], key: str) -> bool:
    """May an answer saved on another form fill this need? Not a per-offer question (an unknown country's work
    authorisation is one), not one typed on the employer's page, not a never-stored key."""
    return (
        _need_key(need) == key
        and not need.get("perOffer")
        and not need.get("onSite")
        and not is_never_stored(key)
        and not _country_unknown(key)
    )


TAILORED_NEED_KEY: Final = "resume:tailored"
"""The engine's need when the tailored resume cannot be exported yet (`apply.engine.TAILORED_NEED_KEY`): answering it
only queues the form again; it is never kept in the answer bank."""


def _savable(key: str, need: Mapping[str, Any] | None) -> bool:
    if is_never_stored(key) or _country_unknown(key) or key == TAILORED_NEED_KEY:
        return False
    # a per-offer custom question (a tick box only the person agrees to, a country nobody can name) stays on its form
    return not (need is not None and need.get("perOffer") and key.startswith("q:"))


def _asked_keys(app: StoredApplication) -> frozenset[str]:
    """Every key an answer to this application may carry: its needs, and the fields of its review (an edit there)."""
    keys: set[str] = set()
    rows: list[Any] = list(app.needs or [])
    review = app.review or {}
    for part in ("fields", "skipped"):
        rows.extend(r for r in review.get(part) or [] if isinstance(r, Mapping))
    for row in rows:
        for name in ("questionKey", "key"):
            if found := row.get(name):
                keys.add(str(found))
    return frozenset(keys)


def _waits_only_for_files(app: StoredApplication) -> bool:
    """A waiting form whose every open required need is a file (nothing to type here): "Fill it again" queues it."""
    if app.stage != "waiting_for_you":
        return False
    open_needs = [n for n in app.needs or [] if n.get("required")]
    return bool(open_needs) and all(n.get("kind") == "file" and not n.get("onSite") for n in open_needs)


def _saved(answer: StoredAnswer, now: datetime) -> SavedAnswer:
    key = answer.question_key
    base, _, country = key.partition(":")
    group: AnswerGroup | None = None
    if key in _BANK:
        group = _BANK[key].group
    elif base in COUNTRY_KEYS:
        group = "where_how"
    elif base in SELF_ID_KEYS:
        group = "self_id"
    seen = answer.confirmed_at or answer.answered_at
    private = key in PRIVATE_KEYS
    stale = key in STALE_KEYS and answer.value is not None and now - seen > STALE_AFTER
    source = answer.source if answer.source in ANSWER_SOURCES else "you"
    return SavedAnswer(
        questionKey=key, question=answer.question, source=source, askEachTime=answer.ask_each_time,  # type: ignore[arg-type]
        answeredAt=answer.answered_at.isoformat(), value=None if private else answer.value, group=group,
        answeredOn=answer.answered_on, confirmedAt=_iso(answer.confirmed_at), stale=True if stale else None,
        country=country.upper() if base in COUNTRY_KEYS and len(country) == 2 else None,
        private=True if private and answer.value is not None else None,
    )  # fmt: skip


def _work_auth(bank: Sequence[StoredAnswer]) -> list[WorkAuthorization]:
    by_country: dict[str, dict[str, str]] = {}
    for answer in bank:
        base, _, country = answer.question_key.partition(":")
        if base in COUNTRY_KEYS and len(country) == 2 and answer.value is not None:
            by_country.setdefault(country.upper(), {})[base] = answer.value
    return [
        WorkAuthorization(
            country=country,
            authorised=values.get("workAuthorization", ""),
            needsSponsorship=values.get("needsSponsorship"),
        )
        for country, values in sorted(by_country.items())
    ]


# ───────────────────────── the service ─────────────────────────


class ApplyService:
    def __init__(
        self,
        repo: ApplyRepo,
        settings: Settings,
        clock: Clock,
        fetcher: PageFetcher,
        status: StatusFetcher | None = None,
        credits: ApplyCredits | None = None,
        facts: FactSink | None = None,
    ) -> None:
        self.repo, self.settings, self.clock, self.fetcher = repo, settings, clock, fetcher
        self.status_fetcher, self.credits, self.facts = status, credits, facts

    # ── the start checks ──

    async def _inspect(
        self, user_id: str, url: str | None, match: MatchInfo | None, *, credit_ok: bool = True
    ) -> Inspected:
        """Every start check, in `evaluate_start`'s order, reading as little as it can: a manual-only site is never
        fetched, the job page is read only for a pasted link or a link whose address alone names no form, and the
        liveness read runs only for a form we fill."""
        blocked = match.blocked if match else False
        reasons = match.gate_reasons if match else ()

        def verdict(fp: Fingerprint, **kw: Any) -> ApplyCheck:
            return evaluate_start(
                signed_in=True, fp=fp, blocked=blocked, gate_reasons=reasons, credit_ok=credit_ok, **kw
            )

        if not url or not url.strip():
            return Inspected(verdict(Fingerprint("unknown"), manual_only=False, liveness=None, existing=None))
        if len(url) > APPLY_URL_MAX_CHARS:
            raise BadRequest("That link is too long.")
        clean = normalize_url(url)
        if clean is None:
            raise BadRequest("That does not look like a web link.")
        domains = self.settings.apply.manual_only_domains
        domain = host_of(clean)
        manual = verdict(Fingerprint("unknown"), manual_only=True, liveness=None, existing=None, host=domain)
        if is_manual_only(clean, domains):
            return Inspected(manual, clean, domain)
        fp, job = fingerprint_url(clean), JobInfo()
        pasted = match is not None and match.pasted
        if pasted or (not fillable(fp) and fp.ats not in OWN_ACCOUNT_ATS):
            read = await intake(clean, self.fetcher, domains)
            if read.manual_only:  # it redirected onto a board that forbids automation
                return Inspected(manual, clean, domain)
            fp, job = read.fingerprint, read.job
            if match is not None and pasted and not same_posting(job, match.title, match.company):
                raise BadRequest(NOT_SAME_POSTING)
        key = posting_key(fp, clean)
        existing = await self.repo.find_by_posting(user_id, key)
        liveness: Liveness | None = None
        if fillable(fp) and fp.ats not in OWN_ACCOUNT_ATS:
            liveness = (
                await check_live(fp, self.status_fetcher) if self.status_fetcher is not None else "unknown"
            )
        check = verdict(fp, manual_only=False, liveness=liveness, existing=existing, host=domain)
        return Inspected(check, clean, site_key(clean, fp), fp, job, key, existing)

    async def check(self, owner: str, match: MatchInfo | None, url: str | None) -> ApplyCheck:
        """The read-only check behind "Apply for me": no row, no hold, no charge."""
        inspected = await self._inspect(user_of(owner), url, match)
        log.info("apply_check", offered=inspected.check.offered, reason=inspected.check.reason)
        return _with_url(inspected)

    # ── start ──

    async def create(
        self, owner: str, body: StartApplicationRequest, match: MatchInfo, *, unlimited: bool = False
    ) -> ApplicationDetail:
        """Start an application from a match. Every check runs before anything is written; then the hold, then the
        row. Raises `StartRefused` (nothing created or held), `PaymentRequired` from the hold, or `PostingTaken`."""
        user_id = user_of(owner)
        if match.user_id is not None and match.user_id != user_id:
            raise NotFound(MATCH_NOT_FOUND)
        now = self.clock.now()
        cap = self.settings.apply.max_per_day
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # the daily cap before any page or ATS endpoint is read: a capped account makes no outbound request
        if cap and await self.repo.count_created_since(owner, day) >= cap:
            raise RateLimited(f"You can start {cap} applications a day. Try again tomorrow.")
        inspected = await self._inspect(user_id, match.posting_url, match)
        if not inspected.check.offered or inspected.url is None or inspected.key is None:
            log.info("apply_start_refused", reason=inspected.check.reason)
            raise StartRefused(_with_url(inspected))
        earlier = inspected.existing if reopens(inspected.existing) else None
        app_id = earlier.id if earlier is not None else "app_" + secrets.token_hex(12)
        ref = apply_ref(app_id)
        held = not unlimited and self.credits is not None
        if held and self.credits is not None:
            await self.credits.hold(user_id, ref)  # PaymentRequired (402) before any row
        fp, job = inspected.fp, inspected.job
        description = (match.description or job.description or "")[:4000] or None
        fields: dict[str, Any] = {
            "profile_id": match.profile_id, "match_id": match.match_id, "apply_url": fp.apply_url,
            "domain": inspected.domain, "ats": fp.ats, "company": match.company or job.company,
            "title": match.title or job.title, "location": match.location or job.location,
            "job": {"description": description, "typed": {}}, "resume_choice": body.resume,
            "listing_id": match.listing_id, "credit_ref": ref if held else None,
            "credit_state": "held" if held else None,
        }  # fmt: skip
        try:
            if earlier is not None:
                app = await self._reopen(earlier, fields, now)
            else:
                app = StoredApplication(
                    id=app_id, owner_key=owner, user_id=user_id, source="discover" if match.listing_id else "match",
                    job_url=inspected.url, stage="queued", created_at=now, updated_at=now, posting_key=inspected.key,
                    **fields,
                )  # fmt: skip
                await self.repo.create_application(app)
        except Exception as exc:
            if held:
                if earlier is not None:
                    await self._give_back_unless_live(user_id, app_id, ref)
                else:
                    await self._give_back(user_id, ref)
            if isinstance(exc, AppError):
                raise
            other = await self.repo.find_by_posting(user_id, inspected.key)
            if other is not None:
                log.info("apply_start_raced", application_id=other.id)
                raise PostingTaken(other.id) from None
            raise
        note = "Started again from a match." if earlier is not None else "Started from a match."
        await stages.note(self.repo, app.id, note, source="user", now=now, kind="created")
        log.info("apply_started", application_id=app.id, ats=fp.ats, reopened=earlier is not None, held=held)
        wake.poke()
        return await self._detail(app)

    async def _reopen(
        self, earlier: StoredApplication, fields: Mapping[str, Any], now: datetime
    ) -> StoredApplication:
        """A cancelled, closed or failed row of the same posting, queued again with a fresh hold and nothing left from
        the earlier fill."""
        clean: dict[str, Any] = {
            "stage_reason": None, "needs": None, "review": None, "review_digest": None, "approved_at": None,
            "approved_digest": None, "submitted_at": None, "submitting_at": None, "attempts": 0, "lease_owner": None,
            "lease_until": None, "stop_reason": None, "progress": None, "manual_url": None,
        }  # fmt: skip
        moved = await stages.move(self.repo, earlier, "queued", source="system", now=now,
                                  note="Started again from a match.", changes={**clean, **fields})  # fmt: skip
        if moved is None:
            raise Conflict(CHANGED)
        await self.repo.put_screenshot(earlier.id, None)
        await self.repo.put_confirmation(earlier.id, None)
        return moved

    # ── the hold ──

    async def _give_back(self, user_id: str, ref: str) -> bool:
        if self.credits is None:
            return False
        try:
            return await self.credits.release(user_id, ref)
        except Exception:  # the maintenance sweep settles a hold whose release failed
            log.error("apply_release_failed", exc_info=True)
            return False

    async def _give_back_unless_live(self, user_id: str, application_id: str, ref: str) -> None:
        """After a lost compare-and-set on a reopen or a retry. Their hold ref is the row's own (``apply:<id>``) and the
        ledger's hold is idempotent per ref, so a concurrent start or retry that won shares this very hold: it is given
        back only when no row now counts on it."""
        current = await self.repo.get_application(application_id, None)
        if (
            current is not None
            and current.credit_state == "held"
            and (current.credit_ref or apply_ref(current.id)) == ref
        ):
            log.info("apply_hold_shared", application_id=application_id)
            return
        await self._give_back(user_id, ref)

    async def _settle(self, app: StoredApplication, how: Literal["release", "capture"]) -> StoredApplication:
        """Give back or charge the application's hold, and record it on the row. A failure is logged and left to the
        maintenance sweep (the row's stage says what it should be)."""
        if app.credit_state != "held" or self.credits is None or app.user_id is None:
            return app
        ref = app.credit_ref or apply_ref(app.id)
        try:
            if how == "capture":
                await self.credits.capture(app.user_id, ref)
            else:
                await self.credits.release(app.user_id, ref)
        except Exception:
            log.error("apply_settle_failed", how=how, application_id=app.id, exc_info=True)
            return app
        state = "charged" if how == "capture" else "released"
        updated = await self.repo.update_application(
            app.id, {"credit_state": state}, now=self.clock.now(), expected_stage=app.stage
        )
        if updated is None and how == "release":
            # a fresh start or "Try again" landed between the move and this write, counting on this very hold
            await retake_if_reopened(self.repo, self.credits, app.id, app.user_id, ref, self.clock.now())
        return updated or app

    # ── applications ──

    async def _get(self, owner: str, application_id: str) -> StoredApplication:
        user_of(owner)
        found = await self.repo.get_application(application_id, owner)
        if found is None:
            raise NotFound(NOT_FOUND)
        return found

    async def detail(self, owner: str, application_id: str) -> ApplicationDetail:
        return await self._detail(await self._get(owner, application_id))

    async def _also_waiting(self, app: StoredApplication) -> dict[str, list[AlsoWaiting]]:
        """Question key -> the other waiting applications that ask it ("Also asked by 2 more forms waiting")."""
        if app.user_id is None or app.stage != "waiting_for_you" or not app.needs:
            return {}
        out: dict[str, list[AlsoWaiting]] = {}
        for other in await self.repo.list_waiting(app.user_id):
            if other.id == app.id:
                continue
            for need in other.needs or []:
                key = _need_key(need)
                if key is None or not _shared(need, key):
                    continue
                rows = out.setdefault(key, [])
                if all(a.applicationId != other.id for a in rows):
                    rows.append(AlsoWaiting(applicationId=other.id, company=other.company or other.domain))
        return out

    async def _detail(self, app: StoredApplication) -> ApplicationDetail:
        events = [
            ApplicationEvent(at=e.at.isoformat(), kind=e.kind, source=e.source, fromStage=e.from_stage, toStage=e.to_stage,  # type: ignore[arg-type]
                             note=(e.detail or {}).get("note"))
            for e in await self.repo.list_events(app.id)
        ]  # fmt: skip
        also = await self._also_waiting(app)
        needs = []
        for raw in app.needs or []:
            need = NeedsInput.model_validate(raw)
            key = need.questionKey
            if key and key in also and _shared(raw, key):
                need = need.model_copy(update={"alsoWaiting": also[key]})
            needs.append(need)
        review = None
        if app.review:
            try:
                review = ApplicationReview.model_validate(app.review)
            except ValidationError:
                log.warning("apply_review_unreadable", application_id=app.id)
        base = summary_of(app).model_dump(exclude_none=True)
        return ApplicationDetail(
            **base, needs=needs, events=events, applyUrl=app.apply_url, manualUrl=app.manual_url,
            description=(app.job or {}).get("description"), submittedAt=_iso(app.submitted_at), review=review,
        )  # fmt: skip

    async def list(self, owner: str) -> ApplicationList:
        user_of(owner)
        return ApplicationList(applications=[summary_of(a) for a in await self.repo.list_applications(owner)])

    # ── answers ──

    def _check_value(self, key: str, value: str, account_email: str | None) -> None:
        base = key.split(":", 1)[0]
        if base in ON_SITE_KEYS or id_shaped(value, key):
            raise BadRequest(ID_REFUSED)
        if base == "email" and not own_email(value, account_email):
            raise BadRequest("Use your own email: the one you sign in with, or a +tag of it.")

    async def _bank_put(
        self,
        user_id: str,
        key: str,
        value: str | None,
        *,
        question: str,
        source: AnswerSource,
        ask_each_time: bool = False,
        answered_on: str | None = None,
        application_id: str | None = None,
    ) -> StoredAnswer:
        now = self.clock.now()
        return await self.repo.put_answer(
            StoredAnswer(
                user_id=user_id, question_key=key, question=question[:APPLY_DETAIL_MAX_CHARS] or key, source=source,
                ask_each_time=ask_each_time, answered_at=now, created_at=now, updated_at=now, value=value,
                answered_on=answered_on, application_id=application_id,
            )
        )  # fmt: skip

    async def _remember_fact(self, user_id: str, profile_id: str | None, key: str, value: str) -> None:
        """The answer's fact replaces the earlier one for its key; an answer that makes no sentence withdraws it."""
        if self.facts is None or profile_id is None or not fact_allowed(key):
            return
        sentence = fact_sentence(key, value)
        try:
            if sentence is None:
                await self.facts.retract(user_id, profile_id, key)
            else:
                await self.facts(user_id, profile_id, key, sentence)
        except Exception:  # a fact that could not be written never fails the answer
            log.warning("apply_fact_failed", key=key.split(":", 1)[0], exc_info=True)

    async def _forget_fact(self, owner: str, user_id: str, key: str) -> None:
        """A withdrawn answer's fact leaves every profile the person applied from."""
        if self.facts is None or not fact_allowed(key):
            return
        seen: set[str] = set()
        for app in await self.repo.list_applications(owner, limit=50):
            if app.profile_id and app.profile_id not in seen:
                seen.add(app.profile_id)
                try:
                    await self.facts.retract(user_id, app.profile_id, key)
                except Exception:
                    log.warning("apply_fact_retract_failed", key=key.split(":", 1)[0], exc_info=True)

    async def answer(
        self, owner: str, application_id: str, body: AnswerRequest, *, account_email: str | None = None
    ) -> AnswerResult:
        """A batch of answers for one application: typed into it, saved ones kept and shared with every other waiting
        application that asks the same question. The application (and those others) are queued again."""
        user_id = user_of(owner)
        app = await self._get(owner, application_id)
        if app.stage not in ANSWERABLE_STAGES:
            raise Conflict("This application is not waiting for answers.")
        by_key = {k: n for n in app.needs or [] if (k := _need_key(n)) is not None}
        by_field = {str(n.get("key")): n for n in app.needs or []}
        asked = _asked_keys(app)
        given: list[tuple[QuestionAnswer, str, str, Mapping[str, Any] | None]] = []
        for item in body.answers:
            key = item.questionKey.strip()
            value = item.value.strip()[:APPLY_ANSWER_MAX_CHARS]
            if not key or not value:
                continue
            if key not in asked or len(key) > QUESTION_KEY_MAX:
                raise BadRequest("That question is not one this form asks.")
            self._check_value(
                key, value, account_email
            )  # the whole batch is refused before anything is written
            given.append((item, key, value, by_key.get(key) or by_field.get(key)))
        if not given:
            raise BadRequest("Type an answer first.")
        typed = dict((app.job or {}).get("typed") or {})
        saved: dict[str, str] = {}
        for item, key, value, need in given:
            typed[key] = value
            if need is not None and need.get("key"):
                typed[str(need["key"])] = value  # the planner reads a typed value by field key first
            if item.save and _savable(key, need):
                label = str((need or {}).get("label") or (_BANK[key].question if key in _BANK else key))
                await self._bank_put(user_id, key, value, question=label, source="form", answered_on=app.company,
                                     application_id=app.id)  # fmt: skip
                saved[key] = value
                await self._remember_fact(user_id, app.profile_id, key, value)
        moved = await stages.move(self.repo, app, "queued", source="user", now=self.clock.now(),
                                  note="Answered and queued again.",
                                  changes={"job": {**(app.job or {}), "typed": typed}, "stage_reason": None,
                                           "stop_reason": None, **_UNAPPROVED})  # fmt: skip
        if moved is None:
            raise Conflict(CHANGED)
        requeued = await self._share(user_id, app.id, saved)
        log.info(
            "apply_answered", application_id=app.id, answers=len(given), saved=len(saved), requeued=requeued
        )
        wake.poke()
        return AnswerResult(application=await self._detail(moved), requeued=requeued)

    async def _share(self, user_id: str, answered_id: str, saved: Mapping[str, str]) -> int:
        """Fill every other waiting application that asks one of the ``saved`` questions and queue it again, so no form
        asks it twice (spec 2.2). Each move is a compare-and-set: a row that moved meanwhile is left."""
        if not saved:
            return 0
        count = 0
        for other in await self.repo.list_waiting(user_id):
            if other.id == answered_id:
                continue
            asked = {k: v for k, v in saved.items() if any(_shared(n, k) for n in other.needs or [])}
            if not asked:
                continue
            typed = {**dict((other.job or {}).get("typed") or {}), **asked}
            moved = await stages.move(self.repo, other, "queued", source="user", now=self.clock.now(),
                                      note="Answered on another form and queued again.",
                                      changes={"job": {**(other.job or {}), "typed": typed}, **_UNAPPROVED})  # fmt: skip
            if moved is not None:
                count += 1
        return count

    # ── approve, cancel, resolve ──

    async def approve(self, owner: str, application_id: str, digest: str) -> ApplicationDetail:
        """The person approves exactly the reviewed values: the digest must be the review's, and no required question
        may be open (one typed on the employer's page included). Only this lets the worker submit."""
        app = await self._get(owner, application_id)
        if app.stage != "waiting_for_you" or not app.review_digest:
            raise Conflict("There is nothing to approve: the form is not filled and reviewed.")
        if digest != app.review_digest:
            raise Conflict("The form changed since you looked at it. Review it again.")
        open_needs = [n for n in app.needs or [] if n.get("required")]
        if any(n.get("onSite") for n in open_needs):
            raise Conflict(
                "This form asks for something you type on the employer's page. Open the form there."
            )
        if open_needs:
            raise Conflict("Answer the open questions first.")
        now = self.clock.now()
        moved = await stages.move(self.repo, app, "approved", source="user", now=now, note="Approved for sending.",
                                  changes={"approved_at": now, "approved_digest": digest}, kind="approved")  # fmt: skip
        if moved is None:
            raise Conflict(CHANGED)
        wake.poke()
        return await self._detail(moved)

    async def cancel(self, owner: str, application_id: str) -> ApplicationDetail:
        """Any stage before submitting -> cancelled, then the hold is given back."""
        app = await self._get(owner, application_id)
        if app.stage not in stages.USER_CANCELLABLE:
            raise Conflict("This application can no longer be cancelled.")
        moved = await stages.move(self.repo, app, "cancelled", source="user", now=self.clock.now(),
                                  note="Cancelled.", changes=_UNAPPROVED)  # fmt: skip
        if moved is None:
            raise Conflict(CHANGED)
        return await self._detail(await self._settle(moved, "release"))

    async def resolve(self, owner: str, application_id: str, outcome: ResolveOutcome) -> ApplicationDetail:
        """The person's word on a ``check_this_one``: sent charges the hold; not sent gives it back and the row is
        failed (a new start or "Try again" fills it again and asks for a new approval; nothing is ever resent)."""
        app = await self._get(owner, application_id)
        if app.stage != "check_this_one":
            raise Conflict("Only an application whose outcome was unclear can be settled.")
        now = self.clock.now()
        how: Literal["release", "capture"]
        if outcome == "sent":
            sent_at = app.submitted_at or app.submitting_at or now
            moved = await stages.move(self.repo, app, "sent", source="user", now=now, kind="resolved",
                                      note="You said it went through.",
                                      changes={"submitted_at": sent_at, "stage_reason": None})  # fmt: skip
            how = "capture"
        else:
            reason = "You said it did not go through."
            moved = await stages.move(self.repo, app, "failed", source="user", now=now, kind="resolved",
                                      note=reason, changes={"stage_reason": reason, **_UNAPPROVED})  # fmt: skip
            how = "release"
        if moved is None:
            raise Conflict(CHANGED)
        return await self._detail(await self._settle(moved, how))

    async def applied_there(self, owner: str, application_id: str) -> ApplicationDetail:
        """ "I applied there": a ``needs_signin`` application the person finished on the employer's site. It is tracked
        as sent; the hold is given back, because Tayyari did not send it."""
        app = await self._get(owner, application_id)
        if app.stage != "needs_signin":
            raise Conflict("Only an application that needed you to sign in can be marked as applied there.")
        # the hold is given back BEFORE the row says sent: the sweep and delete capture a sent row's open hold, so a
        # release that failed after the move would charge for an application Tayyari did not send
        credit: dict[str, Any] = {}
        ref = app.credit_ref or apply_ref(app.id)
        credits, user_id = self.credits, app.user_id
        held = app.credit_state == "held" and credits is not None and user_id is not None
        if held and credits is not None and user_id is not None:
            try:
                await credits.release(user_id, ref)
            except Exception:
                log.error("apply_settle_failed", how="release", application_id=app.id, exc_info=True)
                raise Conflict("We could not settle this one just now. Try again in a moment.") from None
            credit = {"credit_state": "released"}
        moved = await stages.move(self.repo, app, "sent", source="user", now=self.clock.now(), kind="applied_there",
                                  note="Applied on the employer's site.",
                                  changes={"stage_reason": None, "stop_reason": None, "submitted_at": self.clock.now(),
                                           **_UNAPPROVED, **credit})  # fmt: skip
        if moved is None:
            if held and credits is not None and user_id is not None:
                await retake_if_reopened(self.repo, credits, app.id, user_id, ref, self.clock.now())
            raise Conflict(CHANGED)
        return await self._detail(moved)

    async def set_stage(
        self, owner: str, application_id: str, stage: str, note: str | None, *, unlimited: bool = False
    ) -> ApplicationDetail:
        """Manual tracking: viewed, interviewing, offer or rejected, only from sent on. Cancel and "Try again" go to
        their own methods; approve, resolve and "I applied there" have their own routes."""
        if stage == "cancelled":
            return await self.cancel(owner, application_id)
        if stage == "queued":
            return await self.retry(owner, application_id, unlimited=unlimited)
        app = await self._get(owner, application_id)
        from_sent = app.stage in stages.SENT_AND_LATER
        if (
            stage in stages.ROUTED_TARGETS
            or stage not in TRACK_TARGETS
            or not from_sent
            or not stages.allowed(app.stage, stage, "user")
        ):
            raise BadRequest(
                f"An application in {app.stage.replace('_', ' ')} cannot be moved to {stage.replace('_', ' ')}."
            )
        moved = await stages.move(self.repo, app, stage, source="user", now=self.clock.now(),
                                  note=(note or "").strip()[:APPLY_NOTE_MAX_CHARS] or None,
                                  changes={"stage_reason": None, **_UNAPPROVED})  # fmt: skip
        if moved is None:
            raise Conflict(CHANGED)
        return await self._detail(moved)

    async def retry(self, owner: str, application_id: str, *, unlimited: bool = False) -> ApplicationDetail:
        """ "Try again" after failed (before the click), needs_signin or closed, or "Fill it again" on a form that waits
        only for a file (the resume added in Your details, or the tailored resume finished): queued again. The hold is
        always taken through the ledger (idempotent per ref): ``credit_state`` alone is not trusted, because a release
        that crashed before its write-back leaves ``held`` on a row whose hold is gone."""
        user_id = user_of(owner)
        app = await self._get(owner, application_id)
        if app.stage not in stages.USER_RETRYABLE and not _waits_only_for_files(app):
            raise BadRequest(f"An application in {app.stage.replace('_', ' ')} cannot be tried again.")
        ref = app.credit_ref or apply_ref(app.id)
        take = not unlimited and self.credits is not None
        if take and self.credits is not None:
            await self.credits.hold(user_id, ref)
        credit: dict[str, Any] = {"credit_ref": ref, "credit_state": "held"} if take else {}
        moved = await stages.move(self.repo, app, "queued", source="user", now=self.clock.now(), note="Tried again.",
                                  changes={"stage_reason": None, "stop_reason": None, "attempts": 0, **_UNAPPROVED,
                                           **credit})  # fmt: skip
        if moved is None:
            if take and app.credit_state != "held":
                await self._give_back_unless_live(user_id, app.id, ref)
            raise Conflict(CHANGED)
        wake.poke()
        return await self._detail(moved)

    async def delete(self, owner: str, application_id: str) -> None:
        """Delete the row with its history, then settle its hold. The delete is a compare-and-set on the stage that was
        read, so it never races the worker's ``submitting`` marker: either the marker lands first (the delete matches
        nothing and is refused) or the delete does (the marker matches nothing and nothing is clicked)."""
        app = await self._get(owner, application_id)
        if app.stage in UNDELETABLE_STAGES:
            raise Conflict("This one may have reached the employer. Say whether it went through first.")
        if not await self.repo.delete_application(application_id, owner, expected_stage=app.stage):
            if await self.repo.get_application(application_id, owner) is None:
                raise NotFound(NOT_FOUND)
            raise Conflict(CHANGED)
        if app.credit_state != "held" or self.credits is None or app.user_id is None:
            return
        # a sent application's open hold (an email moved it on, or a capture failed) is charged, never refunded
        ref = app.credit_ref or apply_ref(app.id)
        try:
            if app.stage in stages.SENT_AND_LATER:
                await self.credits.capture(app.user_id, ref)
            else:
                await self.credits.release(app.user_id, ref)
        except Exception:  # the row is gone: logged for the ledger's own reconciliation
            log.error("apply_delete_settle_failed", application_id=app.id, exc_info=True)

    async def screenshot(self, owner: str, application_id: str, kind: ShotKind = "review") -> bytes:
        user_of(owner)
        if kind == "confirmation":
            data = await self.repo.get_confirmation(application_id, owner)
        else:
            data = await self.repo.get_screenshot(application_id, owner)
        if data is None:
            raise NotFound("No screenshot yet")
        return data

    # ── status and consent ──

    async def status(self, owner: str | None, *, enabled: bool) -> ApplyStatus:
        """Whether auto-apply is on, whether someone is signed in (it is for signed-in people only), and their first
        check, consent and the price."""
        signed_in = owner is not None
        profile = await self.repo.get_profile(owner) if owner is not None and enabled else None
        return ApplyStatus(
            enabled=enabled, signedIn=signed_in,
            firstCheckDone=(profile is not None and profile.first_check_at is not None) if signed_in and enabled else None,
            consentAt=_iso(profile.consent_at) if profile is not None else None,
            price=self.settings.billing.apply_credit_cost,
        )  # fmt: skip

    async def consent(self, owner: str) -> ApplyStatus:
        await self.repo.set_consent(user_of(owner), now=self.clock.now())
        return await self.status(owner, enabled=True)

    # ── Your details ──

    async def details(self, owner: str) -> YourDetails:
        user_id = user_of(owner)
        bank = await self.repo.list_answers(user_id)
        profile = await self.repo.get_profile(owner)
        now = self.clock.now()
        have = {a.question_key for a in bank}
        times = [a.updated_at for a in bank] + ([profile.updated_at] if profile is not None else [])
        return YourDetails(
            answers=[_saved(a, now) for a in bank],
            notAskedYet=[
                BankQuestion(
                    questionKey=q.key,
                    question=q.question,
                    group=q.group,
                    kind=q.kind,
                    options=list(q.options) or None,
                )
                for q in BANK_QUESTIONS
                if q.key not in have
            ],
            workAuthorization=_work_auth(bank),
            selfIdOnForms=profile.self_id_on_forms if profile is not None else False,
            neverStored=list(NEVER_STORED_LABELS),
            hasResume=profile is not None and profile.resume_bytes is not None,
            firstCheckDone=profile is not None and profile.first_check_at is not None,
            resumeName=profile.resume_name if profile is not None else None,
            updatedAt=max(times).isoformat() if times else None,
        )

    async def read_resume(
        self, owner: str, facts: Mapping[str, str | None], *, account_email: str | None = None
    ) -> int:
        """Layer 1 of the answer bank (spec 2.2): what the resume says (name, email, mobile, city, current role,
        experience), read in code with no model, written with source ``resume`` for the first check to show for Edit.
        Only until the first check is done, and only for questions the bank does not hold yet: what the person wrote
        always wins. How many were written."""
        user_id = user_of(owner)
        profile = await self.repo.get_profile(owner)
        if profile is not None and profile.first_check_at is not None:
            return 0
        wanted = {
            k: v.strip()[:APPLY_DETAIL_MAX_CHARS]
            for k, v in facts.items()
            if k in RESUME_KEYS and v and v.strip()
        }
        if not wanted:
            return 0
        have = await self.repo.get_answers(user_id, list(wanted))
        written = 0
        for key, value in wanted.items():
            if key in have:
                continue
            try:
                self._check_value(key, value, account_email)
            except AppError:
                continue  # an ID-shaped line on a resume is never kept
            await self._bank_put(user_id, key, value, question=_BANK[key].question, source="resume")
            written += 1
        return written

    async def _latest_profile_id(self, owner: str) -> str | None:
        """The profile an answer given in Your details feeds: the one the person last applied from."""
        for app in await self.repo.list_applications(owner, limit=20):
            if app.profile_id:
                return app.profile_id
        return None

    def _bank_key(self, raw: str) -> str:
        key = raw.strip()
        if not key or len(key) > QUESTION_KEY_MAX:
            raise BadRequest("That question is not one we keep.")
        if is_never_stored(key):
            raise BadRequest("We never keep that: it is yours to type on the employer's page or in Review.")
        if _country_unknown(key):
            raise BadRequest("Say which country this answer is for.")
        base, _, country = key.partition(":")
        if base in COUNTRY_KEYS:
            if not country.isalpha():
                raise BadRequest("Say which country this answer is for.")
            key = f"{base}:{country.upper()}"  # one row per country: workAuthorization:in is workAuthorization:IN
        return key

    async def save_answer(
        self, owner: str, body: SaveAnswerRequest, *, account_email: str | None = None
    ) -> YourDetails:
        """Your details: answer, change or set "ask each time" for one question."""
        user_id = user_of(owner)
        key = self._bank_key(body.questionKey)
        value = (body.value or "").strip()[:APPLY_ANSWER_MAX_CHARS] or None
        if value is None and not body.askEachTime:
            raise BadRequest("Type an answer, or choose to be asked each time.")
        if value is not None:
            self._check_value(key, value, account_email)
        existing = (await self.repo.get_answers(user_id, [key])).get(key)
        question = (
            existing.question if existing is not None else (_BANK[key].question if key in _BANK else key)
        )
        await self._bank_put(
            user_id, key, value, question=question, source="you", ask_each_time=body.askEachTime
        )
        if value is not None:
            await self._remember_fact(user_id, await self._latest_profile_id(owner), key, value)
        return await self.details(owner)

    async def delete_answer(self, owner: str, question_key: str) -> YourDetails:
        user_id = user_of(owner)
        if not await self.repo.delete_answer(user_id, question_key):
            raise NotFound("No saved answer for that question")
        await self._forget_fact(owner, user_id, question_key)
        return await self.details(owner)

    async def still_true(self, owner: str, question_key: str) -> YourDetails:
        """ "Still true?": the answer is confirmed as it is."""
        if await self.repo.confirm_answer(user_of(owner), question_key, now=self.clock.now()) is None:
            raise NotFound("No saved answer for that question")
        return await self.details(owner)

    async def first_check(
        self, owner: str, body: FirstCheckRequest, *, account_email: str | None = None
    ) -> YourDetails:
        """The 30-second first check: the notice period, working in India without sponsorship, corrections to what
        the resume said, and the safe defaults. India only: the first check never stores another country."""
        user_id = user_of(owner)
        if not body.skipped:
            writes: list[tuple[str, str]] = []
            if body.noticePeriod and body.noticePeriod.strip():
                writes.append(("noticePeriod", body.noticePeriod.strip()[:APPLY_DETAIL_MAX_CHARS]))
            auth = body.workAuthorization
            if auth is not None:
                if auth.country.strip().upper() != "IN":
                    raise BadRequest(
                        "The first check asks about India only; other countries are asked on their forms."
                    )
                if auth.authorised.strip():
                    writes.append(("workAuthorization:IN", auth.authorised.strip()[:APPLY_DETAIL_MAX_CHARS]))
                if auth.needsSponsorship and auth.needsSponsorship.strip():
                    writes.append(
                        ("needsSponsorship:IN", auth.needsSponsorship.strip()[:APPLY_DETAIL_MAX_CHARS])
                    )
            for edit in body.edits or []:
                value = edit.value.strip()[:APPLY_ANSWER_MAX_CHARS]
                if edit.questionKey.strip().partition(":")[0] in COUNTRY_KEYS:
                    raise BadRequest(
                        "The first check asks about India only; other countries are asked on their forms."
                    )
                if value:
                    writes.append((self._bank_key(edit.questionKey), value))
            for key, value in writes:  # every value is checked before any is written
                self._check_value(key, value, account_email)
            profile_id = await self._latest_profile_id(owner)
            have = await self.repo.get_answers(user_id, [k for k, _ in FIRST_CHECK_DEFAULTS])
            for key, value in writes:
                question = _BANK[key].question if key in _BANK else key
                await self._bank_put(user_id, key, value, question=question, source="first_check")
                await self._remember_fact(user_id, profile_id, key, value)
            written = {k for k, _ in writes}
            for key, default in FIRST_CHECK_DEFAULTS:
                if key not in have and key not in written:
                    await self._bank_put(user_id, key, default, question=_BANK[key].question, source="default",
                                         ask_each_time=default is None)  # fmt: skip
        await self.repo.mark_first_check(user_id, now=self.clock.now())
        return await self.details(owner)

    async def set_self_id(self, owner: str, on: bool) -> YourDetails:
        await self.repo.set_self_id(user_of(owner), on, now=self.clock.now())
        return await self.details(owner)

    async def put_resume(self, owner: str, name: str | None, data: bytes | None) -> YourDetails:
        """The default resume, checked by its content: a PDF or a DOCX of at most 5 MB (``data=None`` removes it)."""
        user_id = user_of(owner)
        now = self.clock.now()
        if data is None:
            await self.repo.put_resume(owner, user_id=user_id, name=None, media_type=None, data=None, now=now)
            return await self.details(owner)
        if len(data) > APPLY_RESUME_MAX_BYTES:
            raise PayloadTooLarge("That file is over 5 MB.")
        if not data:
            raise BadRequest("The file is empty.")
        media_type = sniff_resume(data)
        if media_type is None:
            raise BadRequest("Upload your resume as a PDF or DOCX file.")
        clean = (name or "resume").strip()[:120] or "resume"
        if not clean.lower().endswith(RESUME_EXT[media_type]):
            clean += RESUME_EXT[media_type]
        await self.repo.put_resume(
            owner, user_id=user_id, name=clean, media_type=media_type, data=data, now=now,
            sha256=hashlib.sha256(data).hexdigest(),
        )  # fmt: skip
        return await self.details(owner)


__all__ = [
    "ALREADY_APPLIED",
    "APPLY_REF_PREFIX",
    "BANK_QUESTIONS",
    "ID_REFUSED",
    "NEVER_STORED_LABELS",
    "NOT_FOUND",
    "NOT_SAME_POSTING",
    "ApplyService",
    "Inspected",
    "MatchInfo",
    "StartRefused",
    "apply_ref",
    "fact_allowed",
    "fact_sentence",
    "id_shaped",
    "own_email",
    "sniff_resume",
    "summary_of",
    "user_of",
]
