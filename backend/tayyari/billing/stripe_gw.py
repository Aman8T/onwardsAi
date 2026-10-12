"""Stripe, through the ``stripe`` package: a Checkout Session for the payment, ``Webhook.construct_event`` for the
webhook.

The page sends the browser to the Session's ``url`` (Stripe's hosted page); Stripe sends it back to
``<public url>/pricing?checkout=success`` or ``...=cancelled``. The *webhook* is what grants anything: the return URL
proves nothing. Our order id travels in ``client_reference_id`` and ``metadata``, and the Session id (``cs_...``) is
what the order is stored under.

The SDK does its own HTTP. `stripe.HTTPClient` is its seam: production uses the default client, tests hand in one
that answers from memory, so nothing here ever needs a network to be tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import stripe

from .gateway import InvalidSignature, PaymentEvent, PaymentProviderError

MAX_NETWORK_RETRIES: Final = 1

_PAID_EVENTS: Final = frozenset({"checkout.session.completed", "checkout.session.async_payment_succeeded"})
_FAILED_EVENTS: Final = frozenset({"checkout.session.async_payment_failed", "checkout.session.expired"})


@dataclass(frozen=True, slots=True)
class StripeSession:
    id: str
    url: str


class StripeGateway:
    def __init__(
        self, *, secret_key: str, webhook_secret: str, http_client: stripe.HTTPClient | None = None
    ) -> None:
        self._webhook_secret = webhook_secret
        self._client = stripe.StripeClient(
            secret_key, http_client=http_client, max_network_retries=MAX_NETWORK_RETRIES
        )

    async def create_session(
        self,
        *,
        order_id: str,
        user_id: str,
        product: str,
        title: str,
        amount_minor: int,
        currency: str,
        success_url: str,
        cancel_url: str,
        customer_email: str | None,
    ) -> StripeSession:
        """A one-time payment Checkout Session. ``order_id`` doubles as the idempotency key, so a retried request
        cannot create two Sessions."""
        metadata = {"order_id": order_id, "user_id": user_id, "product": product}
        params: dict[str, Any] = {
            "mode": "payment",
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": currency.lower(),
                        "unit_amount": amount_minor,
                        "product_data": {"name": title},
                    },
                }
            ],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": order_id,
            "metadata": metadata,
        }
        if customer_email:
            params["customer_email"] = customer_email
        try:
            session = await self._client.v1.checkout.sessions.create_async(
                params=params,  # type: ignore[arg-type]
                options={"idempotency_key": f"tayyari-order-{order_id}"},
            )
        except stripe.StripeError as err:
            raise PaymentProviderError(f"stripe refused the session ({type(err).__name__})") from None
        body = session.to_dict()
        session_id, url = _text(body.get("id")), _text(body.get("url"))
        if session_id is None or url is None:
            raise PaymentProviderError("stripe created a session without a payment page")
        return StripeSession(id=session_id, url=url)

    def parse_webhook(self, raw: bytes, signature: str | None) -> PaymentEvent:
        """Check the ``Stripe-Signature`` over the raw body (``construct_event``, five minutes of tolerance), then
        reduce the event. Raises `InvalidSignature` first, always."""
        try:
            event = stripe.Webhook.construct_event(raw, signature, self._webhook_secret)
        except (stripe.SignatureVerificationError, ValueError):
            raise InvalidSignature("stripe webhook signature") from None
        body = event.to_dict()
        name = str(body.get("type", ""))[:80]
        data = body.get("data")
        session = data.get("object") if isinstance(data, dict) else None
        if not isinstance(session, dict) or not str(session.get("object", "")).startswith("checkout.session"):
            return PaymentEvent("ignored", None, event=name)
        session_id = _text(session.get("id"))
        if name in _PAID_EVENTS:
            # A delayed method (a bank debit) completes the Session while it is still unpaid: wait for the success.
            if name == "checkout.session.completed" and session.get("payment_status") != "paid":
                return PaymentEvent("ignored", session_id, event=name)
            currency = _text(session.get("currency"))
            return PaymentEvent(
                "paid",
                session_id,
                payment_id=_text(session.get("payment_intent")),
                amount_minor=_whole(session.get("amount_total")),
                currency=currency.upper() if currency else None,
                event=name,
            )
        if name in _FAILED_EVENTS:
            return PaymentEvent("failed", session_id, event=name)
        return PaymentEvent("ignored", session_id, event=name)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = ["StripeGateway", "StripeSession"]
