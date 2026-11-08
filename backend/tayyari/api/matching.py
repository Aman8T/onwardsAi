"""The posting match between the routes and the services: where a posting's text comes from, how a stored match is
opened again, how the analysis becomes an NDJSON stream that is paid for and given back when it fails, and the small
conversions the routes share.

Paying for a match (`stream_match`)::

    entitlements.consume(user, match_id)   before the response starts: 402 as plain JSON when there is nothing left
    MatchService.analyse(...)              events -> NDJSON lines as they come
    matches.create(...)                    the stored match, with the final result
    done                                   the match id (the charge is kept from the moment the row exists)

Anything that goes wrong after the charge (the posting has no requirements, a model is down, the client hangs up, the
row cannot be written) gives the charge back with ``entitlements.refund(user, match_id)``, which is idempotent: the
stream does it, and the response does it once more when it ends, in case the stream never got to run. A refund is
awaited under `asyncio.shield`, so a client that has gone away cannot cut it short.

A visitor with no sign-in (anonymous matching, `Container.match_anonymous`) has no entitlement to take or give back:
the route never calls ``consume``, and a `Charge` with no account does nothing when asked to refund. Such a match is
stored with ``user_id`` NULL; `open_match` says who may open it again.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final

from pydantic import ValidationError

from tayyari import ingest
from tayyari.api.errors import user_message_for
from tayyari.apply.service import fact_allowed
from tayyari.container import Container
from tayyari.contract.match import (
    Fact,
    MatchFactKind,
    MatchResult,
    Posting,
    Question,
    Requirement,
    ResumeVariant,
)
from tayyari.contract.match_api import (
    MatchCoverageEvent,
    MatchDoneEvent,
    MatchErrorCode,
    MatchErrorEvent,
    MatchGatesEvent,
    MatchPostingEvent,
    MatchQuestionsEvent,
    MatchRadarEvent,
    MatchRequest,
    MatchStreamEvent,
    ResumeDoneEvent,
    ResumeVariantEvent,
)
from tayyari.contract.models import Profile
from tayyari.contract.stored import StoredContext
from tayyari.core.errors import (
    AppError,
    BadRequest,
    NotFound,
    PaymentRequired,
    RateLimited,
    Unauthorized,
)
from tayyari.core.logging import get_logger
from tayyari.ingest.classify import host_is, normalize_url, strip_www
from tayyari.llm.errors import ClaudeError
from tayyari.llm.types import CallContext
from tayyari.match.bullets import export_blockers
from tayyari.match.errors import (
    ExportBlocked,
    NoRequirementsFound,
    PostingLinkBlocked,
    PostingLinkUnreadable,
    PostingTextError,
)
from tayyari.match.facts import user_fact
from tayyari.match.gate import decide
from tayyari.match.models import FactBook
from tayyari.match.posting import clean_posting_text
from tayyari.match.service import (
    CoverageBatch,
    GatesReady,
    MatchEvent,
    MatchOutcome,
    PostingReady,
    RadarReady,
)
from tayyari.match.text import posting_hash
from tayyari.storage.ports import StoredMatch

log = get_logger(__name__)

MATCH_NOT_FOUND: Final = "Match not found"
PROFILE_NOT_FOUND: Final = "Profile not found"

BOARDS_THAT_FORBID_READING: Final[tuple[tuple[str, str], ...]] = (
    ("linkedin.com", "LinkedIn"),
    ("lnkd.in", "LinkedIn"),
    ("naukri.com", "Naukri"),
    ("indeed.com", "Indeed"),
    ("glassdoor.com", "Glassdoor"),
    ("foundit.in", "foundit"),
    ("shine.com", "Shine"),
)
"""Job boards whose terms forbid automated reading. A link on one of them is refused before anything is fetched."""

_refunds: set[asyncio.Task[bool]] = set()
"""Refunds in flight, held so a task that nobody awaits any more is not collected before it finishes."""


# ───────────────────────── the posting's text ─────────────────────────


def _blocked_message(name: str) -> str:
    return (
        f"{name} does not allow automated reading of its pages, so Leafout does not open that link. "
        "Open the posting there, copy its text and paste it here instead."
    )


async def fetch_posting_text(url: str, manual_only: Sequence[str] = ()) -> str:
    """The text of a public page at ``url`` through the SSRF-guarded ingest. 400 for something that is not a web
    link, 422 for a job board that forbids reading (`BOARDS_THAT_FORBID_READING`, and ``manual_only``: the sites
    auto-apply never opens, `ApplySettings.manual_only_domains`, so a pasted application link can never make the match
    read one), a page that cannot be read, or one with no text."""
    parsed = normalize_url(url)
    if parsed is None:
        raise BadRequest("That does not look like a web link. Paste the text of the posting instead.")
    host = strip_www(parsed.hostname)
    for domain, name in BOARDS_THAT_FORBID_READING:
        if host_is(host, domain):
            raise PostingLinkBlocked(_blocked_message(name))
    for domain in manual_only:
        clean = domain.strip().lower()
        if clean and host_is(host, strip_www(clean)):
            raise PostingLinkBlocked(_blocked_message("This site"))
    doc = await ingest.ingest_web(parsed.url, "link", "Job posting", "Job posting", lambda _line: None)
    if not doc.ok or not doc.text.strip():
        reason = (doc.error or "").strip().rstrip(".")
        raise PostingLinkUnreadable(
            f"{reason}. You can paste the text of the posting instead." if reason else None
        )
    return doc.text


async def posting_text_of(container: Container, body: MatchRequest) -> str:
    """The cleaned text of the posting a request names: pasted text wins over a link. Raises `PostingTextError`
    (400) for text that is empty, too short or too long, and the link errors of `fetch_posting_text`."""
    limit = container.settings.match.max_posting_chars
    if body.text is not None and body.text.strip():
        text = body.text
    elif body.url is not None and body.url.strip():
        text = await fetch_posting_text(body.url, container.settings.apply.manual_only_domains)
    else:
        raise BadRequest("Paste the job posting, or give a link to it.")
    if len(text) > limit:
        raise PostingTextError(
            f"That posting is longer than {limit:,} characters. Paste just the role description."
        )
    return clean_posting_text(text)


# ───────────────────────── a stored match ─────────────────────────


@dataclass(slots=True)
class Workspace:
    """A stored match with everything the follow-up routes need, validated."""

    stored: StoredMatch
    posting: Posting
    result: MatchResult
    book: FactBook
    shares_facts: bool = True
    """False when the match is on a sample run, which everyone who holds its id shares: what a visitor says there stays
    on their own match (its book) and is not kept for the profile's other matches, where it would reach other people."""

    @property
    def id(self) -> str:
        return self.stored.id


def book_json(book: FactBook) -> dict[str, object]:
    """The `FactBook` as the JSON a match row keeps."""
    return book.model_dump(mode="json", exclude_none=True)


async def _find_match(
    container: Container, user_id: str | None, match_id: str
) -> tuple[StoredMatch, bool] | None:
    """The stored match a request from ``user_id`` (None: no sign-in) may open, and whether its profile's facts are
    shared between its matches, or None.

    An account's own match is open to that account. A match made without an account (``user_id`` NULL) is open to
    whoever holds its id, signed in or not, while anonymous matching is on and its profile is still unclaimed or a
    sample run: the id is 128 random bits, like a profile's, and is the only thing that protects it. Claiming the profile
    gives such a match to the account (`UserRepo.claim_profile`); one that slipped through is served to nobody else.
    Another account's match is not found, exactly like an id that does not exist."""
    if user_id is not None:
        own = await container.repos.matches.get(match_id, user_id)
        if own is not None:
            return own, True
    if not container.match_anonymous:
        return None
    anonymous = await container.repos.matches.get(match_id, None)
    if anonymous is None:
        return None
    access = await container.repos.profiles.get_access(anonymous.profile_id)
    if access is None or not (access.is_sample or access.user_id is None):
        return None
    return anonymous, not access.is_sample


async def open_match(container: Container, user_id: str | None, match_id: str) -> Workspace:
    """The match with its posting, result and facts as a request from ``user_id`` (None: no sign-in) may open it, or
    `NotFound` (also for another account's match). See `_find_match` for who may."""
    found = await _find_match(container, user_id, match_id)
    if found is None:
        raise NotFound(MATCH_NOT_FOUND)
    stored, shares_facts = found
    posting = await container.repos.postings.get(stored.posting_hash)
    if posting is None:
        log.error("match_posting_missing", match_id=match_id)
        raise NotFound(MATCH_NOT_FOUND)
    try:
        return Workspace(
            stored=stored,
            posting=posting,
            result=MatchResult.model_validate(stored.result),
            book=FactBook.model_validate(stored.facts),
            shares_facts=shares_facts,
        )
    except ValidationError:
        # A match stored in a shape this build cannot read (the contract moved on): it is not served half-understood.
        log.error("match_unreadable", match_id=match_id, version=stored.version)
        raise NotFound(MATCH_NOT_FOUND) from None


async def with_profile_facts(container: Container, ws: Workspace) -> Workspace:
    """``ws`` with the facts the person has given for this profile since (in answers to other matches) added to its
    book. When there are any, the stored book is updated too, so what the app shows is what the checks used. A sample
    run shares nothing between its matches (see `Workspace.shares_facts`)."""
    if not ws.shares_facts:
        return ws
    extra = await container.repos.user_facts.list_for_profile(ws.stored.profile_id)
    merged = ws.book.with_facts(extra)
    if len(merged.facts) == len(ws.book.facts):
        return ws
    await container.repos.matches.save_facts(
        ws.id, ws.stored.user_id, book_json(merged), container.clock.now()
    )
    return Workspace(
        stored=ws.stored, posting=ws.posting, result=ws.result, book=merged, shares_facts=ws.shares_facts
    )


async def remember_new_facts(container: Container, ws: Workspace, book: FactBook, *, question: str) -> None:
    """Store the user facts in ``book`` that ``ws.book`` did not have (a figure given for a resume line) and keep the
    book on the match. The facts are the match's owner's: an account's, or nobody's yet for an anonymous match."""
    if ws.shares_facts:
        have = {f.id for f in ws.book.facts}
        for fact in book.facts:
            if fact.id in have or not all(r.source == "user" for r in fact.refs):
                continue
            answer = fact.refs[0].quote if fact.refs else fact.text
            await container.repos.user_facts.add(
                ws.stored.user_id,
                ws.stored.profile_id,
                fact,
                question=question,
                answer=answer,
                match_id=ws.id,
            )
    await container.repos.matches.save_facts(ws.id, ws.stored.user_id, book_json(book), container.clock.now())


def call_context(
    container: Container, rc_request_id: str, ip_hash: str | None, profile_id: str
) -> CallContext:
    """The correlation data every model call of a match carries (the match code replaces ``purpose``)."""
    return CallContext(
        purpose="match.posting", request_id=rc_request_id, profile_id=profile_id, ip_hash=ip_hash
    )


# ───────────────────────── resume ─────────────────────────


def variant_status(variant: ResumeVariant) -> str:
    """``needs_user`` while the resume has lines waiting for the person (its files cannot be made), else ``ready``."""
    return "needs_user" if export_blockers(variant) else "ready"


async def with_checks(container: Container, variant: ResumeVariant, book: FactBook) -> ResumeVariant:
    """``variant`` with its checklist attached once it can be exported (nothing is waiting for the person), else with
    none: a list left from before the last change would describe text that is no longer there."""
    if export_blockers(variant):
        return variant.model_copy(update={"lint": None})
    try:
        return (await container.match.export_resume(variant, book)).variant
    except ExportBlocked:
        return variant.model_copy(update={"lint": None})
    except Exception:
        # The checks are a convenience on the page; the files are made (and checked) again at download.
        log.warning("resume_checks_failed", exc_info=True)
        return variant.model_copy(update={"lint": None})


async def stream_resume(
    container: Container, ws: Workspace, call_ctx: CallContext
) -> AsyncIterator[MatchStreamEvent]:
    """The resume of a match as ``variant`` then ``done``: the stored one when there is one, else composed now (from
    the cited facts, no entitlement involved) and stored."""
    try:
        variant: ResumeVariant | None = None
        existing = await container.repos.matches.get_variant(ws.id, "resume")
        if existing is not None:
            try:
                variant = ResumeVariant.model_validate(existing.spec)
            except ValidationError:
                log.warning("resume_unreadable", match_id=ws.id)
        if variant is None:
            built = await container.match.compose_resume(
                book=ws.book, posting=ws.posting, result=ws.result, call_ctx=call_ctx
            )
            variant = await with_checks(container, built.variant, ws.book)
            await container.repos.matches.put_variant(
                ws.id,
                "resume",
                spec=variant.to_dict(),
                status=variant_status(variant),
                now=container.clock.now(),
            )
        yield ResumeVariantEvent(variant=variant)
        yield ResumeDoneEvent()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield error_event(exc)


_NOT_FILENAME = re.compile(r"[^a-z0-9]+")


def download_name(person: str, what: str, extension: str) -> str:
    """``meera-nair-resume.pdf``: the person's name in ASCII, what the file is, and its extension."""
    ascii_name = unicodedata.normalize("NFKD", person).encode("ascii", "ignore").decode("ascii").lower()
    slug = _NOT_FILENAME.sub("-", ascii_name).strip("-")[:40] or "leafout"
    return f"{slug}-{what}.{extension}"


# ───────────────────────── answers ─────────────────────────

_FACT_KIND_OF_REQUIREMENT: Final = {
    "skill": "skill",
    "tool": "skill",
    "language": "skill",
    "domain": "skill",
    "cert": "cert",
    "degree": "education",
    "location": "other",
    "auth": "other",
}


def fact_of_answer(
    match_id: str, question: Question, text: str, requirements: dict[str, Requirement]
) -> Fact:
    """The answer to ``question`` as a fact in the person's own words (its source is the person, its quote is
    the answer itself)."""
    if question.requirementId is not None:
        kind = _FACT_KIND_OF_REQUIREMENT.get(
            requirements[question.requirementId].kind if question.requirementId in requirements else "other",
            "accomplishment",
        )
    else:
        kind = "other"
    return user_fact(text, key=f"{match_id}/{question.id}", kind=kind)  # type: ignore[arg-type]


# ───────────────────────── errors ─────────────────────────


def code_of(exc: BaseException) -> MatchErrorCode:
    """The machine code of an `error` event for ``exc``; the web app decides what to show from it."""
    if isinstance(exc, PaymentRequired):
        return "payment_required"
    if isinstance(exc, NoRequirementsFound):
        return "no_requirements"
    if isinstance(exc, ExportBlocked):
        return "export_blocked"
    if isinstance(exc, RateLimited):
        return "rate_limited"
    if isinstance(exc, Unauthorized):
        return "unauthorized"
    return "failed"


def error_event(exc: BaseException) -> MatchErrorEvent:
    """The `error` event for ``exc``. Expected failures are logged quietly; anything else with its traceback."""
    if isinstance(exc, AppError | ClaudeError):
        log.info("match_stream_failed", error_type=type(exc).__name__)
    else:
        log.error("match_stream_crashed", exc_info=(type(exc), exc, exc.__traceback__))
    return MatchErrorEvent(message=user_message_for(exc), code=code_of(exc))


@contextmanager
def model_errors() -> Iterator[None]:
    """A model failure inside a route that does not stream is a 502 with the sentence the person may read, not a
    generic 500."""
    try:
        yield
    except ClaudeError as exc:
        log.info("match_model_failed", error_type=type(exc).__name__)
        raise AppError(exc.user_message, status=502, code="model_unavailable") from exc


# ───────────────────────── events ─────────────────────────


def to_wire(event: MatchEvent) -> MatchStreamEvent:
    """A `MatchService` event as the line the web app reads (``answers`` is ``gates``, ``rows`` is ``coverage``)."""
    if isinstance(event, PostingReady):
        return MatchPostingEvent(posting=event.posting, facts=event.facts)
    if isinstance(event, GatesReady):
        return MatchGatesEvent(gates=event.answers, blocked=event.blocked)
    if isinstance(event, CoverageBatch):
        return MatchCoverageEvent(coverage=event.rows)
    if isinstance(event, RadarReady):
        return MatchRadarEvent(radar=event.radar)
    return MatchQuestionsEvent(questions=event.questions)


# ───────────────────────── the paid stream ─────────────────────────


class Charge:
    """One entitlement taken for a match, and the way back. ``keep()`` once the match is stored. With no account
    (``user_id`` None: an anonymous match) nothing was taken, so there is nothing to give back."""

    def __init__(self, container: Container, user_id: str | None, match_id: str) -> None:
        self._container = container
        self._user_id = user_id
        self._match_id = match_id
        self._kept = False

    def keep(self) -> None:
        self._kept = True

    async def refund(self) -> None:
        """Give the charge back unless it was kept. Safe to call again and from a cancelled task."""
        if self._kept or self._user_id is None:
            return
        task = asyncio.ensure_future(self._give_back())
        _refunds.add(task)
        task.add_done_callback(_refunds.discard)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # The task is going away; the refund carries on by itself and is logged when it ends.
            pass

    async def _give_back(self) -> bool:
        if self._user_id is None:  # pragma: no cover - refund() returns before it gets here
            return False
        try:
            given = await self._container.entitlements.refund(self._user_id, self._match_id)
        except Exception:
            log.error("match_refund_failed", match_id=self._match_id, exc_info=True)
            return False
        self._kept = True  # settled either way: asking again would only repeat it
        return given


async def stream_match(
    container: Container,
    *,
    user_id: str | None,
    profile: Profile,
    context: StoredContext,
    text: str,
    match_id: str,
    charge: Charge,
    call_ctx: CallContext,
    shares_facts: bool = True,
) -> AsyncIterator[MatchStreamEvent]:
    """Run the analysis of ``text`` for ``profile`` and stream it; store the match (for ``user_id``, or for nobody
    when it is None); give the charge back on any failure. See the module docstring. ``shares_facts`` False (a
    sample run) leaves out what other visitors said about the same profile."""
    queue: asyncio.Queue[MatchEvent | None] = asyncio.Queue()
    task: asyncio.Task[MatchOutcome] | None = None
    try:
        user_facts = await container.repos.user_facts.list_for_profile(profile.id) if shares_facts else []

        async def analyse() -> MatchOutcome:
            try:
                return await container.match.analyse(
                    profile=profile,
                    context=context,
                    posting_text=text,
                    call_ctx=call_ctx,
                    user_facts=user_facts,
                    emit=queue.put_nowait,
                )
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(analyse(), name="match-analyse")
        while (event := await queue.get()) is not None:
            yield to_wire(event)
        outcome = await task
        now = container.clock.now()
        await container.repos.postings.put(outcome.posting)  # the match row points at it
        stored = await container.repos.matches.create(
            match_id=match_id,
            user_id=user_id,
            profile_id=profile.id,
            posting_hash=outcome.posting.hash,
            facts=book_json(outcome.book),
            result=outcome.result.to_dict(),
            band=outcome.result.band,
            blocked=outcome.result.blocked,
            version=outcome.result.version,
            now=now,
        )
        charge.keep()
        log.info(
            "match_done",
            match_id=match_id,
            band=outcome.result.band,
            requirements=len(outcome.posting.requirements),
            facts=len(outcome.book.facts),
            refused_in_posting=len(outcome.report.dropped),
            refused_in_facts=len(outcome.book.dropped),
        )
        yield MatchDoneEvent(matchId=stored.id, createdAt=stored.created_at.isoformat())
    except asyncio.CancelledError:
        await charge.refund()
        raise
    except Exception as exc:
        await charge.refund()
        yield error_event(exc)
    finally:
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


# ───────────────────────── what auto-apply reads of a match ─────────────────────────


def gate_reasons(ws: Workspace) -> tuple[bool, tuple[str, ...]]:
    """``(blocked, the questions of the gates that block)``, decided in code by `match.gate.decide` from the stored
    answers (the stored ``blocked`` flag counts too)."""
    decision = decide(ws.posting.gates, ws.result.gates)
    questions = {g.id: g.question for g in ws.posting.gates}
    reasons = tuple(questions[g] for g in decision.blocking if g in questions)
    return ws.result.blocked or decision.blocked, reasons


def proves_posting(ws: Workspace, text: str | None) -> bool:
    """Is ``text`` the posting this match read? Its hash (over the cleaned, folded text) must be the match's."""
    if not text or not text.strip():
        return False
    try:
        cleaned = clean_posting_text(text)
    except PostingTextError:
        return False
    return ws.stored.posting_hash in (posting_hash(cleaned), posting_hash(text))


async def listing_url(container: Container, ws: Workspace, listing_id: str, run_id: str | None) -> str | None:
    """The employer's link of the Discover listing ``listing_id``, only when it is proved to be the matched posting:
    the search run's copy of the posting (``run_id``, the match's profile's run) hashes to the match's posting, else
    the stored listing's text does. None when nothing proves it (a listing stored without its text included: a title
    alone is no proof): the start then has no form address."""
    if run_id:
        held = await container.jobsearch.posting(ws.stored.profile_id, run_id, listing_id)
        if held is not None and held.applyUrl and proves_posting(ws, held.postingText):
            return held.applyUrl
    listing = await container.repos.discover.get(listing_id)
    if listing is None or not listing.apply_url:
        return None
    # a listing stored without its text proves nothing: a title alone matches the same role at any employer
    return listing.apply_url if proves_posting(ws, listing.posting_text) else None


class ApplyFactSink:
    """`apply.seams.FactSink` over the profile's user facts: an allowlisted answer (`apply.service.fact_allowed`) kept
    as a fact "from you", so a match stops calling a notice period or a degree a gap. The sentence is built in code by
    the apply service; this only refuses anything off the allowlist. Each question key has one slot: a changed answer
    replaces the earlier fact (never two contradicting facts), and a withdrawn answer removes it."""

    def __init__(self, container: Container) -> None:
        self._container = container

    async def __call__(self, user_id: str, profile_id: str, question_key: str, sentence: str) -> None:
        if not fact_allowed(question_key):
            return
        kind: MatchFactKind = "education" if question_key in _EDUCATION_KEYS else "other"
        fact = user_fact(sentence, key=f"apply/{question_key}", kind=kind)
        await self._container.repos.user_facts.replace(
            user_id, profile_id, fact, question=_slot(question_key), answer=sentence
        )

    async def retract(self, user_id: str, profile_id: str, question_key: str) -> None:
        await self._container.repos.user_facts.remove(profile_id, _slot(question_key))


def _slot(question_key: str) -> str:
    """The one place an apply answer's fact is kept (a match's own facts carry their question text and a match id)."""
    return f"apply/{question_key}"


_EDUCATION_KEYS: Final = frozenset(
    {"highestQualification", "college", "graduationYear", "graduationScore", "class12Score", "class10Score"}
)


__all__ = [
    "BOARDS_THAT_FORBID_READING",
    "MATCH_NOT_FOUND",
    "PROFILE_NOT_FOUND",
    "ApplyFactSink",
    "Charge",
    "Workspace",
    "book_json",
    "call_context",
    "code_of",
    "download_name",
    "error_event",
    "fact_of_answer",
    "fetch_posting_text",
    "gate_reasons",
    "listing_url",
    "model_errors",
    "open_match",
    "posting_text_of",
    "proves_posting",
    "remember_new_facts",
    "stream_match",
    "stream_resume",
    "to_wire",
    "variant_status",
    "with_checks",
    "with_profile_facts",
]
