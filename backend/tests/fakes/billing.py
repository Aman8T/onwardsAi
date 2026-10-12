"""Fakes for the payment tests: the two providers' network edges and the signed webhooks they send.

FakeRazorpay        Razorpay's order API behind an ``httpx2.MockTransport`` (records requests, answers from ``reply``)
FakeStripeHttp      the ``stripe.HTTPClient`` the SDK sends its requests through, answering from memory
razorpay_event      a Razorpay webhook body; ``sign_razorpay`` signs raw bytes the way Razorpay does
stripe_event        a Stripe webhook body; ``sign_stripe`` makes the ``Stripe-Signature`` header for raw bytes
FakeCustomer        the payer: pays an order through the Checkout.js hand-off or lets a webhook do it

Every value here is made up (they are not credentials of any account); nothing can reach a real provider.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import parse_qsl

import httpx2
import stripe

RZP_KEY_ID = "rzp_test_keyid"
RZP_KEY_SECRET = "rzp-key-secret"
RZP_WEBHOOK_SECRET = "rzp-webhook-secret"
STRIPE_KEY = "sk_test_made_up"
STRIPE_WEBHOOK_SECRET = "whsec_made_up"

ALL_SECRETS = {
    "razorpay_key_id": RZP_KEY_ID,
    "razorpay_key_secret": RZP_KEY_SECRET,
    "razorpay_webhook_secret": RZP_WEBHOOK_SECRET,
    "stripe_secret_key": STRIPE_KEY,
    "stripe_webhook_secret": STRIPE_WEBHOOK_SECRET,
}
PRICES = {"pack3_inr": 49_900, "pass30_inr": 99_900, "pack3_usd": 900, "pass30_usd": 1_900}


def hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


# ───────────────────────── Razorpay ─────────────────────────


class FakeRazorpay:
    """Razorpay's ``POST /v1/orders``. By default it creates ``order_<n>`` with the amount and currency it was asked for."""

    def __init__(self, reply: Callable[[httpx2.Request], httpx2.Response | Exception] | None = None) -> None:
        self.requests: list[httpx2.Request] = []
        self.created = 0
        self.reply = reply or self._create
        self.transport = httpx2.MockTransport(self._handle)

    def _create(self, request: httpx2.Request) -> httpx2.Response:
        self.created += 1
        body = json.loads(request.content)
        return httpx2.Response(
            200,
            json={
                "id": f"order_Rzp{self.created:04d}",
                "amount": body["amount"],
                "currency": body["currency"],
            },
        )

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        result = self.reply(request)
        if isinstance(result, Exception):
            raise result
        return result

    @property
    def last_order_id(self) -> str:
        return f"order_Rzp{self.created:04d}"


def razorpay_payment(**fields: Any) -> dict[str, Any]:
    return {
        "id": "pay_9",
        "order_id": "order_Abc123",
        "amount": 49_900,
        "currency": "INR",
        "status": "captured",
        # what Razorpay sends but we must never keep:
        "email": "ana@example.com",
        "contact": "+910000000000",
        **fields,
    }


def razorpay_event(
    name: str, *, payment: dict[str, Any] | None = None, order: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if payment is not None:
        payload["payment"] = {"entity": payment}
    if order is not None:
        payload["order"] = {"entity": order}
    return {"entity": "event", "event": name, "payload": payload}


def sign_razorpay(body: dict[str, Any] | bytes, *, secret: str = RZP_WEBHOOK_SECRET) -> tuple[bytes, str]:
    """``(raw body, X-Razorpay-Signature)``."""
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return raw, hmac_hex(secret, raw)


def razorpay_paid(
    order_id: str, *, amount: int = 49_900, currency: str = "INR", payment_id: str = "pay_9"
) -> bytes:
    """A signed-ready ``order.paid`` body for ``order_id``."""
    payment = razorpay_payment(id=payment_id, order_id=order_id, amount=amount, currency=currency)
    return json.dumps(
        razorpay_event("order.paid", payment=payment, order={"id": order_id, "amount_paid": amount})
    ).encode()


def razorpay_handoff(order_id: str, payment_id: str = "pay_9") -> dict[str, str]:
    """What Checkout.js passes to the page's success handler (and the page posts to ``/api/billing/verify``)."""
    return {
        "razorpay_order_id": order_id,
        "razorpay_payment_id": payment_id,
        "razorpay_signature": hmac_hex(RZP_KEY_SECRET, f"{order_id}|{payment_id}".encode()),
    }


# ───────────────────────── Stripe ─────────────────────────


class FakeStripeHttp(stripe.HTTPClient):
    """What the SDK sends its requests through: records them, answers from ``reply`` (status, body)."""

    name = "in-memory"

    def __init__(
        self, reply: Callable[[str, dict[str, str]], tuple[int, dict[str, Any]]] | None = None
    ) -> None:
        super().__init__()
        self.requests: list[dict[str, Any]] = []
        self.created = 0
        self.reply = reply or self._create

    def _create(self, _url: str, _form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        self.created += 1
        session_id = f"cs_test_{self.created:04d}"
        return 200, {
            "id": session_id,
            "object": "checkout.session",
            "url": f"https://checkout.stripe.com/c/pay/{session_id}",
            "status": "open",
        }

    async def request_async(  # type: ignore[override]
        self, method: str, url: str, headers: Mapping[str, str], post_data: Any = None
    ) -> tuple[bytes, int, Mapping[str, str]]:
        form = dict(parse_qsl(post_data)) if isinstance(post_data, str) else {}
        self.requests.append({"method": method, "url": url, "headers": dict(headers), "form": form})
        status, body = self.reply(url, form)
        return json.dumps(body).encode(), status, {"Content-Type": "application/json"}

    async def sleep_async(self, secs: float) -> None:  # type: ignore[override]
        return None

    async def close_async(self) -> None:
        return None

    @property
    def last_session_id(self) -> str:
        return f"cs_test_{self.created:04d}"


def stripe_event(name: str, session_id: str = "cs_test_a1B2", **session: Any) -> bytes:
    body = {
        "id": "evt_1",
        "object": "event",
        "type": name,
        "data": {
            "object": {
                "id": session_id,
                "object": "checkout.session",
                "payment_status": "paid",
                "amount_total": 900,
                "currency": "usd",
                "payment_intent": "pi_77",
                "customer_details": {"email": "ana@example.com"},
                **session,
            }
        },
    }
    return json.dumps(body).encode()


def sign_stripe(raw: bytes, *, secret: str = STRIPE_WEBHOOK_SECRET, at: float | None = None) -> str:
    """The ``Stripe-Signature`` header for ``raw``."""
    stamp = int(time.time() if at is None else at)
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + raw, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


__all__ = [
    "ALL_SECRETS",
    "PRICES",
    "RZP_KEY_ID",
    "RZP_KEY_SECRET",
    "RZP_WEBHOOK_SECRET",
    "STRIPE_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "FakeRazorpay",
    "FakeStripeHttp",
    "hmac_hex",
    "razorpay_event",
    "razorpay_handoff",
    "razorpay_paid",
    "razorpay_payment",
    "sign_razorpay",
    "sign_stripe",
    "stripe_event",
]
