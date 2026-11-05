"""Auto-apply routes (``/api/apply/*``): see shared/apply.ts for the contract. Behind ``AUTO_APPLY_ENABLED``: every route but
``status`` answers 404 while it is off.

Who: a signed-in account (owner ``u:<id>``), or, while anonymous use is allowed (no sign-in provider configured, as for the
posting match), an anonymous browser identified by an HttpOnly ``tayyari_apply`` cookie (owner ``a:<sha256>``). JSON bodies
must be ``application/json`` (a cross-site form cannot send that); the resume is a raw PDF or DOCX body. Nothing here submits:
the worker does, and only for an application whose approval matches its review.
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from tayyari.api.deps import get_current_user
from tayyari.api.lifespan import get_container
from tayyari.api.uploads import read_json_body, require_json_content_type
from tayyari.apply.intake import IngestFetcher
from tayyari.apply.service import ApplyService, MatchInfo
from tayyari.container import Container
from tayyari.contract.apply import (
    APPLY_RESUME_MAX_BYTES,
    AnswerNeedsRequest,
    ApplicantProfileUpdate,
    ApplyStatus,
    ApproveRequest,
    CreateApplicationRequest,
    EmailClassifyRequest,
    StageChangeRequest,
)
from tayyari.core.errors import BadRequest, NotFound, PayloadTooLarge, Unauthorized

router = APIRouter()

COOKIE = "tayyari_apply"
BODY_MAX_BYTES = 64 * 1024
RESUME_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def _enabled(container: Container) -> None:
    if not container.settings.auto_apply_enabled:
        raise NotFound("Not found")


async def _owner(request: Request, container: Container) -> tuple[str, str | None, str | None]:
    """``(owner key, user id, new cookie token)``; a token is minted for an anonymous browser that has none."""
    user = await get_current_user(request)
    if user is not None:
        return f"u:{user.id}", user.id, None
    if not container.match_anonymous:
        raise Unauthorized()
    token = request.cookies.get(COOKIE) or ""
    fresh = None
    if len(token) < 32 or len(token) > 64:
        token = fresh = secrets.token_urlsafe(32)
    return "a:" + hashlib.sha256(token.encode()).hexdigest()[:40], None, fresh


def _json(request: Request, data: dict[str, object], fresh: str | None, status: int = 200) -> JSONResponse:
    resp = JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})
    if fresh:
        settings = get_container(request).settings
        resp.set_cookie(
            COOKIE,
            fresh,
            max_age=365 * 86400,
            httponly=True,
            samesite="lax",
            secure=settings.is_prod,
            path="/",
        )
    return resp


async def _body[M: BaseModel](request: Request, model: type[M]) -> M:
    require_json_content_type(request)
    raw = await read_json_body(request, BODY_MAX_BYTES)
    try:
        return model.model_validate(raw)
    except ValidationError as err:
        raise BadRequest("The request is not in the expected shape.") from err


def _service(container: Container) -> ApplyService:
    return ApplyService(container.repos.apply, container.settings, container.clock, IngestFetcher())


async def _start(request: Request) -> tuple[Container, ApplyService, str, str | None, str | None]:
    container = get_container(request)
    _enabled(container)
    owner, user_id, fresh = await _owner(request, container)
    return container, _service(container), owner, user_id, fresh


@router.get("/api/apply/status")
async def status(request: Request) -> JSONResponse:
    container = get_container(request)
    settings = container.settings
    user = await get_current_user(request) if settings.auto_apply_enabled else None
    keys = {"gemini": settings.gemini_api_key is not None, "claude": settings.anthropic_api_key is not None}
    agents = [a for a in settings.apply.agent_order if keys.get(a)]
    body = ApplyStatus(enabled=settings.auto_apply_enabled, signedIn=user is not None,
                       anonymous=user is None and container.match_anonymous, agents=agents)  # fmt: skip
    return JSONResponse(body.to_dict(), headers={"Cache-Control": "no-store"})


@router.get("/api/apply/profile")
async def get_profile(request: Request) -> JSONResponse:
    _, svc, owner, _, fresh = await _start(request)
    return _json(request, (await svc.profile(owner)).to_dict(), fresh)


@router.put("/api/apply/profile")
async def put_profile(request: Request) -> JSONResponse:
    _, svc, owner, user_id, fresh = await _start(request)
    body = await _body(request, ApplicantProfileUpdate)
    return _json(request, (await svc.put_details(owner, user_id, body.details)).to_dict(), fresh)


@router.put("/api/apply/profile/resume")
async def put_resume(request: Request) -> JSONResponse:
    _, svc, owner, user_id, fresh = await _start(request)
    media_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if media_type not in RESUME_TYPES:
        raise BadRequest("Upload your resume as a PDF or DOCX file.")
    data = await request.body()
    if len(data) > APPLY_RESUME_MAX_BYTES:
        raise PayloadTooLarge("That file is over 5 MB.")
    if not data:
        raise BadRequest("The file is empty.")
    name = (request.headers.get("x-file-name") or "resume").strip()[:120] or "resume"
    if not name.lower().endswith(RESUME_TYPES[media_type]):
        name += RESUME_TYPES[media_type]
    return _json(request, (await svc.put_resume(owner, user_id, name, media_type, data)).to_dict(), fresh)


@router.delete("/api/apply/profile/resume")
async def delete_resume(request: Request) -> JSONResponse:
    _, svc, owner, user_id, fresh = await _start(request)
    return _json(request, (await svc.put_resume(owner, user_id, None, None, None)).to_dict(), fresh)


@router.post("/api/apply/applications")
async def create_application(request: Request) -> JSONResponse:
    container, svc, owner, user_id, fresh = await _start(request)
    body = await _body(request, CreateApplicationRequest)
    match = None
    if body.matchId:
        from tayyari.api.matching import open_match

        ws = await open_match(container, user_id, body.matchId)
        match = MatchInfo(
            ws.id, ws.stored.profile_id, ws.posting.title, ws.posting.company, ws.posting.location
        )
    detail = await svc.create(owner, user_id, body, match)
    return _json(request, detail.to_dict(), fresh, status=201)


@router.get("/api/apply/applications")
async def list_applications(request: Request) -> JSONResponse:
    _, svc, owner, _, fresh = await _start(request)
    return _json(request, (await svc.list(owner)).to_dict(), fresh)


@router.get("/api/apply/applications/{application_id}")
async def get_application(application_id: str, request: Request) -> JSONResponse:
    _, svc, owner, _, fresh = await _start(request)
    return _json(request, (await svc.detail(owner, application_id)).to_dict(), fresh)


@router.get("/api/apply/applications/{application_id}/screenshot")
async def get_screenshot(application_id: str, request: Request) -> Response:
    _, svc, owner, _, _ = await _start(request)
    data = await svc.screenshot(owner, application_id)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.post("/api/apply/applications/{application_id}/answers")
async def post_answers(application_id: str, request: Request) -> JSONResponse:
    _, svc, owner, user_id, fresh = await _start(request)
    body = await _body(request, AnswerNeedsRequest)
    return _json(request, (await svc.answer(owner, user_id, application_id, body)).to_dict(), fresh)


@router.post("/api/apply/applications/{application_id}/approve")
async def approve(application_id: str, request: Request) -> JSONResponse:
    _, svc, owner, _, fresh = await _start(request)
    body = await _body(request, ApproveRequest)
    return _json(request, (await svc.approve(owner, application_id, body.digest)).to_dict(), fresh)


@router.post("/api/apply/applications/{application_id}/stage")
async def change_stage(application_id: str, request: Request) -> JSONResponse:
    _, svc, owner, _, fresh = await _start(request)
    body = await _body(request, StageChangeRequest)
    return _json(
        request, (await svc.set_stage(owner, application_id, body.stage, body.note)).to_dict(), fresh
    )


@router.post("/api/apply/applications/{application_id}/retry")
async def retry(application_id: str, request: Request) -> JSONResponse:
    _, svc, owner, _, fresh = await _start(request)
    return _json(request, (await svc.retry(owner, application_id)).to_dict(), fresh)


@router.post("/api/apply/email")
async def classify_email(request: Request) -> JSONResponse:
    """An employer's email, pasted: classified, matched to an application, and dropped (never stored or logged)."""
    from tayyari.apply.email import EmailTracker

    container, _, owner, _, fresh = await _start(request)
    require_json_content_type(request)
    raw = await read_json_body(request, 128 * 1024)
    try:
        body = EmailClassifyRequest.model_validate(raw)
    except ValidationError as err:
        raise BadRequest("The request is not in the expected shape.") from err
    settings = container.settings
    claude = container.claude if settings.anthropic_api_key is not None else None
    tracker = EmailTracker(
        container.repos.apply, container.clock, claude, settings.apply.email_model or settings.reader_model
    )
    return _json(request, (await tracker.handle(owner, body)).to_dict(), fresh)


@router.delete("/api/apply/applications/{application_id}")
async def delete_application(application_id: str, request: Request) -> Response:
    _, svc, owner, _, _ = await _start(request)
    await svc.delete(owner, application_id)
    return Response(status_code=204)
