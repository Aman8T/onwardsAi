"""The application inbox (``/api/inbox/*``) and the Resend Receiving webhook (``POST /api/inbound/resend``): see
shared/inbox.ts for the contract and docs/auto-apply.md §3.4 for the spec.

    GET   /api/inbox/status     `InboxStatus`; answers with the feature off (``enabled: false``) and without a sign-in (no
                                address then)
    POST  /api/inbox/address    `CreateInboxAddressRequest` ``{consent: true}`` -> `InboxAddress` (the existing one when the
                                person has one: idempotent, 200)
    PATCH /api/inbox/address    `UpdateInboxAddressRequest` ``{active?, copies?}`` -> `InboxAddress`
    GET   /api/inbox/emails?limit=50   `InboxEmailList`, newest first (1 to 200)
    POST  /api/inbound/resend   the webhook: the raw body (256 KB at most), Svix headers

On only when ``INBOUND_EMAIL_ENABLED`` is true and both ``RESEND_API_KEY`` and ``RESEND_WEBHOOK_SECRET`` are set; otherwise
every route but the status answers 404, the webhook too. The ``/api/inbox`` routes need a sign-in (401), through the same
dependency as ``/api/apply``. A request body that fails its model (consent not true, an update that changes nothing) is a 400.

The webhook is called by Resend's servers: no cookie, no session, no cross-site check (`api.security.CrossSiteGuard` exempts
it, like the billing webhooks). The signature is checked over the raw bytes before anything is parsed or looked up (401 when
it fails). Only ``email.received`` is acted on; anything else with a good signature is 200. The row is written first (a
repeated delivery is a 200 that does nothing), and a database error is a 503 so Resend tries again. Fetching, classifying
and the copy run after the response, as a background task; the reconcile pass picks up anything that did not finish.
"""

from __future__ import annotations

import json
from contextlib import aclosing
from typing import Any, Final

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from tayyari.api.deps import User, get_current_user, require_user
from tayyari.api.lifespan import get_container
from tayyari.api.routes._body import read_model
from tayyari.api.uploads import body_deadline, check_content_length
from tayyari.container import Container
from tayyari.contract.inbox import (
    INBOX_EMAILS_DEFAULT_LIMIT,
    INBOX_EMAILS_MAX_LIMIT,
    CreateInboxAddressRequest,
    InboxStatus,
    UpdateInboxAddressRequest,
)
from tayyari.core.errors import BadRequest, NotFound, PayloadTooLarge, ServiceUnavailable, Unauthorized
from tayyari.core.logging import get_logger
from tayyari.inbox.service import InboxService
from tayyari.inbox.svix import SignatureError, verify

router = APIRouter()
log = get_logger(__name__)

NO_STORE: Final = {"Cache-Control": "no-store"}
BODY_MAX_BYTES: Final = 4 * 1024
WEBHOOK_MAX_BYTES: Final = 256 * 1024
"""Resend's webhook carries metadata only (a few kilobytes); anything bigger is not from Resend."""
BODY_TOO_LARGE: Final = "Request body too large"
BAD_SIGNATURE: Final = "The webhook signature is not valid."
TRY_AGAIN: Final = "The email could not be recorded right now. Please try again."


def _json(data: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers=NO_STORE)


def _inbox(container: Container) -> InboxService:
    """The inbox service while it runs; 404 otherwise (as auto-apply's routes are while it is off)."""
    inbox = container.inbox
    if inbox is None or not inbox.enabled:
        raise NotFound("Not found")
    return inbox


async def _start(request: Request) -> tuple[Container, InboxService, User]:
    """The feature must be on (else 404) and someone signed in (else 401)."""
    container = get_container(request)
    inbox = _inbox(container)
    user = await require_user(request)
    return container, inbox, user


def _limit(raw: str | None) -> int:
    value = raw.strip() if raw else ""
    number = int(value) if value.isdigit() else INBOX_EMAILS_DEFAULT_LIMIT
    return min(max(number, 1), INBOX_EMAILS_MAX_LIMIT)


@router.get("/api/inbox/status")
async def status(request: Request) -> JSONResponse:
    container = get_container(request)
    inbox = container.inbox
    if inbox is None or not inbox.enabled:
        return _json(InboxStatus(enabled=False).to_dict())
    user = await get_current_user(request)
    return _json((await inbox.status(user.id if user is not None else None)).to_dict())


@router.post("/api/inbox/address")
async def create_address(request: Request) -> JSONResponse:
    container, inbox, user = await _start(request)
    await read_model(request, container, CreateInboxAddressRequest, max_bytes=BODY_MAX_BYTES)
    return _json((await inbox.create_address(user.id, user.name)).to_dict())


@router.patch("/api/inbox/address")
async def update_address(request: Request) -> JSONResponse:
    container, inbox, user = await _start(request)
    body = await read_model(request, container, UpdateInboxAddressRequest, max_bytes=BODY_MAX_BYTES)
    return _json((await inbox.update_address(user.id, active=body.active, copies=body.copies)).to_dict())


@router.get("/api/inbox/emails")
async def list_emails(request: Request) -> JSONResponse:
    _, inbox, user = await _start(request)
    found = await inbox.list_emails(user.id, _limit(request.query_params.get("limit")))
    return _json(found.to_dict())


# ───────────────────────── the webhook ─────────────────────────


async def _read_raw(request: Request, container: Container) -> bytes:
    """The whole body as bytes (the signature is computed over exactly these), capped as it arrives."""
    check_content_length(request, WEBHOOK_MAX_BYTES)
    chunks: list[bytes] = []
    size = 0
    async with (
        body_deadline(container.settings.guard.upload_read_timeout_s),
        aclosing(request.stream()) as stream,
    ):
        async for chunk in stream:
            size += len(chunk)
            if size > WEBHOOK_MAX_BYTES:
                raise PayloadTooLarge(BODY_TOO_LARGE)
            chunks.append(chunk)
    return b"".join(chunks)


@router.post("/api/inbound/resend")
async def resend_webhook(request: Request, background: BackgroundTasks) -> JSONResponse:
    container = get_container(request)
    inbox = _inbox(container)
    secret = container.settings.resend_webhook_secret
    if secret is None:  # pragma: no cover - the inbox is not enabled without it
        raise NotFound("Not found")
    raw = await _read_raw(request, container)
    try:
        verify(secret.get_secret_value(), request.headers, raw, now=container.clock.now().timestamp())
    except SignatureError as exc:
        log.warning("inbox_webhook_refused", reason=str(exc))
        raise Unauthorized(BAD_SIGNATURE) from None
    try:
        event = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        raise BadRequest("The webhook body is not JSON.") from None
    if not isinstance(event, dict):
        raise BadRequest("The webhook body is not a JSON object.")
    try:
        ids = await inbox.receive(event)
    except Exception as exc:  # the database may be waking up: Resend retries a 5xx
        log.error("inbox_webhook_store_failed", error=type(exc).__name__)
        raise ServiceUnavailable(TRY_AGAIN) from None
    if ids:
        background.add_task(inbox.process_many, ids)
    return _json({"ok": True})


__all__ = ["BODY_TOO_LARGE", "WEBHOOK_MAX_BYTES", "router"]
