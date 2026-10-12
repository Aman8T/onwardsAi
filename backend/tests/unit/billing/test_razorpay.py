"""Razorpay: the two signatures (checked against an independent HMAC), order creation over a mock transport, and how a
webhook is reduced to a `PaymentEvent`. No test here can reach the network."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx2
import pytest

from tayyari.billing.gateway import InvalidSignature, PaymentProviderError
from tayyari.billing.razorpay import ORDERS_URL, RazorpayGateway, checkout_signature, webhook_signature
from tests.fakes.billing import (
    RZP_KEY_ID,
    RZP_KEY_SECRET,
    RZP_WEBHOOK_SECRET,
    FakeRazorpay,
    hmac_hex,
)
from tests.fakes.billing import razorpay_event as event
from tests.fakes.billing import razorpay_payment as payment_entity
from tests.fakes.billing import sign_razorpay as signed


def created(request: httpx2.Request) -> httpx2.Response:
    body = json.loads(request.content)
    return httpx2.Response(
        200, json={"id": "order_Abc123", "amount": body["amount"], "currency": body["currency"]}
    )


class Razorpay:
    """A gateway over a mock transport that records the requests and answers with ``reply``."""

    def __init__(self, reply: Any = created) -> None:
        self.fake = FakeRazorpay(reply)
        self.client = httpx2.AsyncClient(transport=self.fake.transport)
        self.gateway = RazorpayGateway(
            key_id=RZP_KEY_ID,
            key_secret=RZP_KEY_SECRET,
            webhook_secret=RZP_WEBHOOK_SECRET,
            client=self.client,
        )

    @property
    def requests(self) -> list[httpx2.Request]:
        return self.fake.requests


@pytest.fixture
def rzp() -> Razorpay:
    return Razorpay()


# ───────────────────────── the hand-off signature ─────────────────────────


def test_the_handoff_signature_is_hmac_sha256_of_order_pipe_payment(rzp: Razorpay) -> None:
    expected = hmac_hex(RZP_KEY_SECRET, b"order_Abc123|pay_9")
    assert checkout_signature(RZP_KEY_SECRET, "order_Abc123", "pay_9") == expected
    assert rzp.gateway.verify_checkout("order_Abc123", "pay_9", expected) is True


@pytest.mark.parametrize(
    ("order_id", "payment_id", "signature"),
    [
        ("order_Other", "pay_9", None),  # a signature for another order
        ("order_Abc123", "pay_Other", None),
        ("order_Abc123", "pay_9", "0" * 64),
        ("order_Abc123", "pay_9", ""),
        ("order_Abc123", "pay_9", "x" * 500),
        ("order_Abc123", "pay_9", "é" * 10),  # not ASCII: refused, not an exception
        ("order_Abc123|pay_9", "", None),  # the pipe cannot be used to shift the boundary
    ],
)
def test_a_handoff_signature_that_does_not_match_is_refused(
    rzp: Razorpay, order_id: str, payment_id: str, signature: str | None
) -> None:
    good = hmac_hex(RZP_KEY_SECRET, b"order_Abc123|pay_9")
    assert (
        rzp.gateway.verify_checkout(order_id, payment_id, good if signature is None else signature) is False
    )


def test_the_webhook_secret_is_not_the_handoff_secret(rzp: Razorpay) -> None:
    wrong = hmac_hex(RZP_WEBHOOK_SECRET, b"order_Abc123|pay_9")
    assert rzp.gateway.verify_checkout("order_Abc123", "pay_9", wrong) is False


# ───────────────────────── creating an order ─────────────────────────


async def test_an_order_is_created_with_basic_auth_and_our_receipt(rzp: Razorpay) -> None:
    order = await rzp.gateway.create_order(
        amount_minor=49_900, currency="INR", receipt="r" * 32, notes={"order": "o1", "user": "u1"}
    )
    assert (order.id, order.amount_minor, order.currency) == ("order_Abc123", 49_900, "INR")

    (request,) = rzp.requests
    assert (request.method, str(request.url)) == ("POST", ORDERS_URL)
    scheme, _, token = request.headers["authorization"].partition(" ")
    assert scheme == "Basic"
    assert base64.b64decode(token).decode() == f"{RZP_KEY_ID}:{RZP_KEY_SECRET}"
    assert json.loads(request.content) == {
        "amount": 49_900,
        "currency": "INR",
        "receipt": "r" * 32,
        "notes": {"order": "o1", "user": "u1"},
    }


def error_answer(status: int, code: str, description: str) -> Any:
    return lambda _request: httpx2.Response(
        status, json={"error": {"code": code, "description": description}}
    )


@pytest.mark.parametrize(
    "reply",
    [
        error_answer(400, "BAD_REQUEST_ERROR", "Amount must be at least 100"),
        error_answer(401, "BAD_REQUEST_ERROR", "Authentication failed"),
        lambda r: httpx2.Response(500, text="<html>oops</html>"),
        lambda r: httpx2.Response(200, text="not json"),
        lambda r: httpx2.Response(200, json=["a"]),
        lambda r: httpx2.Response(200, json={"currency": "INR", "amount": 49_900}),  # no id
        lambda r: httpx2.Response(
            200, json={"id": "order_x", "amount": 1, "currency": "INR"}
        ),  # not the amount asked
        lambda r: httpx2.Response(200, json={"id": "order_x", "amount": 49_900, "currency": "USD"}),
        lambda r: httpx2.ConnectError("boom"),
        lambda r: httpx2.ReadTimeout("slow"),
    ],
)
async def test_a_failed_or_odd_answer_is_a_provider_error_without_the_details(reply: Any) -> None:
    rzp = Razorpay(reply)
    with pytest.raises(PaymentProviderError) as raised:
        await rzp.gateway.create_order(amount_minor=49_900, currency="INR", receipt="r", notes={})
    text = str(raised.value)
    for secret in (RZP_KEY_ID, RZP_KEY_SECRET, "Amount must be", "Authentication failed", "oops"):
        assert secret not in text  # no key, no provider description, no body


async def test_the_status_and_error_code_are_kept_for_the_log() -> None:
    rzp = Razorpay(error_answer(400, "BAD_REQUEST_ERROR", "no"))
    with pytest.raises(PaymentProviderError, match=r"answered 400 \(BAD_REQUEST_ERROR\)"):
        await rzp.gateway.create_order(amount_minor=1, currency="INR", receipt="r", notes={})


# ───────────────────────── webhooks ─────────────────────────


def test_the_webhook_signature_is_hmac_sha256_of_the_raw_body() -> None:
    raw = b'{"event":"order.paid"}'
    assert webhook_signature(RZP_WEBHOOK_SECRET, raw) == hmac_hex(RZP_WEBHOOK_SECRET, raw)


def test_a_signed_order_paid_event_is_a_paid_event_with_what_was_paid(rzp: Razorpay) -> None:
    raw, signature = signed(
        event("order.paid", payment=payment_entity(), order={"id": "order_Abc123", "amount_paid": 49_900})
    )
    parsed = rzp.gateway.parse_webhook(raw, signature)
    assert (parsed.kind, parsed.provider_order_id, parsed.payment_id) == ("paid", "order_Abc123", "pay_9")
    assert (parsed.amount_minor, parsed.currency, parsed.event) == (49_900, "INR", "order.paid")
    assert "ana@example.com" not in repr(parsed) and "+91" not in repr(
        parsed
    )  # no personal data in the event


def test_a_captured_payment_is_paid_too(rzp: Razorpay) -> None:
    raw, signature = signed(event("payment.captured", payment=payment_entity()))
    parsed = rzp.gateway.parse_webhook(raw, signature)
    assert (parsed.kind, parsed.provider_order_id, parsed.amount_minor) == ("paid", "order_Abc123", 49_900)


def test_the_amount_falls_back_to_the_order_when_the_payment_is_absent(rzp: Razorpay) -> None:
    raw, signature = signed(
        event("order.paid", order={"id": "order_Abc123", "amount_paid": 49_900, "currency": "inr"})
    )
    parsed = rzp.gateway.parse_webhook(raw, signature)
    assert (parsed.kind, parsed.amount_minor, parsed.currency) == ("paid", 49_900, "INR")


def test_a_payment_that_is_not_captured_is_not_paid(rzp: Razorpay) -> None:
    raw, signature = signed(event("order.paid", payment=payment_entity(status="authorized")))
    assert rzp.gateway.parse_webhook(raw, signature).kind == "ignored"


def test_a_failed_payment_is_a_failed_event(rzp: Razorpay) -> None:
    raw, signature = signed(event("payment.failed", payment=payment_entity(status="failed")))
    parsed = rzp.gateway.parse_webhook(raw, signature)
    assert (parsed.kind, parsed.provider_order_id) == ("failed", "order_Abc123")


@pytest.mark.parametrize(
    "name",
    ["payment.authorized", "refund.processed", "order.notification.delivered", "", "payment.captured.extra"],
)
def test_other_events_are_ignored(rzp: Razorpay, name: str) -> None:
    raw, signature = signed(event(name, payment=payment_entity()))
    assert rzp.gateway.parse_webhook(raw, signature).kind == "ignored"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"not json", id="text"),
        pytest.param(b"[1, 2]", id="list"),
        pytest.param(b'"text"', id="string"),
        pytest.param(b"null", id="null"),
        pytest.param(b"", id="empty"),
        pytest.param(b"[" * 100_000, id="nested too deep"),
    ],
)
def test_a_signed_body_that_is_not_an_event_is_ignored_not_an_error(rzp: Razorpay, body: bytes) -> None:
    raw, signature = signed(body)
    assert rzp.gateway.parse_webhook(raw, signature).kind == "ignored"


def test_odd_field_types_do_not_break_parsing(rzp: Razorpay) -> None:
    odd = {"id": 5, "order_id": ["x"], "amount": "49900", "currency": 3, "status": "captured"}
    raw, signature = signed(event("payment.captured", payment=odd))
    parsed = rzp.gateway.parse_webhook(raw, signature)
    assert (parsed.kind, parsed.provider_order_id, parsed.amount_minor, parsed.currency) == (
        "paid",
        None,
        None,
        None,
    )


@pytest.mark.parametrize("signature", [None, "", "   ", "0" * 64, "abc", "é" * 64])
def test_a_missing_or_wrong_signature_is_refused_before_anything_is_read(
    rzp: Razorpay, signature: str | None
) -> None:
    raw, _ = signed(event("order.paid", payment=payment_entity()))
    with pytest.raises(InvalidSignature):
        rzp.gateway.parse_webhook(raw, signature)


def test_a_body_changed_after_signing_is_refused(rzp: Razorpay) -> None:
    raw, signature = signed(event("order.paid", payment=payment_entity()))
    with pytest.raises(InvalidSignature):
        rzp.gateway.parse_webhook(raw.replace(b"49900", b"1"), signature)
    with pytest.raises(InvalidSignature):
        rzp.gateway.parse_webhook(
            raw + b" ", signature
        )  # even whitespace: the signature covers the raw bytes


def test_a_signature_made_with_another_secret_is_refused(rzp: Razorpay) -> None:
    raw = json.dumps(event("order.paid", payment=payment_entity())).encode()
    for wrong in (RZP_KEY_SECRET, "guess", ""):
        with pytest.raises(InvalidSignature):
            rzp.gateway.parse_webhook(raw, hmac_hex(wrong, raw))


def test_garbage_with_a_bad_signature_is_refused_not_parsed(rzp: Razorpay) -> None:
    with pytest.raises(InvalidSignature):
        rzp.gateway.parse_webhook(b"\xff\xfe not even utf-8", "0" * 64)
