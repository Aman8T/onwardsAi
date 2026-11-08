"""Auto-apply routes (``/api/apply/*``): see shared/apply.ts for the contract. Behind ``AUTO_APPLY_ENABLED``: every route but
``status`` answers 404 while it is off.

Signed-in only (CLAUDE.md rule 9): every route but ``status`` answers 401 without a sign-in, even while anonymous matching
is on; the owner of everything here is ``u:<user id>``. JSON bodies must be ``application/json`` (a cross-site form cannot
send that); the resume is a raw PDF or DOCX body, checked by its content. Nothing here submits: the worker does, and only
for an application whose approval matches its review.

    POST /api/apply/check              the start checks, read-only: no row, no hold, no charge
    POST /api/apply/applications       `StartApplicationRequest`: the match is opened (the person's own only), its gates
                                       decide, the form's address comes from the Discover listing (proved to be the
                                       matched posting) or the pasted application link (the same posting); a tailored
                                       resume must be ready. 409 with the reason (and ``existingApplicationId`` or
                                       ``gateReasons``) when a check fails, 402 when no credit is left
    .../{id}/answers, approve, cancel, resolve, applied-there, stage, retry, screenshot?kind=review|confirmation
    /api/apply/details[...], /api/apply/profile/resume, /api/apply/consent, /api/apply/email, /api/apply/metrics

The check and the email routes are light on their own but fetch an employer's page or call a model, so each is shed per
account by the in-memory limiter (the ``chat`` bucket, keyed ``u:<user id>``), and the email route is also counted in the
shared ``chat`` windows per account: an unlimited account is exempt from both.
"""

from __future__ import annotations

from typing import Any, Final
from urllib.parse import unquote

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from tayyari.api.deps import User, get_current_user, require_user, unlimited
from tayyari.api.lifespan import get_container
from tayyari.api.matching import (
    MATCH_NOT_FOUND,
    ApplyFactSink,
    gate_reasons,
    listing_url,
    open_match,
)
from tayyari.api.routes._body import describe, read_model
from tayyari.api.uploads import read_capped_body, read_json_body, require_json_content_type
from tayyari.apply.intake import IngestFetcher, IngestStatusFetcher
from tayyari.apply.seams import ApplyCredits
from tayyari.apply.service import ALREADY_APPLIED, ApplyService, MatchInfo, StartRefused
from tayyari.container import Container
from tayyari.contract.apply import (
    APPLY_RESUME_MAX_BYTES,
    AnswerRequest,
    ApplyCheck,
    ApplyCheckRequest,
    ApproveRequest,
    EmailClassifyRequest,
    FirstCheckRequest,
    ResolveRequest,
    ResumeChoice,
    SaveAnswerRequest,
    SelfIdRequest,
    StageChangeRequest,
    StartApplicationRequest,
    StillTrueRequest,
)
from tayyari.core.errors import BadRequest, Conflict, NotFound, PayloadTooLarge
from tayyari.storage.ports import PostingTaken, account_owner_key

router = APIRouter()

BODY_MAX_BYTES: Final = 64 * 1024
EMAIL_BODY_MAX_BYTES: Final = 128 * 1024
ID_MAX_CHARS: Final = 96
NO_STORE: Final = {"Cache-Control": "no-store"}
TAILORED_NOT_READY: Final = (
    "Your resume for this role is not finished yet. Finish it first, or apply with your saved resume."
)


def _enabled(container: Container) -> None:
    if not container.settings.auto_apply_enabled:
        raise NotFound("Not found")


def _service(container: Container) -> ApplyService:
    credits: ApplyCredits = container.entitlements  # mypy checks the seam here
    return ApplyService(
        container.repos.apply,
        container.settings,
        container.clock,
        IngestFetcher(),
        IngestStatusFetcher(),
        credits,
        ApplyFactSink(container),
    )


async def _start(request: Request) -> tuple[Container, ApplyService, User, str]:
    """The feature must be on (else 404) and someone signed in (else 401)."""
    container = get_container(request)
    _enabled(container)
    user = await require_user(request)
    return container, _service(container), user, account_owner_key(user.id)


async def _shed(request: Request, container: Container, owner: str) -> bool:
    """Turn an account that hammers a fetching or model route away before its body is read (429), per account; True
    for an unlimited account (exempt)."""
    free = await unlimited(request, container)
    container.rate_limiter.shed("chat", owner, exempt=free)
    return free


def _json(data: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers=NO_STORE)


async def _body[M: BaseModel](request: Request, container: Container, model: type[M]) -> M:
    return await read_model(request, container, model, max_bytes=BODY_MAX_BYTES)


def _short_id(value: str | None, what: str) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    if not clean or len(clean) > ID_MAX_CHARS:
        raise BadRequest(f"That {what} is not one we know.")
    return clean


async def _match(
    container: Container,
    user: User,
    match_id: str,
    *,
    listing_id: str | None,
    run_id: str | None,
    apply_url: str | None,
    resume: ResumeChoice = "saved",
) -> MatchInfo:
    """The person's own match as auto-apply sees it, with the form's address resolved server-side."""
    ws = await open_match(container, user.id, match_id)
    if ws.stored.user_id != user.id:  # a match made without an account is not started from
        raise NotFound(MATCH_NOT_FOUND)
    blocked, reasons = gate_reasons(ws)
    url, pasted = None, False
    if listing_id is not None:
        url = await listing_url(container, ws, listing_id, run_id)
    elif apply_url and apply_url.strip():
        url, pasted = apply_url, True
    return MatchInfo(
        match_id=ws.id, profile_id=ws.stored.profile_id, user_id=ws.stored.user_id, blocked=blocked,
        gate_reasons=reasons, title=ws.posting.title, company=ws.posting.company, location=ws.posting.location,
        posting_url=url, listing_id=listing_id, resume_choice=resume, pasted=pasted, description=ws.posting.rawText,
    )  # fmt: skip


def _refused(check: ApplyCheck, status: int) -> JSONResponse:
    body: dict[str, Any] = {
        "error": check.note or "Apply for me is not offered for this posting.",
        "reason": check.reason,
    }
    if check.applicationId:
        body["existingApplicationId"] = check.applicationId
    if check.gateReasons:
        body["gateReasons"] = check.gateReasons
    if check.host:
        body["host"] = check.host
    return _json(body, status)


# ───────────────────────── status, consent ─────────────────────────


@router.get("/api/apply/status")
async def status(request: Request) -> JSONResponse:
    container = get_container(request)
    enabled = container.settings.auto_apply_enabled
    user = await get_current_user(request) if enabled else None
    owner = account_owner_key(user.id) if user is not None else None
    return _json((await _service(container).status(owner, enabled=enabled)).to_dict())


@router.post("/api/apply/consent")
async def consent(request: Request) -> JSONResponse:
    _, svc, _, owner = await _start(request)
    return _json((await svc.consent(owner)).to_dict())


# ───────────────────────── the start ─────────────────────────


@router.post("/api/apply/check")
async def check(request: Request) -> JSONResponse:
    """The start checks for one posting: read-only, charges nothing. Shed per account (it may read the employer's
    page and the ATS's public job endpoint)."""
    container, svc, user, owner = await _start(request)
    await _shed(request, container, owner)
    body = await _body(request, container, ApplyCheckRequest)
    listing_id = _short_id(body.listingId, "listing")
    run_id = _short_id(body.runId, "search")
    if body.matchId:
        match = await _match(
            container, user, body.matchId, listing_id=listing_id, run_id=run_id, apply_url=body.applyUrl
        )
        return _json((await svc.check(owner, match, match.posting_url)).to_dict())
    url = body.applyUrl
    if listing_id is not None:
        listing = await container.repos.discover.get(listing_id)
        url = listing.apply_url if listing is not None else None
    return _json((await svc.check(owner, None, url)).to_dict())


CONSENT_FIRST: Final = "Agree to how Apply for me works first: nothing is started before you do."


@router.post("/api/apply/applications")
async def create_application(request: Request) -> JSONResponse:
    container, svc, user, owner = await _start(request)
    body = await _body(request, container, StartApplicationRequest)
    match = await _match(
        container, user, body.matchId, listing_id=_short_id(body.listingId, "listing"),
        run_id=_short_id(body.runId, "search"), apply_url=body.applyUrl, resume=body.resume,
    )  # fmt: skip
    profile = await container.repos.apply.get_profile(owner)
    if (
        profile is None or profile.consent_at is None
    ):  # spec 5: the consent screen comes before the first application
        raise Conflict(CONSENT_FIRST)
    if body.resume == "tailored":
        variant = await container.repos.matches.get_variant(match.match_id, "resume")
        if variant is None or variant.status != "ready":
            raise Conflict(TAILORED_NOT_READY)
    try:
        detail = await svc.create(owner, body, match, unlimited=await unlimited(request, container))
    except StartRefused as refused:
        return _refused(refused.check, refused.status)
    except PostingTaken as taken:
        body_out = {
            "error": ALREADY_APPLIED,
            "reason": "already_applied",
            "existingApplicationId": taken.application_id,
        }
        return _json(body_out, 409)
    return _json(detail.to_dict(), 201)


# ───────────────────────── applications ─────────────────────────


@router.get("/api/apply/applications")
async def list_applications(request: Request) -> JSONResponse:
    _, svc, _, owner = await _start(request)
    return _json((await svc.list(owner)).to_dict())


@router.get("/api/apply/applications/{application_id}")
async def get_application(application_id: str, request: Request) -> JSONResponse:
    _, svc, _, owner = await _start(request)
    return _json((await svc.detail(owner, application_id)).to_dict())


@router.get("/api/apply/applications/{application_id}/screenshot")
async def get_screenshot(application_id: str, request: Request) -> Response:
    _, svc, _, owner = await _start(request)
    kind = request.query_params.get("kind", "review")
    if kind not in ("review", "confirmation"):
        raise BadRequest("kind is review or confirmation")
    data = await svc.screenshot(owner, application_id, "confirmation" if kind == "confirmation" else "review")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.post("/api/apply/applications/{application_id}/answers")
async def post_answers(application_id: str, request: Request) -> JSONResponse:
    container, svc, user, owner = await _start(request)
    body = await _body(request, container, AnswerRequest)
    result = await svc.answer(owner, application_id, body, account_email=user.email)
    return _json(result.to_dict())


@router.post("/api/apply/applications/{application_id}/approve")
async def approve(application_id: str, request: Request) -> JSONResponse:
    container, svc, _, owner = await _start(request)
    body = await _body(request, container, ApproveRequest)
    return _json((await svc.approve(owner, application_id, body.digest)).to_dict())


@router.post("/api/apply/applications/{application_id}/cancel")
async def cancel(application_id: str, request: Request) -> JSONResponse:
    _, svc, _, owner = await _start(request)
    return _json((await svc.cancel(owner, application_id)).to_dict())


@router.post("/api/apply/applications/{application_id}/resolve")
async def resolve(application_id: str, request: Request) -> JSONResponse:
    container, svc, _, owner = await _start(request)
    body = await _body(request, container, ResolveRequest)
    return _json((await svc.resolve(owner, application_id, body.outcome)).to_dict())


@router.post("/api/apply/applications/{application_id}/applied-there")
async def applied_there(application_id: str, request: Request) -> JSONResponse:
    _, svc, _, owner = await _start(request)
    return _json((await svc.applied_there(owner, application_id)).to_dict())


@router.post("/api/apply/applications/{application_id}/stage")
async def change_stage(application_id: str, request: Request) -> JSONResponse:
    container, svc, _, owner = await _start(request)
    body = await _body(request, container, StageChangeRequest)
    moved = await svc.set_stage(
        owner, application_id, body.stage, body.note, unlimited=await unlimited(request, container)
    )
    return _json(moved.to_dict())


@router.post("/api/apply/applications/{application_id}/retry")
async def retry(application_id: str, request: Request) -> JSONResponse:
    container, svc, _, owner = await _start(request)
    moved = await svc.retry(owner, application_id, unlimited=await unlimited(request, container))
    return _json(moved.to_dict())


@router.delete("/api/apply/applications/{application_id}")
async def delete_application(application_id: str, request: Request) -> Response:
    _, svc, _, owner = await _start(request)
    await svc.delete(owner, application_id)
    return Response(status_code=204)


# ───────────────────────── Your details ─────────────────────────


@router.get("/api/apply/details")
async def get_details(request: Request) -> JSONResponse:
    _, svc, _, owner = await _start(request)
    return _json((await svc.details(owner)).to_dict())


@router.put("/api/apply/details/answer")
async def put_answer(request: Request) -> JSONResponse:
    container, svc, user, owner = await _start(request)
    body = await _body(request, container, SaveAnswerRequest)
    return _json((await svc.save_answer(owner, body, account_email=user.email)).to_dict())


@router.put("/api/apply/details/answers/{question_key}")
async def put_answer_by_key(question_key: str, request: Request) -> JSONResponse:
    """The same as ``PUT /details/answer`` with the question key in the path (``{askEachTime, value?}`` as the body)."""
    container, svc, user, owner = await _start(request)
    require_json_content_type(request)
    raw = await read_json_body(
        request, BODY_MAX_BYTES, read_timeout_s=container.settings.guard.upload_read_timeout_s
    )
    if not isinstance(raw, dict):
        raise BadRequest("The request is not in the expected shape.")
    try:
        body = SaveAnswerRequest.model_validate({**raw, "questionKey": question_key})
    except ValidationError as err:
        raise BadRequest(describe(err)) from None
    return _json((await svc.save_answer(owner, body, account_email=user.email)).to_dict())


@router.delete("/api/apply/details/answers/{question_key}")
async def delete_answer(question_key: str, request: Request) -> JSONResponse:
    _, svc, _, owner = await _start(request)
    return _json((await svc.delete_answer(owner, question_key)).to_dict())


@router.post("/api/apply/details/still-true")
async def still_true(request: Request) -> JSONResponse:
    container, svc, _, owner = await _start(request)
    body = await _body(request, container, StillTrueRequest)
    if not body.questionKey.strip():
        raise BadRequest("Say which answer is still true.")
    return _json((await svc.still_true(owner, body.questionKey.strip())).to_dict())


@router.post("/api/apply/details/first-check")
async def first_check(request: Request) -> JSONResponse:
    container, svc, user, owner = await _start(request)
    body = await _body(request, container, FirstCheckRequest)
    return _json((await svc.first_check(owner, body, account_email=user.email)).to_dict())


@router.put("/api/apply/details/self-id")
async def self_id(request: Request) -> JSONResponse:
    container, svc, _, owner = await _start(request)
    body = await _body(request, container, SelfIdRequest)
    return _json((await svc.set_self_id(owner, body.selfIdOnForms)).to_dict())


# ───────────────────────── the resume ─────────────────────────


@router.put("/api/apply/profile/resume")
async def put_resume(request: Request) -> JSONResponse:
    """The default resume as the raw body: a PDF or DOCX by its content (the Content-Type is not trusted), 5 MB at most.
    Answers with `YourDetails`."""
    container, svc, _, owner = await _start(request)
    try:  # counted on the stream: a chunked upload with no Content-Length is cut off at the cap too
        data = await read_capped_body(
            request, APPLY_RESUME_MAX_BYTES, read_timeout_s=container.settings.guard.upload_read_timeout_s
        )
    except PayloadTooLarge:
        raise PayloadTooLarge("That file is over 5 MB.") from None
    name = unquote(request.headers.get("x-file-name") or "resume")  # sent URI-encoded
    return _json((await svc.put_resume(owner, name, data)).to_dict())


@router.delete("/api/apply/profile/resume")
async def delete_resume(request: Request) -> JSONResponse:
    _, svc, _, owner = await _start(request)
    return _json((await svc.put_resume(owner, None, None)).to_dict())


# ───────────────────────── email and metrics ─────────────────────────


@router.post("/api/apply/email")
async def classify_email(request: Request) -> JSONResponse:
    """An employer's email, pasted: classified, matched to a sent application, and dropped (never stored or logged)."""
    from tayyari.apply.email import EmailTracker

    container, _, _, owner = await _start(request)
    free = await _shed(request, container, owner)
    body = await read_model(request, container, EmailClassifyRequest, max_bytes=EMAIL_BODY_MAX_BYTES)
    await container.rate_limiter.check("chat", owner, memory=False, globally=False, exempt=free)
    settings = container.settings
    claude = container.claude if settings.anthropic_api_key is not None else None
    tracker = EmailTracker(
        container.repos.apply,
        container.clock,
        claude,
        settings.apply.email_model or settings.reader_model,
        reserve=container.spend_guard.reserve,
        est_usd=settings.guard.est_apply_email_usd,
    )
    return _json((await tracker.handle(owner, body)).to_dict())


@router.get("/api/apply/metrics")
async def metrics(request: Request) -> JSONResponse:
    """Playbook vs loop runs, outcomes and failure reasons per ATS. Signed in; in production, only to
    `ApplySettings.admin_emails` (anyone else gets a 404)."""
    from tayyari.apply.metrics import compute_metrics

    container = get_container(request)
    _enabled(container)
    user = await require_user(request)
    if container.settings.is_prod and not container.settings.apply.is_admin(user.email):
        raise NotFound("Not found")
    raw_days = request.query_params.get("days", "30")
    days = min(max(int(raw_days) if raw_days.isdigit() else 30, 1), 365)
    data = await compute_metrics(container.repos.apply, container.clock.now(), days)
    return _json(data.to_dict())
