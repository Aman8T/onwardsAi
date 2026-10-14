"""Razorpay through its official Python SDK: create an order, check the Checkout.js hand-off, check a webhook.

    client.order.create({"amount": 49900, "currency": "INR", "receipt": "<our order id>", "notes": {...}})
                                                    -> {"id": "order_...", ...}      HTTP basic auth: key id : key secret
    client.utility.verify_payment_signature({...})  the hand-off
    client.utility.verify_webhook_signature(body, signature, secret)

The page opens Checkout.js with the order id and the *key id* (public), the customer pays in Razorpay's modal, and
Checkout.js hands the page three values. Two signatures protect us, both HMAC-SHA256 in hex and both compared in
constant time (the SDK does the comparing; `checkout_signature` and `webhook_signature` below are the same algorithm
spelled out, for the tests and the fakes that sign):

    hand-off   HMAC(key secret,     "<razorpay_order_id>|<razorpay_payment_id>")  must equal razorpay_signature
    webhook    HMAC(webhook secret, <the raw request body>)                       must equal X-Razorpay-Signature

The hand-off is Razorpay's own signed proof under the key secret, so it settles the order at once; the key pair is all
a payment needs. The webhook, when its secret is set, covers the browser that never came back (a closed tab, a dropped
connection); both end in the same idempotent settle. Without a webhook secret every webhook is refused.

The SDK is synchronous (``requests``): the one network call, ``order.create``, runs in a worker thread with a 10 s
timeout, and the signature checks (pure CPU) stay synchronous. The SDK's retries are left off. Nothing here logs a key,
a signature or a response body, and an error carries the exception's class name only, never Razorpay's description.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import razorpay as sdk
import requests
from razorpay.errors import BadRequestError, GatewayError, ServerError, SignatureVerificationError

from .gateway import InvalidSignature, PaymentEvent, PaymentProviderError

REQUEST_TIMEOUT_S: Final = 10.0
"""Passed to ``requests`` on every call to Razorpay (the SDK forwards it): seconds to connect and to read."""
SIGNATURE_HEADER: Final = "x-razorpay-signature"

_PAID_EVENTS: Final = frozenset({"order.paid", "payment.captured"})
_FAILED_EVENTS: Final = frozenset({"payment.failed"})
_BAD_SIGNATURE: Final = (SignatureVerificationError, TypeError, ValueError)
"""What the SDK's checks raise for a signature that is wrong (`SignatureVerificationError`) or not comparable at
all (``TypeError`` from ``hmac.compare_digest`` on non-ASCII text). All of them mean "refused"."""


def checkout_signature(secret: str, order_id: str, payment_id: str) -> str:
    """The signature Razorpay gives Checkout.js for a paid order (hex)."""
    return hmac.new(secret.encode("utf-8"), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def webhook_signature(secret: str, body: bytes) -> str:
    """The ``X-Razorpay-Signature`` of a webhook body (hex)."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class RazorpayOrder:
    id: str
    amount_minor: int
    currency: str


class RazorpayGateway:
    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        webhook_secret: str | None,
        session: requests.Session | None = None,
    ) -> None:
        """``session`` is the ``requests.Session`` the SDK sends through (tests hand in one that answers from memory)."""
        self._key_id = key_id
        self._webhook_secret = webhook_secret or None  # an empty secret would let anyone sign: same as none
        self._client: Any = sdk.Client(session=session, auth=(key_id, key_secret))
        self._client.enable_retry(False)

    @property
    def key_id(self) -> str:
        """The public half of the API key: Checkout.js needs it to open."""
        return self._key_id

    @property
    def webhooks_enabled(self) -> bool:
        """True when a webhook secret is set; without one every webhook is refused."""
        return self._webhook_secret is not None

    async def create_order(
        self, *, amount_minor: int, currency: str, receipt: str, notes: Mapping[str, str]
    ) -> RazorpayOrder:
        payload = {"amount": amount_minor, "currency": currency, "receipt": receipt, "notes": dict(notes)}
        try:
            body = await asyncio.to_thread(self._client.order.create, payload, timeout=REQUEST_TIMEOUT_S)
        except (BadRequestError, GatewayError, ServerError) as err:
            raise PaymentProviderError(_refused(err)) from None
        except requests.exceptions.JSONDecodeError:
            raise PaymentProviderError("razorpay answered with something that is not JSON") from None
        except requests.RequestException as err:
            raise PaymentProviderError(f"razorpay could not be reached ({type(err).__name__})") from None
        except TypeError as err:  # the SDK reading an error body that is JSON but not an object
            raise PaymentProviderError(
                f"razorpay answered with something unexpected ({type(err).__name__})"
            ) from None
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
        """True when ``signature`` is Razorpay's for this order and payment. Never raises."""
        params = {
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        }
        try:
            return self._client.utility.verify_payment_signature(params) is True
        except _BAD_SIGNATURE:
            return False

    def parse_webhook(self, raw: bytes, signature: str | None) -> PaymentEvent:
        """Check the signature over the raw body, then reduce the event. Raises `InvalidSignature` first, always."""
        secret = self._webhook_secret
        if secret is None:
            raise InvalidSignature("razorpay webhooks are not configured")
        if not signature:
            raise InvalidSignature("razorpay webhook signature")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise InvalidSignature("razorpay webhook signature") from None
        try:
            verified = self._client.utility.verify_webhook_signature(text, signature.strip(), secret)
        except _BAD_SIGNATURE:
            raise InvalidSignature("razorpay webhook signature") from None
        if verified is not True:
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


def _refused(err: Exception) -> str:
    """``razorpay refused the order (BadRequestError)``: the exception's class, never Razorpay's description.

    A rejected key is the one misconfiguration an operator must be able to see in the log, so it is named; the
    SDK reports it as a `BadRequestError` whose message begins "Authentication".
    """
    text = f"razorpay refused the order ({type(err).__name__})"
    if isinstance(err, BadRequestError) and str(err).lower().startswith("authentication"):
        text += " (the API key was rejected)"
    return text


def _entity(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    holder = payload.get(key)
    entity = holder.get("entity") if isinstance(holder, dict) else None
    return entity if isinstance(entity, dict) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = [
    "REQUEST_TIMEOUT_S",
    "SIGNATURE_HEADER",
    "RazorpayGateway",
    "RazorpayOrder",
    "checkout_signature",
    "webhook_signature",
]
