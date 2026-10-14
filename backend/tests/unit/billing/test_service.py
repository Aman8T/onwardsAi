"""`BillingService` over fake repositories and the real gateways (their network edges faked): checkout for both
providers, the 409s, settling from a webhook or the browser hand-off exactly once, and the refusals."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from tayyari.billing.entitlements import EntitlementService
from tayyari.billing.razorpay import RazorpayGateway
from tayyari.billing.service import BillingService
from tayyari.billing.stripe_gw import StripeGateway
from tayyari.core.errors import BadRequest, Conflict, NotFound, ServiceUnavailable
from tayyari.core.settings import Settings
from tayyari.storage.ports import StoredUser
from tests.fakes.billing import (
    ALL_SECRETS,
    RAZORPAY_KEYS,
    RZP_KEY_ID,
    RZP_KEY_SECRET,
    RZP_WEBHOOK_SECRET,
    STRIPE_KEY,
    STRIPE_WEBHOOK_SECRET,
    Answer,
    FakeRazorpay,
    FakeStripeHttp,
    captured_events,
    hmac_hex,
    razorpay_event,
    razorpay_handoff,
    razorpay_paid,
    razorpay_payment,
    sign_razorpay,
    sign_stripe,
    stripe_event,
)
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

MakeSettings = Callable[..., Settings]
PUBLIC = "https://tayyari.example"


@dataclass
class Rig:
    service: BillingService
    fakes: FakeRepositories
    clock: FakeClock
    rzp: FakeRazorpay
    stripe_http: FakeStripeHttp
    user: StoredUser

    async def another_user(self, email: str = "raj@example.com") -> StoredUser:
        return await self.fakes.users.sign_in_email(email=email, now=self.clock.now())

    async def stored(self, provider_order_id: str):  # type: ignore[no-untyped-def]
        order = await self.fakes.billing.get_order_by_provider_id(provider_order_id)
        assert order is not None
        return order

    async def credits(self, user: StoredUser | None = None) -> int:
        return (await self.service.entitlements.summary((user or self.user).id)).credits


@pytest.fixture
async def rig(make_billing_settings: MakeSettings, fakes: FakeRepositories, clock: FakeClock) -> Rig:
    return await make_rig(make_billing_settings(auth={"public_url": PUBLIC}), fakes, clock)


async def make_rig(settings: Settings, fakes: FakeRepositories, clock: FakeClock) -> Rig:
    rzp, stripe_http = FakeRazorpay(), FakeStripeHttp()
    razorpay = (
        RazorpayGateway(
            key_id=RZP_KEY_ID,
            key_secret=RZP_KEY_SECRET,
            webhook_secret=RZP_WEBHOOK_SECRET if settings.razorpay_webhooks_enabled else None,
            session=rzp,
        )
        if settings.razorpay_enabled
        else None
    )
    stripe = (
        StripeGateway(secret_key=STRIPE_KEY, webhook_secret=STRIPE_WEBHOOK_SECRET, http_client=stripe_http)
        if settings.stripe_enabled
        else None
    )
    entitlements = EntitlementService(fakes.billing, settings.billing, clock)
    service = BillingService(
        settings=settings,
        repo=fakes.billing,
        entitlements=entitlements,
        clock=clock,
        razorpay=razorpay,
        stripe=stripe,
    )
    user = await fakes.users.sign_in_email(email="ana@example.com", now=clock.now())
    return Rig(service, fakes, clock, rzp, stripe_http, user)


# ───────────────────────── checkout ─────────────────────────


async def test_an_inr_checkout_creates_a_razorpay_order_and_ours(rig: Rig) -> None:
    answer = await rig.service.checkout(rig.user, "pack3", "INR")

    assert answer.provider == "razorpay" and answer.url is None
    assert (answer.orderId, answer.amount, answer.currency) == ("order_Rzp0001", 49_900, "INR")
    assert (answer.keyId, answer.name, answer.description) == (
        RZP_KEY_ID,
        "Tayyari",
        "Tayyari: three postings",
    )
    assert answer.prefillEmail == "ana@example.com"

    order = await rig.stored("order_Rzp0001")
    assert (order.user_id, order.provider, order.product, order.currency) == (
        rig.user.id,
        "razorpay",
        "pack3",
        "INR",
    )
    assert (order.amount_minor, order.status, order.paid_at) == (49_900, "created", None)

    sent = rig.rzp.requests[0].json
    assert (sent["amount"], sent["currency"], sent["receipt"]) == (49_900, "INR", order.id)
    assert sent["notes"] == {"order": order.id, "user": rig.user.id, "product": "pack3"}
    assert "ana@example.com" not in rig.rzp.requests[0].body.decode()  # the address stays with us


async def test_a_usd_checkout_creates_a_stripe_session_that_returns_to_the_pricing_page(rig: Rig) -> None:
    answer = await rig.service.checkout(rig.user, "pass30", "USD")

    assert answer.provider == "stripe" and answer.orderId is None and answer.keyId is None
    assert answer.url == "https://checkout.stripe.com/c/pay/cs_test_0001"

    order = await rig.stored("cs_test_0001")
    assert (order.user_id, order.provider, order.product, order.currency) == (
        rig.user.id,
        "stripe",
        "pass30",
        "USD",
    )
    assert (order.amount_minor, order.status) == (1_900, "created")

    form = rig.stripe_http.requests[0]["form"]
    assert form["success_url"] == f"{PUBLIC}/pricing?checkout=success"
    assert form["cancel_url"] == f"{PUBLIC}/pricing?checkout=cancelled"
    assert form["client_reference_id"] == order.id and form["customer_email"] == "ana@example.com"
    assert form["line_items[0][price_data][unit_amount]"] == "1900"


@pytest.mark.parametrize(
    ("product", "currency", "unset"),
    [
        ("pack3", "INR", "pack3_inr"),
        ("pass30", "INR", "pass30_inr"),
        ("pack3", "USD", "pack3_usd"),
        ("pass30", "USD", "pass30_usd"),
    ],
)
async def test_a_plan_with_no_price_is_a_409_and_nothing_is_created(
    make_billing_settings: MakeSettings,
    fakes: FakeRepositories,
    clock: FakeClock,
    product: str,
    currency: str,
    unset: str,
) -> None:
    rig = await make_rig(make_billing_settings(**{unset: 0}), fakes, clock)
    with pytest.raises(Conflict) as refused:
        await rig.service.checkout(rig.user, product, currency)  # type: ignore[arg-type]
    assert (refused.value.status, refused.value.code) == (409, "not_offered")
    assert not rig.rzp.requests and not rig.stripe_http.requests and not fakes.billing.orders


@pytest.mark.parametrize(
    ("currency", "missing"),
    [
        ("INR", ["razorpay_key_id"]),
        ("INR", ["razorpay_key_secret"]),
        ("INR", ["razorpay_key_id", "razorpay_key_secret", "razorpay_webhook_secret"]),
        ("USD", ["stripe_secret_key"]),
        ("USD", ["stripe_webhook_secret"]),
    ],
)
async def test_a_provider_with_a_secret_missing_is_a_409_and_nothing_is_created(
    make_billing_settings: MakeSettings,
    fakes: FakeRepositories,
    clock: FakeClock,
    currency: str,
    missing: list[str],
) -> None:
    secrets = {k: v for k, v in ALL_SECRETS.items() if k not in missing}
    rig = await make_rig(make_billing_settings(secrets=secrets), fakes, clock)
    with pytest.raises(Conflict) as refused:
        await rig.service.checkout(rig.user, "pack3", currency)  # type: ignore[arg-type]
    assert refused.value.code == "not_offered"
    assert not rig.rzp.requests and not rig.stripe_http.requests and not fakes.billing.orders


async def test_razorpay_sells_on_the_key_pair_alone(
    make_billing_settings: MakeSettings, fakes: FakeRepositories, clock: FakeClock
) -> None:
    """No webhook secret (a laptop: nothing can reach it anyway): INR plans are on sale and checkout works."""
    rig = await make_rig(make_billing_settings(secrets=RAZORPAY_KEYS), fakes, clock)
    answer = await rig.service.checkout(rig.user, "pack3", "INR")
    assert (answer.provider, answer.orderId, answer.amount, answer.keyId) == (
        "razorpay",
        "order_Rzp0001",
        49_900,
        RZP_KEY_ID,
    )
    assert (await rig.stored("order_Rzp0001")).status == "created"
    view = await rig.service.entitlements_view(None)
    assert view.prices.model_dump(exclude_none=True) == {
        "INR": {"pack3": 49_900, "pass30": 99_900},
        "USD": {},
    }


@pytest.mark.parametrize(
    ("currency", "price_field", "below", "at_floor"),
    [("INR", "pack3_inr", 99, 100), ("USD", "pack3_usd", 49, 50)],
)
async def test_a_price_under_the_providers_minimum_is_a_409_not_a_503_from_the_provider(
    make_billing_settings: MakeSettings,
    fakes: FakeRepositories,
    clock: FakeClock,
    currency: str,
    price_field: str,
    below: int,
    at_floor: int,
) -> None:
    rig = await make_rig(make_billing_settings(**{price_field: below}), fakes, clock)
    with pytest.raises(Conflict) as refused:
        await rig.service.checkout(rig.user, "pack3", currency)  # type: ignore[arg-type]
    assert refused.value.code == "not_offered"
    assert not rig.rzp.requests and not rig.stripe_http.requests and not fakes.billing.orders
    assert (
        "pack3"
        not in (await rig.service.entitlements_view(None)).prices.model_dump(exclude_none=True)[currency]
    )

    rig = await make_rig(make_billing_settings(**{price_field: at_floor}), fakes, clock)
    answer = await rig.service.checkout(rig.user, "pack3", currency)  # type: ignore[arg-type]
    assert answer.provider == ("razorpay" if currency == "INR" else "stripe")


async def test_nothing_is_on_sale_by_default(
    make_settings: MakeSettings, fakes: FakeRepositories, clock: FakeClock
) -> None:
    rig = await make_rig(make_settings(), fakes, clock)
    for currency in ("INR", "USD"):
        with pytest.raises(Conflict):
            await rig.service.checkout(rig.user, "pack3", currency)  # type: ignore[arg-type]
    view = await rig.service.entitlements_view(rig.user)
    assert view.prices.model_dump(exclude_none=True) == {"INR": {}, "USD": {}}


async def test_a_provider_that_fails_is_a_503_and_leaves_no_order(
    make_billing_settings: MakeSettings, fakes: FakeRepositories, clock: FakeClock
) -> None:
    rig = await make_rig(make_billing_settings(), fakes, clock)
    rig.rzp.reply = lambda _request: Answer(500, {"error": {"code": "SERVER_ERROR", "description": "boom"}})
    rig.stripe_http.reply = lambda _url, _form: (500, {"error": {"message": "boom"}})
    for currency in ("INR", "USD"):
        with pytest.raises(ServiceUnavailable) as failed:
            await rig.service.checkout(rig.user, "pack3", currency)  # type: ignore[arg-type]
        assert failed.value.status == 503 and "boom" not in failed.value.user_message
    assert not fakes.billing.orders


async def test_a_rejected_key_is_a_503_for_the_customer_and_a_hint_in_the_log(
    make_billing_settings: MakeSettings,
    fakes: FakeRepositories,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = await make_rig(make_billing_settings(secrets=RAZORPAY_KEYS), fakes, clock)
    rig.rzp.reply = lambda _request: Answer(
        401, {"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}}
    )
    with captured_events(monkeypatch, "tayyari.billing.service") as logs:
        with pytest.raises(ServiceUnavailable) as failed:
            await rig.service.checkout(rig.user, "pack3", "INR")
    assert failed.value.status == 503 and "Authentication" not in failed.value.user_message
    (entry,) = [e for e in logs if e["event"] == "checkout_provider_failed"]
    assert "the API key was rejected" in entry["reason"]
    assert "Authentication failed" not in entry["reason"] and RZP_KEY_SECRET not in str(logs)
    assert not fakes.billing.orders


# ───────────────────────── paying: Razorpay ─────────────────────────


async def razorpay_order(rig: Rig, product: str = "pack3", user: StoredUser | None = None) -> str:
    answer = await rig.service.checkout(user or rig.user, product, "INR")  # type: ignore[arg-type]
    assert answer.orderId is not None
    return answer.orderId


async def pay_razorpay(rig: Rig, order_id: str, **fields: Any) -> str:
    raw, signature = sign_razorpay(razorpay_paid(order_id, **fields))
    return await rig.service.webhook("razorpay", raw, signature)


async def test_a_razorpay_webhook_pays_the_order_and_grants_once_however_often_it_is_replayed(
    rig: Rig,
) -> None:
    order_id = await razorpay_order(rig)
    assert await rig.credits() == 0

    assert await pay_razorpay(rig, order_id) == "granted"
    for _ in range(3):
        assert (
            await pay_razorpay(rig, order_id) == "already_paid"
        )  # the provider retries; we never double-grant

    assert await rig.credits() == 3
    order = await rig.stored(order_id)
    assert (order.status, order.paid_at) == ("paid", rig.clock.now())
    assert order.raw == {
        "provider_order_id": order_id,
        "paid": {"payment_id": "pay_9", "event": "order.paid"},
    }
    assert rig.fakes.billing.ledger_sum(rig.user.id) == 3
    assert "ana@example.com" not in json.dumps(order.raw)


async def test_a_pass_order_extends_the_pass(rig: Rig) -> None:
    order_id = await razorpay_order(rig, "pass30")
    await pay_razorpay(rig, order_id, amount=99_900)
    summary = await rig.service.entitlements.summary(rig.user.id)
    assert summary.pass_until is not None and (summary.pass_until - rig.clock.now()).days == 30


async def test_a_payment_of_the_wrong_amount_or_currency_grants_nothing(rig: Rig) -> None:
    order_id = await razorpay_order(rig)
    assert await pay_razorpay(rig, order_id, amount=100) == "mismatch"
    assert await pay_razorpay(rig, order_id, currency="USD") == "mismatch"
    assert await rig.credits() == 0 and (await rig.stored(order_id)).status == "created"
    assert await pay_razorpay(rig, order_id) == "granted"  # the right payment still settles it


async def test_a_signed_event_for_an_order_we_do_not_know_is_accepted_and_ignored(rig: Rig) -> None:
    assert await pay_razorpay(rig, "order_Unknown") == "unknown_order"
    assert not rig.fakes.billing.ledger


async def test_an_order_cannot_be_paid_by_the_other_providers_event(rig: Rig) -> None:
    await rig.service.checkout(rig.user, "pack3", "USD")
    assert (
        await pay_razorpay(rig, "cs_test_0001") == "mismatch"
    )  # a Razorpay-signed event naming a Stripe order
    assert await rig.credits() == 0


async def test_a_failed_attempt_is_recorded_and_a_later_success_still_pays(rig: Rig) -> None:
    order_id = await razorpay_order(rig)
    failed = razorpay_event("payment.failed", payment=razorpay_payment(order_id=order_id, status="failed"))
    raw, signature = sign_razorpay(failed)
    assert await rig.service.webhook("razorpay", raw, signature) == "failed"
    assert (await rig.stored(order_id)).status == "failed" and await rig.credits() == 0

    assert await pay_razorpay(rig, order_id) == "granted"  # the customer retried on the same order
    assert await rig.credits() == 3
    assert await rig.service.webhook("razorpay", raw, signature) == "failed"
    assert (await rig.stored(order_id)).status == "paid"  # a late failure event never un-pays an order


async def test_events_we_do_not_act_on_are_accepted(rig: Rig) -> None:
    raw, signature = sign_razorpay(razorpay_event("refund.processed", payment=razorpay_payment()))
    assert await rig.service.webhook("razorpay", raw, signature) == "ignored"


async def test_a_forged_razorpay_webhook_changes_nothing(rig: Rig) -> None:
    order_id = await razorpay_order(rig)
    raw = razorpay_paid(order_id)
    for signature in (None, "", "0" * 64, hmac_hex("guess", raw), hmac_hex(RZP_KEY_SECRET, raw)):
        with pytest.raises(BadRequest) as refused:
            await rig.service.webhook("razorpay", raw, signature)
        assert (refused.value.status, refused.value.code) == (400, "invalid_signature")
    assert await rig.credits() == 0 and (await rig.stored(order_id)).status == "created"


# ───────────────────────── paying: Stripe ─────────────────────────


async def stripe_session(rig: Rig, product: str = "pack3", user: StoredUser | None = None) -> str:
    await rig.service.checkout(user or rig.user, product, "USD")  # type: ignore[arg-type]
    return rig.stripe_http.last_session_id


async def pay_stripe(
    rig: Rig, session_id: str, name: str = "checkout.session.completed", **fields: Any
) -> str:
    raw = stripe_event(name, session_id, **fields)
    return await rig.service.webhook("stripe", raw, sign_stripe(raw))


async def test_a_stripe_webhook_pays_the_order_and_grants_once_however_often_it_is_replayed(rig: Rig) -> None:
    session_id = await stripe_session(rig)
    assert await pay_stripe(rig, session_id) == "granted"
    assert await pay_stripe(rig, session_id) == "already_paid"
    assert await pay_stripe(rig, session_id, "checkout.session.async_payment_succeeded") == "already_paid"
    assert await rig.credits() == 3
    order = await rig.stored(session_id)
    assert order.status == "paid" and order.raw == {
        "provider_order_id": session_id,
        "paid": {"payment_id": "pi_77", "event": "checkout.session.completed"},
    }


async def test_a_stripe_session_still_waiting_for_the_bank_grants_nothing_until_it_succeeds(rig: Rig) -> None:
    session_id = await stripe_session(rig, "pass30")
    assert await pay_stripe(rig, session_id, payment_status="unpaid", amount_total=1_900) == "ignored"
    assert (
        await rig.credits() == 0 and (await rig.service.entitlements.summary(rig.user.id)).pass_until is None
    )
    assert (
        await pay_stripe(rig, session_id, "checkout.session.async_payment_succeeded", amount_total=1_900)
        == "granted"
    )
    assert (await rig.service.entitlements.summary(rig.user.id)).pass_until is not None


async def test_a_stripe_payment_of_the_wrong_amount_grants_nothing(rig: Rig) -> None:
    session_id = await stripe_session(rig)
    assert await pay_stripe(rig, session_id, amount_total=1) == "mismatch"
    assert await pay_stripe(rig, session_id, currency="inr") == "mismatch"
    assert await rig.credits() == 0


async def test_an_expired_session_is_marked_failed(rig: Rig) -> None:
    session_id = await stripe_session(rig)
    assert await pay_stripe(rig, session_id, "checkout.session.expired", payment_status="unpaid") == "failed"
    assert (await rig.stored(session_id)).status == "failed"


async def test_a_forged_stripe_webhook_changes_nothing(rig: Rig) -> None:
    session_id = await stripe_session(rig)
    raw = stripe_event("checkout.session.completed", session_id)
    for header in (None, "", "garbage", sign_stripe(raw, secret="whsec_guess"), sign_stripe(raw + b" ")):
        with pytest.raises(BadRequest) as refused:
            await rig.service.webhook("stripe", raw, header)
        assert (refused.value.status, refused.value.code) == (400, "invalid_signature")
    assert await rig.credits() == 0 and (await rig.stored(session_id)).status == "created"


async def test_a_webhook_for_a_provider_that_is_not_configured_is_a_503(
    make_billing_settings: MakeSettings, fakes: FakeRepositories, clock: FakeClock
) -> None:
    rig = await make_rig(
        make_billing_settings(secrets={k: v for k, v in ALL_SECRETS.items() if k.startswith("razorpay")}),
        fakes,
        clock,
    )
    raw = stripe_event("checkout.session.completed")
    with pytest.raises(ServiceUnavailable) as refused:
        await rig.service.webhook("stripe", raw, sign_stripe(raw))
    assert refused.value.status == 503


async def test_a_razorpay_webhook_without_a_webhook_secret_is_a_503_and_changes_nothing(
    make_billing_settings: MakeSettings,
    fakes: FakeRepositories,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key pair sells and settles through the hand-off; a webhook has nothing to be checked against."""
    rig = await make_rig(make_billing_settings(secrets=RAZORPAY_KEYS), fakes, clock)
    order_id = await razorpay_order(rig)
    raw, signature = sign_razorpay(razorpay_paid(order_id))  # signed with the secret the server does not have
    with captured_events(monkeypatch, "tayyari.billing.service") as logs:
        for header in (signature, None, "", "0" * 64):
            with pytest.raises(ServiceUnavailable) as refused:
                await rig.service.webhook("razorpay", raw, header)
            assert (refused.value.status, refused.value.code) == (503, "webhooks_not_configured")
    assert {e["event"] for e in logs} == {"webhook_without_secret"}
    assert await rig.credits() == 0 and (await rig.stored(order_id)).status == "created"


async def test_with_the_webhook_secret_set_a_bad_signature_is_a_400_and_a_good_one_settles(
    make_billing_settings: MakeSettings, fakes: FakeRepositories, clock: FakeClock
) -> None:
    rig = await make_rig(make_billing_settings(), fakes, clock)
    order_id = await razorpay_order(rig)
    raw, signature = sign_razorpay(razorpay_paid(order_id))
    with pytest.raises(BadRequest) as refused:
        await rig.service.webhook("razorpay", raw, "0" * 64)
    assert (refused.value.status, refused.value.code) == (400, "invalid_signature")
    assert await rig.service.webhook("razorpay", raw, signature) == "granted"
    assert await rig.credits() == 3


# ───────────────────────── the browser hand-off ─────────────────────────


async def test_the_handoff_settles_the_order_and_returns_the_balance(rig: Rig) -> None:
    order_id = await razorpay_order(rig)
    answer = await rig.service.verify_razorpay(
        rig.user,
        order_id=order_id,
        payment_id="pay_9",
        signature=razorpay_handoff(order_id)["razorpay_signature"],
    )
    assert (answer.ok, answer.credits, answer.passUntil, answer.freeMatchAvailable) == (True, 3, None, True)
    assert (await rig.stored(order_id)).status == "paid"


async def test_the_handoff_and_the_webhook_in_either_order_grant_once(rig: Rig) -> None:
    first, second = await razorpay_order(rig), await razorpay_order(rig)
    handoff = razorpay_handoff(first)
    await rig.service.verify_razorpay(
        rig.user, order_id=first, payment_id="pay_9", signature=handoff["razorpay_signature"]
    )
    assert await pay_razorpay(rig, first) == "already_paid"

    assert await pay_razorpay(rig, second) == "granted"
    again = razorpay_handoff(second)
    answer = await rig.service.verify_razorpay(
        rig.user, order_id=second, payment_id="pay_9", signature=again["razorpay_signature"]
    )
    assert answer.ok is True and answer.credits == 6  # two orders of three, each paid once


@pytest.mark.parametrize("tamper", ["order", "payment", "signature"])
async def test_a_tampered_handoff_is_a_400_and_grants_nothing(rig: Rig, tamper: str) -> None:
    order_id = await razorpay_order(rig)
    values = razorpay_handoff(order_id)
    sent = {
        "order_id": "order_Rzp9999" if tamper == "order" else order_id,
        "payment_id": "pay_other" if tamper == "payment" else "pay_9",
        "signature": "0" * 64 if tamper == "signature" else values["razorpay_signature"],
    }
    with pytest.raises(BadRequest) as refused:
        await rig.service.verify_razorpay(rig.user, **sent)
    assert refused.value.code == "invalid_signature"
    assert await rig.credits() == 0 and (await rig.stored(order_id)).status == "created"


async def test_someone_elses_order_cannot_be_claimed_through_the_handoff(rig: Rig) -> None:
    raj = await rig.another_user()
    order_id = await razorpay_order(rig, user=raj)
    with pytest.raises(NotFound):
        await rig.service.verify_razorpay(
            rig.user,
            order_id=order_id,
            payment_id="pay_9",
            signature=razorpay_handoff(order_id)["razorpay_signature"],
        )
    assert await rig.credits(raj) == 0 and await rig.credits() == 0
    assert (await rig.stored(order_id)).status == "created"


async def test_a_signed_handoff_for_an_order_we_never_made_is_a_404(rig: Rig) -> None:
    with pytest.raises(NotFound):
        await rig.service.verify_razorpay(
            rig.user,
            order_id="order_Nope",
            payment_id="pay_9",
            signature=razorpay_handoff("order_Nope")["razorpay_signature"],
        )


async def test_the_handoff_grants_with_the_key_pair_alone(
    make_billing_settings: MakeSettings, fakes: FakeRepositories, clock: FakeClock
) -> None:
    """Razorpay's signature under the key secret is the proof; no webhook is needed for the balance to appear."""
    rig = await make_rig(make_billing_settings(secrets=RAZORPAY_KEYS), fakes, clock)
    order_id = await razorpay_order(rig, "pass30")
    answer = await rig.service.verify_razorpay(
        rig.user,
        order_id=order_id,
        payment_id="pay_9",
        signature=razorpay_handoff(order_id)["razorpay_signature"],
    )
    assert answer.ok is True and answer.passUntil is not None
    order = await rig.stored(order_id)
    assert (order.status, order.raw["paid"]) == ("paid", {"payment_id": "pay_9", "event": "checkout.handoff"})
    again = await rig.service.verify_razorpay(
        rig.user,
        order_id=order_id,
        payment_id="pay_9",
        signature=razorpay_handoff(order_id)["razorpay_signature"],
    )
    assert again.ok is True  # a reload or a double click changes nothing
    assert await rig.credits() == 0 and rig.fakes.billing.ledger_sum(rig.user.id) == 0


async def test_a_handoff_signed_with_the_webhook_secret_is_refused_on_the_key_pair_alone(
    make_billing_settings: MakeSettings, fakes: FakeRepositories, clock: FakeClock
) -> None:
    rig = await make_rig(make_billing_settings(secrets=RAZORPAY_KEYS), fakes, clock)
    order_id = await razorpay_order(rig)
    forged = hmac_hex(RZP_WEBHOOK_SECRET, f"{order_id}|pay_9".encode())
    with pytest.raises(BadRequest):
        await rig.service.verify_razorpay(rig.user, order_id=order_id, payment_id="pay_9", signature=forged)
    assert await rig.credits() == 0


@pytest.mark.parametrize("missing", [["razorpay_key_id"], ["razorpay_key_secret"], list(ALL_SECRETS)])
async def test_the_handoff_needs_razorpay_to_be_configured(
    make_billing_settings: MakeSettings,
    fakes: FakeRepositories,
    clock: FakeClock,
    missing: list[str],
) -> None:
    secrets = {k: v for k, v in ALL_SECRETS.items() if k not in missing}
    rig = await make_rig(make_billing_settings(secrets=secrets), fakes, clock)
    with pytest.raises(Conflict):
        await rig.service.verify_razorpay(rig.user, order_id="o", payment_id="p", signature="s")


# ───────────────────────── what the pricing page asks ─────────────────────────


async def test_an_anonymous_visitor_sees_prices_and_no_balance(rig: Rig) -> None:
    view = await rig.service.entitlements_view(None, accept_language="en-IN,en;q=0.9")
    assert (view.credits, view.passUntil, view.freeMatchAvailable, view.currency) == (0, None, False, "INR")
    assert view.prices.model_dump(exclude_none=True) == {
        "INR": {"pack3": 49_900, "pass30": 99_900},
        "USD": {"pack3": 900, "pass30": 1_900},
    }
    assert (await rig.service.entitlements_view(None)).currency == "USD"
    assert (await rig.service.entitlements_view(None, timezone="Asia/Kolkata")).currency == "INR"


async def test_a_signed_in_visitor_sees_the_balance_and_their_saved_currency(rig: Rig) -> None:
    await rig.fakes.users.update(rig.user.id, currency="USD")
    user = await rig.fakes.users.get(rig.user.id)
    assert user is not None
    await pay_razorpay(rig, await razorpay_order(rig))
    view = await rig.service.entitlements_view(user, accept_language="en-IN", timezone="Asia/Kolkata")
    assert (view.credits, view.freeMatchAvailable, view.currency) == (3, True, "USD")
