"""Stripe: a Checkout Session created through the SDK (over an in-memory HTTP client, so no network) and webhooks
verified with ``construct_event``: valid, forged, tampered, stale and malformed signatures."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from tayyari.billing.gateway import InvalidSignature, PaymentProviderError
from tayyari.billing.stripe_gw import StripeGateway
from tests.fakes.billing import (
    STRIPE_KEY,
    STRIPE_WEBHOOK_SECRET,
    FakeStripeHttp,
    sign_stripe,
    stripe_event,
)


def gateway(http: FakeStripeHttp | None = None) -> tuple[StripeGateway, FakeStripeHttp]:
    client = http or FakeStripeHttp()
    return StripeGateway(
        secret_key=STRIPE_KEY, webhook_secret=STRIPE_WEBHOOK_SECRET, http_client=client
    ), client


SESSION_ARGS: dict[str, Any] = {
    "order_id": "o" * 32,
    "user_id": "u" * 32,
    "product": "pack3",
    "title": "Leafout: three postings",
    "amount_minor": 900,
    "currency": "USD",
    "success_url": "https://tayyari.example/pricing?checkout=success",
    "cancel_url": "https://tayyari.example/pricing?checkout=cancelled",
    "customer_email": "ana@example.com",
}


# ───────────────────────── the Checkout Session ─────────────────────────


async def test_a_one_time_payment_session_is_created_with_our_order_in_it() -> None:
    stripe_gw, http = gateway()
    session = await stripe_gw.create_session(**SESSION_ARGS)
    assert (session.id, session.url) == ("cs_test_0001", "https://checkout.stripe.com/c/pay/cs_test_0001")

    (request,) = http.requests
    assert (request["method"], request["url"]) == ("post", "https://api.stripe.com/v1/checkout/sessions")
    assert request["headers"]["Authorization"] == f"Bearer {STRIPE_KEY}"
    assert request["headers"]["Idempotency-Key"] == "tayyari-order-" + "o" * 32
    form = request["form"]
    assert form["mode"] == "payment"
    assert (
        form["success_url"] == SESSION_ARGS["success_url"]
        and form["cancel_url"] == SESSION_ARGS["cancel_url"]
    )
    assert form["client_reference_id"] == "o" * 32 and form["customer_email"] == "ana@example.com"
    assert form["line_items[0][quantity]"] == "1"
    assert form["line_items[0][price_data][currency]"] == "usd"
    assert form["line_items[0][price_data][unit_amount]"] == "900"
    assert form["line_items[0][price_data][product_data][name]"] == "Leafout: three postings"
    assert form["metadata[order_id]"] == "o" * 32 and form["metadata[user_id]"] == "u" * 32
    assert form["metadata[product]"] == "pack3"


async def test_without_an_email_none_is_sent() -> None:
    stripe_gw, http = gateway()
    await stripe_gw.create_session(**{**SESSION_ARGS, "customer_email": None})
    assert "customer_email" not in http.requests[0]["form"]


@pytest.mark.parametrize(
    "reply",
    [
        lambda url, form: (
            400,
            {"error": {"type": "invalid_request_error", "message": "Invalid currency: xyz"}},
        ),
        lambda url, form: (
            401,
            {"error": {"type": "authentication_error", "message": f"Invalid API Key {STRIPE_KEY}"}},
        ),
        lambda url, form: (500, {"error": {"message": "boom"}}),
        lambda url, form: (200, {"id": "cs_x", "object": "checkout.session"}),  # no payment page
        lambda url, form: (
            200,
            {"object": "checkout.session", "url": "https://checkout.stripe.com/x"},
        ),  # no id
    ],
)
async def test_a_refusal_is_a_provider_error_without_the_details(reply: Any) -> None:
    stripe_gw, _ = gateway(FakeStripeHttp(reply))
    with pytest.raises(PaymentProviderError) as raised:
        await stripe_gw.create_session(**SESSION_ARGS)
    text = str(raised.value)
    for private in (STRIPE_KEY, "Invalid currency", "boom", "ana@example.com"):
        assert private not in text


# ───────────────────────── webhooks ─────────────────────────


def test_a_completed_paid_session_is_a_paid_event_with_what_was_paid() -> None:
    stripe_gw, _ = gateway()
    raw = stripe_event("checkout.session.completed")
    parsed = stripe_gw.parse_webhook(raw, sign_stripe(raw))
    assert (parsed.kind, parsed.provider_order_id, parsed.payment_id) == ("paid", "cs_test_a1B2", "pi_77")
    assert (parsed.amount_minor, parsed.currency, parsed.event) == (900, "USD", "checkout.session.completed")
    assert "ana@example.com" not in repr(parsed)


def test_a_completed_session_still_waiting_for_the_money_is_not_paid_yet() -> None:
    stripe_gw, _ = gateway()
    raw = stripe_event("checkout.session.completed", payment_status="unpaid")
    assert stripe_gw.parse_webhook(raw, sign_stripe(raw)).kind == "ignored"
    raw = stripe_event("checkout.session.async_payment_succeeded", payment_status="paid")
    assert stripe_gw.parse_webhook(raw, sign_stripe(raw)).kind == "paid"  # ... until the bank says so


@pytest.mark.parametrize("name", ["checkout.session.async_payment_failed", "checkout.session.expired"])
def test_a_session_that_cannot_be_paid_is_a_failed_event(name: str) -> None:
    stripe_gw, _ = gateway()
    raw = stripe_event(name, payment_status="unpaid")
    parsed = stripe_gw.parse_webhook(raw, sign_stripe(raw))
    assert (parsed.kind, parsed.provider_order_id) == ("failed", "cs_test_a1B2")


@pytest.mark.parametrize("name", ["charge.refunded", "payment_intent.succeeded", "customer.created"])
def test_events_about_other_things_are_ignored(name: str) -> None:
    stripe_gw, _ = gateway()
    raw = stripe_event(name, object="charge")
    assert stripe_gw.parse_webhook(raw, sign_stripe(raw)).kind == "ignored"


def test_an_event_without_a_session_object_is_ignored() -> None:
    stripe_gw, _ = gateway()
    raw = json.dumps(
        {"id": "evt_2", "object": "event", "type": "checkout.session.completed", "data": {}}
    ).encode()
    assert stripe_gw.parse_webhook(raw, sign_stripe(raw)).kind == "ignored"


def test_a_replayed_event_parses_the_same_each_time() -> None:
    stripe_gw, _ = gateway()
    raw = stripe_event("checkout.session.completed")
    header = sign_stripe(raw)
    assert stripe_gw.parse_webhook(raw, header) == stripe_gw.parse_webhook(raw, header)


def test_a_missing_or_malformed_signature_header_is_refused() -> None:
    stripe_gw, _ = gateway()
    raw = stripe_event("checkout.session.completed")
    for header in (None, "", "garbage", "t=1", "v1=abc", f"t={int(time.time())},v1=", "t=abc,v1=def"):
        with pytest.raises(InvalidSignature):
            stripe_gw.parse_webhook(raw, header)


def test_a_signature_made_with_another_secret_is_refused() -> None:
    stripe_gw, _ = gateway()
    raw = stripe_event("checkout.session.completed")
    for wrong in (STRIPE_KEY, "whsec_guess", ""):
        with pytest.raises(InvalidSignature):
            stripe_gw.parse_webhook(raw, sign_stripe(raw, secret=wrong))


def test_a_body_changed_after_signing_is_refused() -> None:
    stripe_gw, _ = gateway()
    raw = stripe_event("checkout.session.completed")
    header = sign_stripe(raw)
    with pytest.raises(InvalidSignature):
        stripe_gw.parse_webhook(raw.replace(b"900", b"1"), header)
    with pytest.raises(InvalidSignature):
        stripe_gw.parse_webhook(raw + b"\n", header)


def test_an_old_signature_is_refused_so_a_captured_request_cannot_be_replayed_later() -> None:
    stripe_gw, _ = gateway()
    raw = stripe_event("checkout.session.completed")
    with pytest.raises(InvalidSignature):
        stripe_gw.parse_webhook(raw, sign_stripe(raw, at=time.time() - 3600))


def test_a_signed_body_that_is_not_json_is_refused() -> None:
    stripe_gw, _ = gateway()
    raw = b"definitely not json"
    with pytest.raises(InvalidSignature):
        stripe_gw.parse_webhook(raw, sign_stripe(raw))
