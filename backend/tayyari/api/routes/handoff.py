"""The hand-off from the marketing site (https://leafout.in): a resume, links and a goal, posted once and taken once.

    POST /api/handoff        a top-level HTML form POST (multipart/form-data) from the site, not a fetch. Fields: ``resume`` (file),
                             ``links``, ``goal``, ``notes``. The answer is always ``303 See Other``, never JSON, because the
                             browser is navigating: ``/?handoff=<id>`` when it was stored, ``/?handoff_error=<code>`` when it was
                             refused (``origin``, ``too_large``, ``type``, ``empty``, ``rate``; `HandoffErrorCode`).
    GET  /api/handoff/{id}   the app's same-origin fetch: the `HandoffPayload`, once. The row is deleted as it is read; 404 when
                             there is none, it was taken already or it expired.

The order of the checks on the POST (everything before the form is read is cheap):

1. the ``Origin`` (else ``Referer``) must be one of ``TAYYARI_HANDOFF__ORIGINS``; the cross-site guard (`api.security`) lets this
   one route through for that reason, since the site is on another origin by design;
2. ``rate_limiter.shed``: the in-memory bucket turns hammering away before the body is read;
3. `read_upload_form`: the analyze upload's rules (streamed, hard caps, the accepted file types, one upload read per client);
4. ``rate_limiter.check(memory=False)``: the shared per-address and whole-service counters, in the ``handoff`` window;
5. `HandoffService.submit`: the checks on what was posted, the store, and the delete of expired rows.

Nothing is paid for a hand-off (no model is called), so there is no spend reservation. The id is a secret in the redirect's
query string and in the GET's path: it is never logged (the outcome is), the telemetry leaves these paths out
(`api.app._skip_telemetry`) and every answer is ``Cache-Control: no-store``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.datastructures import Headers
from starlette.responses import Response

from tayyari.api.deps import get_request_context
from tayyari.api.lifespan import get_container
from tayyari.api.security import cross_site_reason, subnet_hash
from tayyari.api.uploads import (
    MSG_BAD_FILE_TYPE,
    MSG_WRONG_FILE_FIELD,
    UploadLimits,
    read_upload_form,
    upload_gate,
)
from tayyari.contract.handoff import HandoffErrorCode
from tayyari.core.errors import (
    AppError,
    BadRequest,
    NotFound,
    PayloadTooLarge,
    RateLimited,
    ServiceUnavailable,
)
from tayyari.core.limits import MIB
from tayyari.core.logging import get_logger
from tayyari.handoff import HandoffRejected, ResumeFile

log = get_logger(__name__)

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}
_FILE_TYPE_MESSAGES = frozenset({MSG_BAD_FILE_TYPE, MSG_WRONG_FILE_FIELD})


def _last(fields: list[tuple[str, str]], name: str) -> str | None:
    for key, value in reversed(fields):
        if key == name:
            return value
    return None


def _redirect(query: str) -> Response:
    # A relative Location: the browser resolves it against the app's own address.
    return RedirectResponse(f"/?{query}" if query else "/", status_code=303, headers=NO_STORE)


def _refusal(error: Exception) -> HandoffErrorCode | None:
    """The code a failure of the form read or of the limiter becomes, or None for anything unexpected."""
    if isinstance(error, HandoffRejected):
        return error.code
    if isinstance(error, PayloadTooLarge):
        return "too_large"
    if isinstance(error, RateLimited | ServiceUnavailable):
        return "rate"
    if isinstance(error, BadRequest):
        return "type" if error.user_message in _FILE_TYPE_MESSAGES else "empty"
    if isinstance(error, AppError):  # a stalled or truncated upload
        return "empty"
    return None


@router.post("/api/handoff")
async def post_handoff(request: Request) -> Response:
    container = get_container(request)
    service = container.handoff
    limiter = container.rate_limiter
    guard = container.settings.guard
    rc = await get_request_context(request)
    ip48 = subnet_hash(container.ip_hash_key, rc.client_ip)
    max_bytes = container.settings.handoff.max_bytes
    try:
        service.check_origin(request.headers.get("origin"), request.headers.get("referer"))
        limiter.shed("handoff", rc.ip_hash, ip_hash_48=ip48)
        fields, upload = await read_upload_form(
            request,
            limits=UploadLimits(max_body_bytes=max_bytes + MIB, max_resume_bytes=max_bytes),
            read_timeout_s=guard.upload_read_timeout_s,
            gate=upload_gate(request, guard),
            client_key=rc.ip_hash,
        )
        await limiter.check("handoff", rc.ip_hash, ip_hash_48=ip48, memory=False)
        handoff_id = await service.submit(
            links=[value for name, value in fields if name == "links"],
            goal=_last(fields, "goal"),
            notes=_last(fields, "notes"),
            resume=ResumeFile(upload.filename, upload.content_type, upload.data)
            if upload is not None
            else None,
        )
    except Exception as error:
        code = _refusal(error)
        if code is None:
            log.error("handoff_failed", exc_info=True)
            return _redirect(
                ""
            )  # the app opens as usual; there is nothing to say about a server fault in a code
        log.info("handoff", outcome=code)
        return _redirect(f"handoff_error={code}")
    log.info("handoff", outcome="stored")
    return _redirect(f"handoff={handoff_id}")


@router.get("/api/handoff/{handoff_id}")
async def take_handoff(handoff_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    # Reading one costs a delete, so it takes a token from the light chat-side bucket like a profile read does.
    container.rate_limiter.shed(
        "chat", rc.ip_hash, ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip)
    )
    if cross_site_reason(Headers(scope=request.scope)) is not None:
        raise NotFound("Not found")  # another site's page asking: leave the hand-off where it is
    payload = await container.handoff.take(handoff_id)
    return JSONResponse(payload.to_dict(), headers=NO_STORE)


__all__ = ["router"]
