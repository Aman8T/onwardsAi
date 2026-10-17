"""Pricing, checkout and the two payment webhooks.

    GET  /api/billing/entitlements[?timezone=Asia/Kolkata]
                                    `EntitlementsResponse` for anyone: the balance (zeros when anonymous), the
                                    currency to show first and the prices that are on sale
    POST /api/billing/checkout      {"product", "currency"?, "timezone"?} -> `CheckoutResponse`; needs a sign-in.
                                    409 ``not_offered`` when the plan has no price or its provider has no secrets
    POST /api/billing/verify        {"razorpay_order_id", "razorpay_payment_id", "razorpay_signature"} ->
                                    `VerifyResponse`; needs a sign-in; the signature must be Razorpay's
    POST /api/billing/webhooks/razorpay    the raw body, ``X-Razorpay-Signature``
    POST /api/billing/webhooks/stripe      the raw body, ``Stripe-Signature``

The webhooks are called by the providers' servers, not by a browser: no cookie, no JSON content type, and they are
exempt from the cross-site guard (`api.security.CrossSiteGuard`). The signature is the only credential, and it is
checked over the raw bytes before the body is parsed or anything is looked up. A good signature is always answered
200 ``{"ok": true}`` (even for an event we do not act on or an order we do not know), so the provider stops retrying;
a bad one is 400, and a provider that is not configured here is 503. Settling is idempotent on the provider's order
id, so a retried or replayed webhook, or the browser hand-off arriving as well, grants once.

Responses are ``Cache-Control: no-store``.
"""

from __future__ import annotations

from contextlib import aclosing

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tayyari.api.deps import get_current_user, get_request_context, require_user, unlimited
from tayyari.api.lifespan import get_container
from tayyari.api.routes._body import read_model
from tayyari.api.security import subnet_hash
from tayyari.api.uploads import body_deadline, check_content_length
from tayyari.billing.razorpay import SIGNATURE_HEADER as RAZORPAY_SIGNATURE
from tayyari.container import Container
from tayyari.contract.billing import TIMEZONE_MAX_CHARS, CheckoutRequest, VerifyRequest
from tayyari.core.errors import PayloadTooLarge
from tayyari.core.logging import get_logger

router = APIRouter()
log = get_logger(__name__)

NO_STORE = {"Cache-Control": "no-store"}
STRIPE_SIGNATURE = "stripe-signature"
WEBHOOK_MAX_BYTES = 512 * 1024
"""The largest webhook body read (a Stripe event is a few kilobytes; a bigger one is not from Stripe)."""
BODY_TOO_LARGE = "Request body too large"


async def _shed(request: Request, container: Container, ip_hash: str | None, client_ip: str | None) -> None:
    container.rate_limiter.shed(
        "chat",
        ip_hash,
        ip_hash_48=subnet_hash(container.ip_hash_key, client_ip),
        exempt=await unlimited(request, container),
    )


def _timezone(value: str | None) -> str | None:
    return value.strip()[:TIMEZONE_MAX_CHARS] if value and value.strip() else None


async def _read_raw(request: Request, container: Container) -> bytes:
    """The whole body as bytes (a signature is computed over exactly these), capped as it arrives."""
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


@router.get("/api/billing/entitlements")
async def get_entitlements(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc.ip_hash, rc.client_ip)
    user = await get_current_user(request)
    answer = await container.billing.entitlements_view(
        user,
        accept_language=request.headers.get("accept-language"),
        timezone=_timezone(request.query_params.get("timezone")),
    )
    return JSONResponse(answer.to_dict(), headers=NO_STORE)


@router.post("/api/billing/checkout")
async def checkout(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc.ip_hash, rc.client_ip)
    user = await require_user(request)
    body = await read_model(request, container, CheckoutRequest)
    currency = body.currency or container.billing.currency_for(
        user, accept_language=request.headers.get("accept-language"), timezone=_timezone(body.timezone)
    )
    answer = await container.billing.checkout(user, body.product, currency)
    return JSONResponse(answer.to_dict(), headers=NO_STORE)


@router.post("/api/billing/verify")
async def verify(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc.ip_hash, rc.client_ip)
    user = await require_user(request)
    body = await read_model(request, container, VerifyRequest)
    answer = await container.billing.verify_razorpay(
        user,
        order_id=body.razorpay_order_id,
        payment_id=body.razorpay_payment_id,
        signature=body.razorpay_signature,
    )
    return JSONResponse(answer.to_dict(), headers=NO_STORE)


@router.post("/api/billing/webhooks/razorpay")
async def razorpay_webhook(request: Request) -> JSONResponse:
    container = get_container(request)
    raw = await _read_raw(request, container)
    await container.billing.webhook("razorpay", raw, request.headers.get(RAZORPAY_SIGNATURE))
    return JSONResponse({"ok": True}, headers=NO_STORE)


@router.post("/api/billing/webhooks/stripe")
async def stripe_webhook(request: Request) -> JSONResponse:
    container = get_container(request)
    raw = await _read_raw(request, container)
    await container.billing.webhook("stripe", raw, request.headers.get(STRIPE_SIGNATURE))
    return JSONResponse({"ok": True}, headers=NO_STORE)


__all__ = ["BODY_TOO_LARGE", "WEBHOOK_MAX_BYTES", "router"]
