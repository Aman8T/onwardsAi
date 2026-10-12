"""Razorpay, over plain HTTPS: create an order, check the Checkout.js hand-off, check a webhook.

    POST https://api.razorpay.com/v1/orders      HTTP basic auth: key id : key secret
    {"amount": 49900, "currency": "INR", "receipt": "<our order id>", "notes": {...}}   -> {"id": "order_...", ...}

The page opens Checkout.js with the order id and the *key id* (public), the customer pays in Razorpay's modal, and
Checkout.js hands the page three values. Two signatures protect us, both HMAC-SHA256 in hex and both compared in
constant time:

    hand-off   HMAC(key secret,     "<razorpay_order_id>|<razorpay_payment_id>")  must equal razorpay_signature
    webhook    HMAC(webhook secret, <the raw request body>)                       must equal X-Razorpay-Signature

Only a signed webhook is the source of truth; the hand-off lets the page show the new balance at once, and both end
in the same idempotent settle. Nothing here logs a key, a signature or a response body.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import httpx2

from .gateway import InvalidSignature, PaymentEvent, PaymentProviderError

ORDERS_URL: Final = "https://api.razorpay.com/v1/orders"
REQUEST_TIMEOUT_S: Final = 10.0
SIGNATURE_HEADER: Final = "x-razorpay-signature"

_PAID_EVENTS: Final = frozenset({"order.paid", "payment.captured"})
_FAILED_EVENTS: Final = frozenset({"payment.failed"})


def checkout_signature(secret: str, order_id: str, payment_id: str) -> str:
    """The signature Razorpay gives Checkout.js for a paid order (hex)."""
    return hmac.new(secret.encode("utf-8"), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def webhook_signature(secret: str, body: bytes) -> str:
    """The ``X-Razorpay-Signature`` of a webhook body (hex)."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class RazorpayOrder:
    id: str
    amount_minor: int
    currency: str


class RazorpayGateway:
    def __init__(
        self, *, key_id: str, key_secret: str, webhook_secret: str, client: httpx2.AsyncClient
    ) -> None:
        self._key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret
        self._client = client

    @property
    def key_id(self) -> str:
        """The public half of the API key: Checkout.js needs it to open."""
        return self._key_id

    async def create_order(
        self, *, amount_minor: int, currency: str, receipt: str, notes: Mapping[str, str]
    ) -> RazorpayOrder:
        payload = {"amount": amount_minor, "currency": currency, "receipt": receipt, "notes": dict(notes)}
        try:
            response = await self._client.post(
                ORDERS_URL, json=payload, auth=(self._key_id, self._key_secret), timeout=REQUEST_TIMEOUT_S
            )
        except httpx2.HTTPError as err:
            raise PaymentProviderError(f"razorpay could not be reached ({type(err).__name__})") from None
        if response.status_code >= 300:
            raise PaymentProviderError(f"razorpay answered {response.status_code}{_error_code(response)}")
        try:
            body = response.json()
        except ValueError:
            raise PaymentProviderError("razorpay answered with something that is not JSON") from None
        order_id = body.get("id") if isinstance(body, dict) else None
        if (
            not isinstance(order_id, str)
            or not order_id
            or body.get("amount") != amount_minor
            or body.get("currency") != currency
        ):
            raise PaymentProviderError("razorpay created an order that is not the one asked for")
        return RazorpayOrder(id=order_id, amount_minor=amount_minor, currency=currency)

    def verify_checkout(self, order_id: str, payment_id: str, signature: str) -> bool:
        """True when ``signature`` is Razorpay's for this order and payment."""
        return _same(checkout_signature(self._key_secret, order_id, payment_id), signature)

    def parse_webhook(self, raw: bytes, signature: str | None) -> PaymentEvent:
        """Check the signature over the raw body, then reduce the event. Raises `InvalidSignature` first, always."""
        if not signature or not _same(webhook_signature(self._webhook_secret, raw), signature.strip()):
            raise InvalidSignature("razorpay webhook signature")
        try:
            body = json.loads(raw)
        except (ValueError, RecursionError):
            return PaymentEvent("ignored", None, event="unreadable")
        if not isinstance(body, dict):
            return PaymentEvent("ignored", None, event="unreadable")
        name = str(body.get("event", ""))[:64]
        payload = body.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        payment = _entity(payload, "payment")
        order = _entity(payload, "order")
        order_id = _text(order.get("id")) or _text(payment.get("order_id"))
        if name in _PAID_EVENTS:
            status = payment.get("status")
            if isinstance(status, str) and status != "captured":
                return PaymentEvent("ignored", order_id, event=name)
            amount = _whole(payment.get("amount"))
            if amount is None:
                amount = _whole(order.get("amount_paid"))
            currency = _text(payment.get("currency")) or _text(order.get("currency"))
            return PaymentEvent(
                "paid",
                order_id,
                payment_id=_text(payment.get("id")),
                amount_minor=amount,
                currency=currency.upper() if currency else None,
                event=name,
            )
        if name in _FAILED_EVENTS:
            return PaymentEvent("failed", order_id, payment_id=_text(payment.get("id")), event=name)
        return PaymentEvent("ignored", order_id, event=name)


def _entity(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    holder = payload.get(key)
    entity = holder.get("entity") if isinstance(holder, dict) else None
    return entity if isinstance(entity, dict) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _error_code(response: httpx2.Response) -> str:
    """`` (BAD_REQUEST_ERROR)``: Razorpay's error code, when the body has one; never its description."""
    try:
        body = response.json()
        code = body["error"]["code"] if isinstance(body, dict) else None
    except (ValueError, KeyError, TypeError):
        return ""
    return f" ({code[:48]})" if isinstance(code, str) and code.replace("_", "").isalnum() else ""


__all__ = [
    "ORDERS_URL",
    "SIGNATURE_HEADER",
    "RazorpayGateway",
    "RazorpayOrder",
    "checkout_signature",
    "webhook_signature",
]
