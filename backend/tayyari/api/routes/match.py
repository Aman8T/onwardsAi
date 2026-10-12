"""The posting-match routes: match a posting, read it back, answer its questions, build the resume and the cover note,
download them.

    POST  /api/match                       {"profileId", "text"?, "url"?} -> NDJSON `posting`, `gates`, `coverage`
                                           (one line per batch), `radar`, `questions`, `done` {matchId} (or `error`)
    GET   /api/match/{id}                  `MatchDetail`: the posting (with its text), the facts it cites, the result
    GET   /api/matches                     `MatchList`: the person's matches, newest first
    POST  /api/match/{id}/answers          {"answers": [{"questionId", "text"}]} -> `MatchDetail`, re-checked
    POST  /api/match/{id}/resume           NDJSON `variant`, `done`; the stored resume when there is one
    GET   /api/match/{id}/resume           {"variant"} (404 until one is built)
    PATCH /api/match/{id}/resume           {"decisions": [DecisionChoice]} -> {"variant"}, re-verified
    GET   /api/match/{id}/resume.pdf|.docx the file; 409 while the resume has lines waiting for the person
    POST  /api/match/{id}/cover            {"cover"}; the stored note when there is one (422 if nothing supports one)
    GET   /api/match/{id}/cover            {"cover"} (404 until one is written)
    GET   /api/match/{id}/cover.pdf        the file

Every route needs a sign-in (401), unless anonymous matching is on (`Container.match_anonymous`: the setting
``TAYYARI_MATCH__ALLOW_ANONYMOUS``, by default on exactly when the server offers no way to sign in). A signed-in
account answers for its own profiles and matches only: an id that is someone else's is a 404, exactly like one that does
not exist. With anonymous matching on, a visitor with no sign-in may match against a profile nobody has claimed or the
sample, and may open a match made that way (``matches.user_id`` NULL) by its id, for as long as its profile stays
unclaimed or a sample run (`api.matching.open_match`); `GET /api/matches` has no identity to list by and answers an
empty list. Claiming the profile (`POST /api/me/claim`) gives those matches to the account.

Starting a match is the only thing that costs an entitlement. In the order of `routes/portfolio.py`, all answered as
JSON before the stream starts: ``shed`` (in-memory, before the body is read) -> sign-in, or anonymous matching allowed
(401) -> body (415 / 413 / 408 / 400) -> the profile is the account's, or open to a visitor with no sign-in (404) -> the
client's counters, and the account's (429 with ``Retry-After``) -> the posting's text (400 / 422; a link goes through
the SSRF-guarded ingest, LinkedIn and Naukri are refused before any fetch) -> for a visitor with no sign-in, the
client's daily allowance (``TAYYARI_GUARD__ANON_MATCH_PER_DAY``, 429) -> the spend reservation (503) ->
``entitlements.consume`` (402; never called without an account). From there a failure gives the entitlement back (see
`api.matching`). Re-checks after answers, the resume, the cover note and the downloads never consume and are not
counted; the resume and the cover note reserve spend because they call a model, and everything else only ``shed``s in
the ``chat`` bucket like the other cheap routes.

Failures before a stream starts are JSON ``{"error": "<sentence>"}``, except the 402 whose body is
``{"error": "payment_required", "reason": ...}`` (a code the web app turns into the pricing panel).
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.responses import Response

from tayyari.api.deps import get_request_context, user_or_anonymous
from tayyari.api.lifespan import get_container
from tayyari.api.matching import (
    MATCH_NOT_FOUND,
    PROFILE_NOT_FOUND,
    Charge,
    Workspace,
    book_json,
    call_context,
    download_name,
    fact_of_answer,
    model_errors,
    open_match,
    posting_text_of,
    remember_new_facts,
    stream_match,
    stream_resume,
    variant_status,
    with_checks,
    with_profile_facts,
)
from tayyari.api.ndjson import NdjsonResponse
from tayyari.api.routes._body import read_model
from tayyari.api.routes.analyze import HeldResponse
from tayyari.api.security import subnet_hash
from tayyari.container import Container
from tayyari.contract.match import CoverNote, ResumeVariant
from tayyari.contract.match_api import (
    AnswersRequest,
    CoverResponse,
    DecisionsRequest,
    MatchDetail,
    MatchList,
    MatchListItem,
    MatchRequest,
    VariantResponse,
)
from tayyari.contract.models import Profile
from tayyari.contract.requests import RequestContext
from tayyari.core.errors import AppError, BadRequest, Conflict, NotFound
from tayyari.core.ids import is_valid_id, new_match_id
from tayyari.guard.ratelimit import Route
from tayyari.llm.types import CallContext
from tayyari.storage.ports import StoredMatch

router = APIRouter()

TAG: Final = "match"
NO_STORE: Final = {"Cache-Control": "no-store"}
MATCH_BODY_MAX_BYTES: Final = 128 * 1024
"""A posting of 20,000 characters is at most 80 KB as UTF-8 (about 120 KB when every character is escaped)."""
SMALL_BODY_MAX_BYTES: Final = 32 * 1024
"""Answers (three of at most 1,500 characters) and resume decisions (at most forty short ones)."""
NO_RESUME: Final = "No resume has been built for this match yet."
NO_COVER: Final = "No cover note has been written for this match yet."
MATCH_CHANGED: Final = "This match changed in another tab. Reload it and try again."
NO_OPEN_QUESTIONS: Final = "Those questions are not open on this match any more. Reload it to see what is."
PDF: Final = "application/pdf"
DOCX: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FIGURE_QUESTION: Final = "A figure for a line of your resume"


def _shed(container: Container, rc: RequestContext, route: Route = "chat") -> None:
    container.rate_limiter.shed(
        route, rc.ip_hash, ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip)
    )


def _require_id(value: str, message: str) -> None:
    if not is_valid_id(value):
        raise NotFound(message)


def _json(data: dict[str, object]) -> JSONResponse:
    return JSONResponse(data, headers=NO_STORE)


def _detail(ws: Workspace) -> MatchDetail:
    return MatchDetail(
        id=ws.id,
        createdAt=ws.stored.created_at.isoformat(),
        posting=ws.posting,
        facts=ws.book.facts,
        result=ws.result,
    )


def _ctx(container: Container, rc: RequestContext, stored: StoredMatch) -> CallContext:
    return call_context(container, rc.request_id, rc.ip_hash, stored.profile_id)


async def _open(request: Request, match_id: str) -> tuple[Container, RequestContext, Workspace]:
    """The cheap routes' common start: shed, sign-in (or anonymous matching), id shape, the match this caller may open."""
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc)
    user = await user_or_anonymous(request, anonymous_allowed=container.match_anonymous)
    _require_id(match_id, MATCH_NOT_FOUND)
    return container, rc, await open_match(container, None if user is None else user.id, match_id)


# ───────────────────────── start a match ─────────────────────────


@router.post("/api/match")
async def start_match(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    limiter = container.rate_limiter
    ip48 = subnet_hash(container.ip_hash_key, rc.client_ip)
    limiter.shed("match", rc.ip_hash, ip_hash_48=ip48)
    user = await user_or_anonymous(request, anonymous_allowed=container.match_anonymous)
    body = await read_model(request, container, MatchRequest, max_bytes=MATCH_BODY_MAX_BYTES)
    _require_id(body.profileId, PROFILE_NOT_FOUND)
    if user is not None:
        owned = await container.repos.profiles.get_owned_for_chat(user.id, body.profileId)
        if owned is None:
            raise NotFound(PROFILE_NOT_FOUND)
        profile_doc, context, shares_facts = owned[0], owned[1], True
    else:
        # Nobody has claimed it, or it is a sample run: what a visitor with no sign-in may match against.
        open_profile = await container.repos.profiles.get_open_for_chat(body.profileId)
        if open_profile is None:
            raise NotFound(PROFILE_NOT_FOUND)
        profile_doc, context, shares_facts = (
            open_profile.profile,
            open_profile.context,
            not open_profile.is_sample,
        )
    await limiter.check("match", rc.ip_hash, ip_hash_48=ip48, memory=False)
    if user is not None:
        await limiter.check("match", f"u:{user.id}", memory=False, globally=False)
    text = await posting_text_of(container, body)
    try:
        profile = Profile.from_dict(profile_doc)
    except ValidationError:
        raise NotFound(PROFILE_NOT_FOUND) from None
    if user is None:
        # With no account there is nothing to charge: the client's own daily allowance is the meter. It is counted
        # only now, so a posting that could not be read does not use up one of the few runs.
        await limiter.check("match_anon", rc.ip_hash, ip_hash_48=ip48, memory=False)

    match_id = new_match_id()
    user_id = None if user is None else user.id
    charge = Charge(container, user_id, match_id)
    held = AsyncExitStack()
    try:
        await held.enter_async_context(container.spend_guard.reserve(container.settings.guard.est_match_usd))
        if user_id is not None:
            await container.entitlements.consume(user_id, match_id)
            held.push_async_callback(charge.refund)
        events = stream_match(
            container,
            user_id=user_id,
            profile=profile,
            context=context,
            text=text,
            match_id=match_id,
            charge=charge,
            call_ctx=call_context(container, rc.request_id, rc.ip_hash, profile.id),
            shares_facts=shares_facts,
        )
        return HeldResponse(request, events, TAG, held)
    except BaseException:
        await held.aclose()
        raise


# ───────────────────────── read ─────────────────────────


@router.get("/api/matches")
async def list_matches(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc)
    user = await user_or_anonymous(request, anonymous_allowed=container.match_anonymous)
    # A visitor with no sign-in has no identity to list by: the web app keeps its own list of the ids it was given.
    rows = [] if user is None else await container.repos.matches.list_for_user(user.id)
    listing = MatchList(
        matches=[
            MatchListItem(
                id=r.id,
                title=r.title,
                company=r.company,
                band=r.band,  # type: ignore[arg-type]  # validated by the model
                createdAt=r.created_at.isoformat(),
            )
            for r in rows
        ]
    )
    return _json(listing.to_dict())


@router.get("/api/match/{match_id}")
async def get_match(match_id: str, request: Request) -> JSONResponse:
    _, _, ws = await _open(request, match_id)
    return _json(_detail(ws).to_dict())


# ───────────────────────── answers ─────────────────────────


@router.post("/api/match/{match_id}/answers")
async def post_answers(match_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc)
    user = await user_or_anonymous(request, anonymous_allowed=container.match_anonymous)
    _require_id(match_id, MATCH_NOT_FOUND)
    body = await read_model(request, container, AnswersRequest, max_bytes=SMALL_BODY_MAX_BYTES)
    ws = await with_profile_facts(
        container, await open_match(container, None if user is None else user.id, match_id)
    )

    open_questions = {q.id: q for q in ws.result.questions}
    requirements = {r.id: r for r in ws.posting.requirements}
    given = []
    for item in body.answers:
        text, question = item.text.strip(), open_questions.get(item.questionId)
        if text and question is not None:
            given.append((question, text, fact_of_answer(ws.id, question, text, requirements)))
    if not given:
        raise BadRequest(NO_OPEN_QUESTIONS)

    if ws.shares_facts:  # a sample run keeps what was said on its own match only
        for question, text, fact in given:
            await container.repos.user_facts.add(
                ws.stored.user_id,
                ws.stored.profile_id,
                fact,
                question=question.text,
                answer=text,
                match_id=ws.id,
            )
    book = ws.book.with_facts([fact for _, _, fact in given])
    profile_doc = await container.repos.profiles.get_profile(ws.stored.profile_id)
    if profile_doc is None:
        raise NotFound(MATCH_NOT_FOUND)
    with model_errors():
        result = await container.match.recheck(
            profile=Profile.from_dict(profile_doc),
            posting=ws.posting,
            book=book,
            previous=ws.result,
            call_ctx=_ctx(container, rc, ws.stored),
            requirement_ids={q.requirementId for q, _, _ in given if q.requirementId},
            gate_ids={q.gateId for q, _, _ in given if q.gateId},
        )
    saved = await container.repos.matches.save_result(
        ws.id,
        ws.stored.user_id,
        expected_version=ws.stored.version,
        facts=book_json(book),
        result=result.to_dict(),
        band=result.band,
        blocked=result.blocked,
        version=result.version,
        now=container.clock.now(),
    )
    if not saved:
        raise Conflict(MATCH_CHANGED, code="match_changed")
    return _json(_detail(Workspace(ws.stored, ws.posting, result, book, ws.shares_facts)).to_dict())


# ───────────────────────── resume ─────────────────────────


@router.post("/api/match/{match_id}/resume")
async def build_resume(match_id: str, request: Request) -> Response:
    container, rc, ws = await _open(request, match_id)
    ws = await with_profile_facts(container, ws)
    call_ctx = _ctx(container, rc, ws.stored)
    if await container.repos.matches.get_variant(ws.id, "resume") is not None:
        return NdjsonResponse(request, stream_resume(container, ws, call_ctx), TAG)
    held = AsyncExitStack()
    try:
        await held.enter_async_context(container.spend_guard.reserve(container.settings.guard.est_match_usd))
        return HeldResponse(request, stream_resume(container, ws, call_ctx), TAG, held)
    except BaseException:
        await held.aclose()
        raise


@router.get("/api/match/{match_id}/resume")
async def get_resume(match_id: str, request: Request) -> JSONResponse:
    container, _, ws = await _open(request, match_id)
    stored = await container.repos.matches.get_variant(ws.id, "resume")
    if stored is None:
        raise NotFound(NO_RESUME)
    return _json(VariantResponse(variant=ResumeVariant.model_validate(stored.spec)).to_dict())


@router.patch("/api/match/{match_id}/resume")
async def settle_resume(match_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc)
    user = await user_or_anonymous(request, anonymous_allowed=container.match_anonymous)
    _require_id(match_id, MATCH_NOT_FOUND)
    body = await read_model(request, container, DecisionsRequest, max_bytes=SMALL_BODY_MAX_BYTES)
    ws = await with_profile_facts(
        container, await open_match(container, None if user is None else user.id, match_id)
    )
    stored = await container.repos.matches.get_variant(ws.id, "resume")
    if stored is None:
        raise NotFound(NO_RESUME)
    outcome = container.match.settle_resume(
        ResumeVariant.model_validate(stored.spec), ws.book, ws.posting, ws.result, body.decisions
    )
    if outcome.refused:
        # Nothing is applied when any answer does not fit: the person fixes it and sends it again.
        raise AppError(_sentence(outcome.refused[0]), status=422, code="decision_refused")
    await remember_new_facts(container, ws, outcome.book, question=FIGURE_QUESTION)
    variant = await with_checks(container, outcome.variant, outcome.book)
    await container.repos.matches.put_variant(
        ws.id, "resume", spec=variant.to_dict(), status=variant_status(variant), now=container.clock.now()
    )
    return _json(VariantResponse(variant=variant).to_dict())


def _sentence(refusal: str) -> str:
    """``b3: that is not a figure we can use`` -> ``That is not a figure we can use.`` (the person never sees ids)."""
    reason = refusal.split(": ", 1)[-1].strip().rstrip(".")
    return f"{reason[:1].upper()}{reason[1:]}." if reason else "That answer does not fit this line."


def _file(content: bytes, media_type: str, name: str) -> Response:
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Robots-Tag": "noindex",
            **NO_STORE,
        },
    )


async def _download_resume(request: Request, match_id: str, extension: str) -> Response:
    container, _, ws = await _open(
        request, match_id
    )  # a download only reads: the stored book is what it checks
    stored = await container.repos.matches.get_variant(ws.id, "resume")
    if stored is None:
        raise NotFound(NO_RESUME)
    variant = ResumeVariant.model_validate(stored.spec)
    exported = await container.match.export_resume(
        variant, ws.book
    )  # ExportBlocked (409) while lines are open
    if exported.variant.lint != variant.lint:
        await container.repos.matches.put_variant(
            ws.id,
            "resume",
            spec=exported.variant.to_dict(),
            status=variant_status(exported.variant),
            now=container.clock.now(),
        )
    pdf = extension == "pdf"
    return _file(
        exported.files.pdf if pdf else exported.files.docx,
        PDF if pdf else DOCX,
        download_name(ws.book.name, "resume", extension),
    )


@router.get("/api/match/{match_id}/resume.pdf")
async def resume_pdf(match_id: str, request: Request) -> Response:
    return await _download_resume(request, match_id, "pdf")


@router.get("/api/match/{match_id}/resume.docx")
async def resume_docx(match_id: str, request: Request) -> Response:
    return await _download_resume(request, match_id, "docx")


# ───────────────────────── cover note ─────────────────────────


@router.post("/api/match/{match_id}/cover")
async def write_cover(match_id: str, request: Request) -> JSONResponse:
    container, rc, ws = await _open(request, match_id)
    existing = await container.repos.matches.get_variant(ws.id, "cover")
    if existing is not None:
        try:
            return _json(CoverResponse(cover=CoverNote.model_validate(existing.spec)).to_dict())
        except ValidationError:
            pass  # stored in a shape this build cannot read: write it again
    ws = await with_profile_facts(container, ws)
    async with container.spend_guard.reserve(container.settings.guard.est_match_usd):
        with model_errors():
            written = await container.match.compose_cover(
                book=ws.book, posting=ws.posting, result=ws.result, call_ctx=_ctx(container, rc, ws.stored)
            )
            exported = await container.match.export_cover(written.note, ws.book, ws.posting)
    await container.repos.matches.put_variant(
        ws.id, "cover", spec=exported.note.to_dict(), status="ready", now=container.clock.now()
    )
    return _json(CoverResponse(cover=exported.note).to_dict())


@router.get("/api/match/{match_id}/cover")
async def get_cover(match_id: str, request: Request) -> JSONResponse:
    container, _, ws = await _open(request, match_id)
    stored = await container.repos.matches.get_variant(ws.id, "cover")
    if stored is None:
        raise NotFound(NO_COVER)
    return _json(CoverResponse(cover=CoverNote.model_validate(stored.spec)).to_dict())


@router.get("/api/match/{match_id}/cover.pdf")
async def cover_pdf(match_id: str, request: Request) -> Response:
    container, _, ws = await _open(request, match_id)
    stored = await container.repos.matches.get_variant(ws.id, "cover")
    if stored is None:
        raise NotFound(NO_COVER)
    note = CoverNote.model_validate(stored.spec)
    exported = await container.match.export_cover(note, ws.book, ws.posting)
    if exported.note.lint != note.lint:
        await container.repos.matches.put_variant(
            ws.id, "cover", spec=exported.note.to_dict(), status="ready", now=container.clock.now()
        )
    return _file(exported.files.pdf, PDF, download_name(ws.book.name, "cover-note", "pdf"))


__all__ = [
    "DOCX",
    "MATCH_BODY_MAX_BYTES",
    "PDF",
    "SMALL_BODY_MAX_BYTES",
    "router",
]
