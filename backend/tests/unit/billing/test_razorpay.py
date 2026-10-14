"""Razorpay through the official SDK: the two signatures (checked against an independent HMAC), order creation over a
``requests.Session`` with no socket, and how a webhook is reduced to a `PaymentEvent`. No test here can reach the
network: the SDK's own session is replaced by `FakeRazorpay`, which answers ``POST /v1/orders`` from memory."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest
import requests

from tayyari.billing.gateway import InvalidSignature, PaymentProviderError
from tayyari.billing.razorpay import REQUEST_TIMEOUT_S, RazorpayGateway, checkout_signature, webhook_signature
from tests.fakes.billing import (
    RZP_KEY_ID,
    RZP_KEY_SECRET,
    RZP_WEBHOOK_SECRET,
    Answer,
    FakeRazorpay,
    RecordedRequest,
    error_answer,
    hmac_hex,
)
from tests.fakes.billing import razorpay_event as event
from tests.fakes.billing import razorpay_payment as payment_entity
from tests.fakes.billing import sign_razorpay as signed


def created(request: RecordedRequest) -> Answer:
    body = request.json
    return Answer(200, {"id": "order_Abc123", "amount": body["amount"], "currency": body["currency"]})


class Razorpay:
    """A gateway over a session with no socket that records the requests and answers with ``reply``."""

    def __init__(self, reply: Any = created, *, webhook_secret: str | None = RZP_WEBHOOK_SECRET) -> None:
        self.session = FakeRazorpay(reply)
        self.gateway = RazorpayGateway(
            key_id=RZP_KEY_ID, key_secret=RZP_KEY_SECRET, webhook_secret=webhook_secret, session=self.session
        )

    @property
    def requests(self) -> list[RecordedRequest]:
        return self.session.requests


@pytest.fixture
def rzp() -> Razorpay:
    return Razorpay()


@pytest.fixture
def keys_only() -> Razorpay:
    """Only the key pair: what a developer has on a laptop."""
    return Razorpay(webhook_secret=None)


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
        ("order_Abc123", "pay_9", "日本語"),
        ("order_Abc123", "pay_9", "\ud800"),  # a lone surrogate, as a broken client could send
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


def test_a_non_ascii_order_or_payment_id_does_not_raise(rzp: Razorpay) -> None:
    good = hmac_hex(RZP_KEY_SECRET, b"order_Abc123|pay_9")
    assert rzp.gateway.verify_checkout("órder", "pay_9", good) is False
    assert rzp.gateway.verify_checkout("order_Abc123", "päy", good) is False
    assert rzp.gateway.verify_checkout("\ud800", "pay_9", good) is False  # cannot even be encoded


def test_the_webhook_secret_is_not_the_handoff_secret(rzp: Razorpay) -> None:
    wrong = hmac_hex(RZP_WEBHOOK_SECRET, b"order_Abc123|pay_9")
    assert rzp.gateway.verify_checkout("order_Abc123", "pay_9", wrong) is False


def test_the_handoff_needs_only_the_key_pair(keys_only: Razorpay) -> None:
    good = hmac_hex(RZP_KEY_SECRET, b"order_Abc123|pay_9")
    assert keys_only.gateway.verify_checkout("order_Abc123", "pay_9", good) is True
    assert keys_only.gateway.verify_checkout("order_Abc123", "pay_9", "0" * 64) is False


# ───────────────────────── creating an order ─────────────────────────


async def test_an_order_is_created_with_basic_auth_and_our_receipt(rzp: Razorpay) -> None:
    order = await rzp.gateway.create_order(
        amount_minor=49_900, currency="INR", receipt="r" * 32, notes={"order": "o1", "user": "u1"}
    )
    assert (order.id, order.amount_minor, order.currency) == ("order_Abc123", 49_900, "INR")

    (request,) = rzp.requests
    assert (request.method, request.url) == ("POST", "https://api.razorpay.com/v1/orders")
    assert request.basic_auth == f"{RZP_KEY_ID}:{RZP_KEY_SECRET}"
    assert request.headers["Content-type"] == "application/json"
    assert request.json == {
        "amount": 49_900,
        "currency": "INR",
        "receipt": "r" * 32,
        "notes": {"order": "o1", "user": "u1"},
    }


async def test_every_call_to_razorpay_has_a_ten_second_timeout(rzp: Razorpay) -> None:
    await rzp.gateway.create_order(amount_minor=49_900, currency="INR", receipt="r", notes={})
    assert REQUEST_TIMEOUT_S == 10.0
    assert [request.timeout for request in rzp.requests] == [10.0]  # what requests handed to the socket layer


async def test_the_blocking_sdk_call_runs_off_the_event_loop(rzp: Razorpay) -> None:
    threads: list[int] = []

    def reply(request: RecordedRequest) -> Answer:
        threads.append(threading.get_ident())
        return created(request)

    rzp.session.reply = reply
    await rzp.gateway.create_order(amount_minor=49_900, currency="INR", receipt="r", notes={})
    assert threads and threads[0] != threading.get_ident()


def refused(status: int, code: str, description: str = "no") -> Any:
    return lambda _request: error_answer(status, code, description)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        pytest.param(
            refused(400, "BAD_REQUEST_ERROR", "Amount must be at least 100"),
            "razorpay refused the order (BadRequestError)",
            id="bad request",
        ),
        pytest.param(
            refused(401, "BAD_REQUEST_ERROR", "Authentication failed"),
            "razorpay refused the order (BadRequestError) (the API key was rejected)",
            id="rejected key",
        ),
        pytest.param(
            refused(502, "GATEWAY_ERROR", "The gateway said gibberish"),
            "razorpay refused the order (GatewayError)",
            id="gateway error",
        ),
        pytest.param(
            refused(500, "SERVER_ERROR", "Something broke inside"),
            "razorpay refused the order (ServerError)",
            id="server error",
        ),
        pytest.param(
            refused(503, "SOMETHING_NEW", "unheard of"),
            "razorpay refused the order (ServerError)",
            id="a code the SDK does not know",
        ),
        pytest.param(
            lambda _r: Answer(500, text="<html>oops</html>"),
            "razorpay answered with something that is not JSON",
            id="html error page",
        ),
        pytest.param(
            lambda _r: Answer(200, text="not json"),
            "razorpay answered with something that is not JSON",
            id="200 that is not json",
        ),
        pytest.param(
            lambda _r: Answer(502, json=None),
            "razorpay answered with something unexpected (TypeError)",
            id="error body that is null",
        ),
        pytest.param(
            lambda _r: Answer(400, json={"error": None}),
            "razorpay answered with something unexpected (TypeError)",
            id="error that is null",
        ),
        pytest.param(
            lambda _r: Answer(400, json={"error": "a string, not an object"}),
            "razorpay refused the order (ServerError)",
            id="error that is a string",
        ),
        pytest.param(
            lambda _r: requests.ConnectionError("boom: https://user:pw@api.razorpay.com"),
            "razorpay could not be reached (ConnectionError)",
            id="connection error",
        ),
        pytest.param(
            lambda _r: requests.ReadTimeout("slow"),
            "razorpay could not be reached (ReadTimeout)",
            id="read timeout",
        ),
        pytest.param(
            lambda _r: requests.exceptions.SSLError("certificate verify failed"),
            "razorpay could not be reached (SSLError)",
            id="tls",
        ),
        pytest.param(
            lambda _r: Answer(200, ["a"]),
            "razorpay created an order that is not the one asked for",
            id="a list",
        ),
        pytest.param(
            lambda _r: Answer(200, {"currency": "INR", "amount": 49_900}),
            "razorpay created an order that is not the one asked for",
            id="no id",
        ),
        pytest.param(
            lambda _r: Answer(200, {"id": "", "currency": "INR", "amount": 49_900}),
            "razorpay created an order that is not the one asked for",
            id="empty id",
        ),
        pytest.param(
            lambda _r: Answer(200, {"id": "order_x", "amount": 1, "currency": "INR"}),
            "razorpay created an order that is not the one asked for",
            id="not the amount asked",
        ),
        pytest.param(
            lambda _r: Answer(200, {"id": "order_x", "amount": 49_900, "currency": "USD"}),
            "razorpay created an order that is not the one asked for",
            id="not the currency asked",
        ),
    ],
)
async def test_a_failed_or_odd_answer_is_a_provider_error_naming_the_class_and_nothing_else(
    reply: Any, expected: str
) -> None:
    rzp = Razorpay(reply)
    with pytest.raises(PaymentProviderError) as raised:
        await rzp.gateway.create_order(amount_minor=49_900, currency="INR", receipt="r", notes={})
    text = str(raised.value)
    assert text == expected
    for private in (
        RZP_KEY_ID,
        RZP_KEY_SECRET,
        "Amount must be",
        "Authentication failed",
        "gibberish",
        "inside",
        "unheard",
        "oops",
        "user:pw",
        "certificate",
        "slow",
        "boom",
    ):
        assert private not in text  # no key, no provider description, no body, no address


@pytest.mark.parametrize(
    "reply",
    [
        refused(500, "SERVER_ERROR"),
        lambda _r: requests.ConnectionError("down"),
        lambda _r: requests.ReadTimeout("slow"),
    ],
)
async def test_a_failed_call_is_sent_once_the_sdk_retries_are_off(reply: Any) -> None:
    rzp = Razorpay(reply)
    with pytest.raises(PaymentProviderError):
        await rzp.gateway.create_order(amount_minor=49_900, currency="INR", receipt="r", notes={})
    assert len(rzp.requests) == 1  # a retried order could be created twice at Razorpay


async def test_a_call_nobody_mocked_never_reaches_a_network(rzp: Razorpay) -> None:
    """The fake fails anything but ``POST /v1/orders``: proof the session has no socket behind it."""
    with pytest.raises(AssertionError, match="nobody mocked"):
        await asyncio.to_thread(rzp.gateway._client.order.fetch, "order_x")


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


@pytest.mark.parametrize("signature", [None, "", "   ", "0" * 64, "abc", "é" * 64, "日本語", "\ud800"])
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


def test_a_properly_signed_body_with_non_ascii_text_is_accepted(rzp: Razorpay) -> None:
    """The SDK compares signatures as text: only the signature must be ASCII, the body may be any UTF-8."""
    body = event("order.paid", payment=payment_entity(description="चाय का कप ☕"))
    raw, signature = signed(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    assert "चाय".encode() in raw
    assert rzp.gateway.parse_webhook(raw, signature).kind == "paid"


def test_a_non_ascii_body_with_the_wrong_signature_is_refused(rzp: Razorpay) -> None:
    raw = json.dumps(
        event("order.paid", payment=payment_entity(description="é☕")), ensure_ascii=False
    ).encode()
    for signature in ("0" * 64, "é" * 64, hmac_hex("guess", raw)):
        with pytest.raises(InvalidSignature):
            rzp.gateway.parse_webhook(raw, signature)


def test_a_body_that_is_not_utf8_is_invalid_even_with_a_matching_hmac(rzp: Razorpay) -> None:
    """The SDK checks text, so bytes that do not decode cannot be verified: refused rather than guessed at."""
    raw, signature = signed(b'{"event": "order.paid", "x": "\xff\xfe"}')
    with pytest.raises(InvalidSignature):
        rzp.gateway.parse_webhook(raw, signature)


# ───────────────────────── no webhook secret ─────────────────────────


def test_a_gateway_with_only_the_key_pair_says_its_webhooks_are_off(
    rzp: Razorpay, keys_only: Razorpay
) -> None:
    assert rzp.gateway.webhooks_enabled is True
    assert keys_only.gateway.webhooks_enabled is False


def test_an_empty_webhook_secret_counts_as_none_because_anyone_could_sign_with_it() -> None:
    gateway = RazorpayGateway(key_id=RZP_KEY_ID, key_secret=RZP_KEY_SECRET, webhook_secret="")
    assert gateway.webhooks_enabled is False
    raw, signature = signed(event("order.paid", payment=payment_entity()), secret="")
    with pytest.raises(InvalidSignature, match="not configured"):
        gateway.parse_webhook(raw, signature)


@pytest.mark.parametrize("secret", [RZP_WEBHOOK_SECRET, RZP_KEY_SECRET, "", "guess"])
def test_without_a_webhook_secret_every_webhook_is_refused_whatever_it_is_signed_with(
    keys_only: Razorpay, secret: str
) -> None:
    raw, signature = signed(event("order.paid", payment=payment_entity()), secret=secret)
    with pytest.raises(InvalidSignature, match="not configured"):
        keys_only.gateway.parse_webhook(raw, signature)


@pytest.mark.parametrize("signature", [None, "", "0" * 64, "é"])
def test_without_a_webhook_secret_a_missing_or_odd_signature_is_refused_the_same_way(
    keys_only: Razorpay, signature: str | None
) -> None:
    with pytest.raises(InvalidSignature):
        keys_only.gateway.parse_webhook(b"{}", signature)
    with pytest.raises(InvalidSignature):
        keys_only.gateway.parse_webhook(b"\xff", signature)
