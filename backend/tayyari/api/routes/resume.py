"""The resume routes (R2, `/resume`: one set of blocks, many resumes; spec in docs/resume.md).

    GET   /api/resumes?profile=<id>          `ResumeList`: the profile's resumes and next roles. Makes the uploaded resume
                                             on the first call (finalised at once, the default)
    GET   /api/resumes/blocks?profile=<id>   `BlocksResponse`: every block the person has, and how many resumes use it
    POST  /api/resumes/path?profile=<id>     `PathRequest` -> `ResumeDetail`: the resume for that next role, the one
                                             already made, else made now (404 for a role the graph does not have)
    GET   /api/resumes/files?profile=<id>    `ResumeFilesResponse`: the profile's resume files (the one it was analysed
                                             from, then each upload) with how each merged
    POST  /api/resumes/upload?profile=<id>   multipart, one file in the field `resume` (PDF, DOCX, TXT, MD; the analysis
                                             upload's size and type limits) -> `UploadResponse`: the file's facts merged
                                             into the profile's blocks by code (`resume.merge`), kept with its bytes and
                                             made a resume of its own. One extraction call: spend is reserved first (twice
                                             when the profile has no uploaded resume yet) and counted in the ``chat``
                                             window
    GET   /api/resumes/{id}                  `ResumeDetail`
    PATCH /api/resumes/{id}                  `ResumeOpsRequest` -> `ResumeDetail`. All operations apply or none: 422
                                             ``op_refused`` names the first that does not fit
    POST  /api/resumes/{id}/finalise         `FinaliseRequest` -> `FinaliseResponse`: the typed lines checked, the answers
                                             applied, and with nothing left the files exported and read back
    POST  /api/resumes/{id}/default          `ResumeDetail`; 409 ``not_finalised`` unless a finalised version exists
    POST  /api/resumes/{id}/grammar          `GrammarResponse`: fixes for lines of the draft whose grammar, spelling or
                                             punctuation can be fixed without changing what they say (one Haiku call; code
                                             drops any fix that changes a fact). Nothing is stored: the person accepts one
                                             with the ``edit`` op. With no model key or on a model failure: no proposals
                                             and ``ran: false``, never a 500
    GET   /api/resumes/{id}/wordings         `WordingsResponse`: the finalised wordings of the same blocks that the
                                             profile's other resumes use, per line (no model call)
    POST  /api/resumes/from-match            `FromMatchRequest` -> `ResumeDetail`: a match's tailored resume saved into
                                             the list as a ``posting`` resume (finalised when the match exported it);
                                             saving the same match again returns the same resume
    GET   /api/resumes/{id}/resume.pdf|.docx the file of the finalised version; 409 ``not_finalised`` without one
    GET   /api/resumes/{id}/original         the bytes the resume came from, with their content type and a safe file name;
                                             404 ``no_original`` for a path or posting resume, and for the first resume of a
                                             profile analysed before originals were kept

Who may call is the posting match's rule (`routes/match.py`): a sign-in, unless anonymous matching is on
(`Container.match_anonymous`), else 401. The profile must be one the caller may open: for an account its own profile
(`ProfileRepo.get_owned_for_chat`); with no sign-in, one nobody has claimed or a sample run
(`ProfileRepo.get_open_for_chat`). A resume is opened through its profile by the same rule (`ProfileRepo.get_access`),
so another account's profile or resume is a 404, exactly like an id that does not exist. On a sample run, which
everyone who holds its id shares, a line the person calls their own stays on that resume and is not kept as a fact of
the profile.

Every route sheds in the in-memory ``chat`` bucket before anything is read, like the match's light routes. Making
something reserves spend first (``TAYYARI_GUARD__EST_MATCH_USD``, 503 at the daily ceiling): the uploaded resume, whose
fact book may have to be read from the sources, and a path resume, which asks the model once for relevance. A resume
that already exists is returned with no reservation and no model call. Edits, Finalise, the default and the downloads
call no model (so do the wordings and saving a match's resume). The grammar check asks the model once: it reserves
spend first (the same estimate) and is counted in the per-address ``chat`` window. Nothing is charged to an account.

The refusals this feature adds carry their code beside the sentence, ``{"error": sentence, "code": code}``:
``op_refused`` (422), ``not_finalised`` and ``resume_changed`` (409: the draft was written from another tab).
Every answer is ``Cache-Control: no-store``.

`resume_in_view` is not a route: `POST /api/chat` calls it when its body names a resume, to decide by this same rule whether
the coach may be shown it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.responses import Response

from tayyari.api.deps import User, get_request_context, unlimited, user_or_anonymous
from tayyari.api.lifespan import get_container
from tayyari.api.matching import (
    MATCH_NOT_FOUND,
    PROFILE_NOT_FOUND,
    call_context,
    download_name,
    model_errors,
    open_match,
)
from tayyari.api.routes._body import read_model
from tayyari.api.routes.match import DOCX, PDF
from tayyari.api.security import subnet_hash
from tayyari.api.uploads import read_upload_form, upload_gate
from tayyari.chat.ports import ResumeInView
from tayyari.container import Container
from tayyari.contract.match import ResumeVariant
from tayyari.contract.models import Profile
from tayyari.contract.requests import RequestContext
from tayyari.contract.resume import FinaliseRequest, FromMatchRequest, PathRequest, ResumeOpsRequest
from tayyari.contract.stored import StoredContext
from tayyari.core.errors import AppError, BadRequest, NotFound
from tayyari.core.ids import is_valid_id
from tayyari.core.logging import get_logger
from tayyari.llm.types import CallContext
from tayyari.resume.arrange import role_terms
from tayyari.resume.errors import ResumeNotFound, RoleNotFound
from tayyari.resume.service import Holder
from tayyari.storage.ports import StoredResume

router = APIRouter()
log = get_logger(__name__)

NO_STORE: Final = {"Cache-Control": "no-store"}
OPS_BODY_MAX_BYTES: Final = 96 * 1024
"""Forty operations of at most 320 characters each (up to four bytes a character, more when escaped)."""
SMALL_BODY_MAX_BYTES: Final = 32 * 1024
"""A role id, or the answers of a finalise call."""
NO_PROFILE: Final = "Say which profile: add ?profile=<id> to the address."
NO_MATCH_RESUME: Final = "That match has no resume yet: open its resume first."
NO_FILE: Final = "Choose a resume file to add."


@dataclass(frozen=True, slots=True)
class _Scope:
    """A profile the caller may open, with what a resume is made from."""

    holder: Holder
    profile: Profile
    context: StoredContext


def _json(data: dict[str, object]) -> JSONResponse:
    return JSONResponse(data, headers=NO_STORE)


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


async def _start(request: Request) -> tuple[Container, RequestContext, User | None]:
    """Every route's start: shed, then a sign-in or anonymous matching (401)."""
    container = get_container(request)
    rc = await get_request_context(request)
    container.rate_limiter.shed(
        "chat",
        rc.ip_hash,
        ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip),
        exempt=await unlimited(request, container),
    )
    user = await user_or_anonymous(request, anonymous_allowed=container.match_anonymous)
    return container, rc, user


async def _open_profile(request: Request, container: Container, user: User | None) -> _Scope:
    """The profile ``?profile=`` names, as this caller may open it (the rule of `POST /api/match`), or 404."""
    profile_id = request.query_params.get("profile", "").strip()
    if not profile_id:
        raise BadRequest(NO_PROFILE)
    if not is_valid_id(profile_id):
        raise NotFound(PROFILE_NOT_FOUND)
    if user is not None:
        owned = await container.repos.profiles.get_owned_for_chat(user.id, profile_id)
        if owned is None:
            raise NotFound(PROFILE_NOT_FOUND)
        doc, context, shares_facts = owned[0], owned[1], True
    else:
        # Nobody has claimed it, or it is a sample run: what a visitor with no sign-in may open.
        open_profile = await container.repos.profiles.get_open_for_chat(profile_id)
        if open_profile is None:
            raise NotFound(PROFILE_NOT_FOUND)
        doc, context, shares_facts = open_profile.profile, open_profile.context, not open_profile.is_sample
    try:
        profile = Profile.from_dict(doc)
    except ValidationError:
        raise NotFound(PROFILE_NOT_FOUND) from None
    holder = Holder(
        profile_id=profile_id, user_id=None if user is None else user.id, shares_facts=shares_facts
    )
    return _Scope(holder=holder, profile=profile, context=context)


async def _open_resume(
    container: Container, user: User | None, resume_id: str
) -> tuple[StoredResume, Holder]:
    """The resume with this id when the caller may open its profile, else 404 (also for another account's)."""
    if not is_valid_id(resume_id):
        raise ResumeNotFound()
    stored = await container.resume.get(resume_id)
    if stored is None:
        raise ResumeNotFound()
    access = await container.repos.profiles.get_access(stored.profile_id)
    if access is None:
        raise ResumeNotFound()
    if user is not None:
        allowed = access.user_id == user.id and not access.is_sample
    else:
        allowed = access.is_sample or access.user_id is None
    if not allowed:
        raise ResumeNotFound()
    holder = Holder(
        profile_id=stored.profile_id,
        user_id=None if user is None else user.id,
        shares_facts=not access.is_sample,
    )
    return stored, holder


def _ctx(container: Container, rc: RequestContext, profile_id: str) -> CallContext:
    return call_context(container, rc.request_id, rc.ip_hash, profile_id)


# ───────────────────────── the list and the blocks ─────────────────────────


@router.get("/api/resumes")
async def list_resumes(request: Request) -> JSONResponse:
    container, rc, user = await _start(request)
    scope = await _open_profile(request, container, user)
    call_ctx = _ctx(container, rc, scope.holder.profile_id)
    if await container.resume.find_upload(scope.holder.profile_id) is not None:
        listing = await container.resume.listing(scope.holder, scope.profile, scope.context, call_ctx)
    else:
        # The first call makes the uploaded resume: its fact book may have to be read from the sources.
        async with container.spend_guard.reserve(container.settings.guard.est_match_usd):
            with model_errors():
                listing = await container.resume.listing(scope.holder, scope.profile, scope.context, call_ctx)
    return _json(listing.to_dict())


@router.get("/api/resumes/blocks")
async def list_blocks(request: Request) -> JSONResponse:
    container, rc, user = await _start(request)
    scope = await _open_profile(request, container, user)
    call_ctx = _ctx(container, rc, scope.holder.profile_id)
    if await container.resume.find_upload(scope.holder.profile_id) is not None:
        blocks = await container.resume.blocks(scope.holder, scope.profile, scope.context, call_ctx)
    else:
        async with container.spend_guard.reserve(container.settings.guard.est_match_usd):
            with model_errors():
                blocks = await container.resume.blocks(scope.holder, scope.profile, scope.context, call_ctx)
    return _json(blocks.to_dict())


@router.get("/api/resumes/files")
async def list_files(request: Request) -> JSONResponse:
    container, _, user = await _start(request)
    scope = await _open_profile(request, container, user)
    return _json((await container.resume.file_list(scope.holder)).to_dict())


@router.post("/api/resumes/upload")
async def upload_resume(request: Request) -> JSONResponse:
    container, rc, user = await _start(request)
    scope = await _open_profile(request, container, user)
    guard = container.settings.guard
    limiter = container.rate_limiter
    free = await unlimited(request, container)
    ip48 = subnet_hash(container.ip_hash_key, rc.client_ip)
    _, upload = await read_upload_form(
        request,
        read_timeout_s=guard.upload_read_timeout_s,
        gate=upload_gate(request, guard),
        client_key=rc.ip_hash,
    )
    if upload is None or not upload.data:
        raise BadRequest(NO_FILE)
    await limiter.check("chat", rc.ip_hash, ip_hash_48=ip48, memory=False, exempt=free)
    # One extraction for the file; the profile's first resume is made too when it does not exist yet.
    made = await container.resume.find_upload(scope.holder.profile_id) is not None
    async with container.spend_guard.reserve(guard.est_match_usd * (1 if made else 2)):
        with model_errors():
            answer = await container.resume.upload_file(
                scope.holder,
                scope.profile,
                scope.context,
                filename=upload.filename,
                content_type=upload.content_type,
                data=upload.data,
                call_ctx=_ctx(container, rc, scope.holder.profile_id),
            )
    return _json(answer.to_dict())


# ───────────────────────── a resume for a next role ─────────────────────────


@router.post("/api/resumes/path")
async def make_path(request: Request) -> JSONResponse:
    container, rc, user = await _start(request)
    body = await read_model(request, container, PathRequest, max_bytes=SMALL_BODY_MAX_BYTES)
    scope = await _open_profile(request, container, user)
    if not any(role.id == body.roleId for role in scope.profile.roles):
        raise RoleNotFound()
    existing = await container.resume.find_path(scope.holder.profile_id, body.roleId)
    if existing is not None:
        return _json(container.resume.detail(existing).to_dict())
    async with container.spend_guard.reserve(container.settings.guard.est_match_usd):
        with model_errors():
            detail = await container.resume.path(
                scope.holder,
                scope.profile,
                scope.context,
                body.roleId,
                _ctx(container, rc, scope.holder.profile_id),
            )
    return _json(detail.to_dict())


# ───────────────────────── one resume ─────────────────────────


@router.get("/api/resumes/{resume_id}")
async def get_resume(resume_id: str, request: Request) -> JSONResponse:
    container, _, user = await _start(request)
    stored, _ = await _open_resume(container, user, resume_id)
    return _json(container.resume.detail(stored).to_dict())


@router.patch("/api/resumes/{resume_id}")
async def edit_resume(resume_id: str, request: Request) -> JSONResponse:
    container, _, user = await _start(request)
    if not is_valid_id(resume_id):
        raise ResumeNotFound()
    body = await read_model(request, container, ResumeOpsRequest, max_bytes=OPS_BODY_MAX_BYTES)
    stored, _ = await _open_resume(container, user, resume_id)
    return _json((await container.resume.edit(stored, body.ops)).to_dict())


async def _role_terms(container: Container, stored: StoredResume) -> list[str]:
    """The words of the role a path resume is for (its graph's requirement names); none for the uploaded resume."""
    if stored.kind != "path":
        return []
    doc = await container.repos.profiles.get_profile(stored.profile_id)
    if doc is None:
        return []
    try:
        profile = Profile.from_dict(doc)
    except ValidationError:
        return []
    role = next((r for r in profile.roles if r.id == stored.role_key), None)
    return [] if role is None else role_terms(role)


@router.post("/api/resumes/{resume_id}/finalise")
async def finalise_resume(resume_id: str, request: Request) -> JSONResponse:
    container, _, user = await _start(request)
    if not is_valid_id(resume_id):
        raise ResumeNotFound()
    body = await read_model(request, container, FinaliseRequest, max_bytes=SMALL_BODY_MAX_BYTES)
    stored, holder = await _open_resume(container, user, resume_id)
    answer = await container.resume.finalise(stored, holder, body, terms=await _role_terms(container, stored))
    return _json(answer.to_dict())


@router.post("/api/resumes/{resume_id}/default")
async def make_default(resume_id: str, request: Request) -> JSONResponse:
    container, _, user = await _start(request)
    stored, _ = await _open_resume(container, user, resume_id)
    return _json((await container.resume.make_default(stored)).to_dict())


@router.post("/api/resumes/{resume_id}/grammar")
async def check_grammar(resume_id: str, request: Request) -> JSONResponse:
    container, rc, user = await _start(request)
    stored, holder = await _open_resume(container, user, resume_id)
    limiter = container.rate_limiter
    free = await unlimited(request, container)
    await limiter.check(
        "chat",
        rc.ip_hash,
        ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip),
        memory=False,
        exempt=free,
    )
    terms = await _role_terms(container, stored)
    async with container.spend_guard.reserve(container.settings.guard.est_match_usd):
        answer = await container.resume.grammar(
            stored, terms=terms, call_ctx=_ctx(container, rc, holder.profile_id)
        )
    return _json(answer.to_dict())


@router.get("/api/resumes/{resume_id}/wordings")
async def resume_wordings(resume_id: str, request: Request) -> JSONResponse:
    container, _, user = await _start(request)
    stored, _ = await _open_resume(container, user, resume_id)
    return _json((await container.resume.wordings(stored)).to_dict())


@router.post("/api/resumes/from-match")
async def save_match_resume(request: Request) -> JSONResponse:
    container, _, user = await _start(request)
    body = await read_model(request, container, FromMatchRequest, max_bytes=SMALL_BODY_MAX_BYTES)
    if not is_valid_id(body.matchId):
        raise NotFound(MATCH_NOT_FOUND)
    ws = await open_match(container, None if user is None else user.id, body.matchId)
    variant = await container.repos.matches.get_variant(ws.id, "resume")
    if variant is None:
        raise NotFound(NO_MATCH_RESUME)
    try:
        spec = ResumeVariant.model_validate(variant.spec)
    except ValidationError:
        raise NotFound(NO_MATCH_RESUME) from None
    holder = Holder(
        profile_id=ws.stored.profile_id,
        user_id=None if user is None else user.id,
        shares_facts=ws.shares_facts,
    )
    title = f"{ws.posting.title} at {ws.posting.company}" if ws.posting.company else ws.posting.title
    detail = await container.resume.save_match(
        holder, match_id=ws.id, title=title, variant=spec, book=ws.book
    )
    return _json(detail.to_dict())


async def resume_in_view(
    request: Request, container: Container, resume_id: str, profile_id: str
) -> ResumeInView | None:
    """The resume ``resume_id`` as the coach is shown it, for `POST /api/chat`, or None. None (never an error: the chat turn
    simply goes on without it) when the caller may not open that resume by the rule of the routes above (a sign-in, or
    anonymous matching while it is on; the profile an account's own, or an unclaimed or sample one), when it is not a resume of
    the profile being chatted about, or when it cannot be read."""
    try:
        user = await user_or_anonymous(request, anonymous_allowed=container.match_anonymous)
        stored, _ = await _open_resume(container, user, resume_id)
        if stored.profile_id != profile_id:
            return None
        return container.resume.coach_view(stored, await _role_terms(container, stored))
    except AppError:
        return None
    except Exception:
        log.warning("resume_in_view_failed", exc_info=True)
        return None


async def _download(request: Request, resume_id: str, extension: str) -> Response:
    container, _, user = await _start(request)
    stored, _ = await _open_resume(container, user, resume_id)
    files, person = await container.resume.files(
        stored
    )  # NotFinalised (409) while there is no finalised version
    pdf = extension == "pdf"
    return _file(
        files.pdf if pdf else files.docx, PDF if pdf else DOCX, download_name(person, "resume", extension)
    )


_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


@router.get("/api/resumes/{resume_id}/original")
async def resume_original(resume_id: str, request: Request) -> Response:
    container, _, user = await _start(request)
    stored, _ = await _open_resume(container, user, resume_id)
    original = await container.resume.original(stored)
    plain = _UNSAFE_NAME.sub("_", original.filename).strip(" .")[:100] or "resume"
    return Response(
        original.data,
        media_type=original.mime,
        headers={
            "Content-Disposition": f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(original.filename[:200])}",
            "X-Content-Type-Options": "nosniff",
            "X-Robots-Tag": "noindex",
            **NO_STORE,
        },
    )


@router.get("/api/resumes/{resume_id}/resume.pdf")
async def resume_pdf(resume_id: str, request: Request) -> Response:
    return await _download(request, resume_id, "pdf")


@router.get("/api/resumes/{resume_id}/resume.docx")
async def resume_docx(resume_id: str, request: Request) -> Response:
    return await _download(request, resume_id, "docx")


__all__ = ["OPS_BODY_MAX_BYTES", "SMALL_BODY_MAX_BYTES", "resume_in_view", "router"]
