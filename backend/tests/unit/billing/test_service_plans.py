"""`BillingService` and the plans (roadmap B2): Day One's checkout at the price of the moment, ``already_yours``, the
hand-off's plan and receipt, the receipt number, the orders list, "tell me when it opens" and the entitlements view's
new fields. The providers' network edges are faked as in test_service.py."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tayyari.billing.service import receipt_number, receipt_of
from tayyari.core.errors import Conflict
from tayyari.core.settings import Settings
from tayyari.storage.ports import StoredOrder
from tests.fakes.billing import razorpay_handoff
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories
from tests.unit.billing.test_service import PUBLIC, Rig, make_rig, pay_razorpay

MakeSettings = Callable[..., Settings]
ENDS = datetime.fromisoformat("2026-12-15T23:59:00+05:30")


@pytest.fixture
async def rig(make_billing_settings: MakeSettings, fakes: FakeRepositories, clock: FakeClock) -> Rig:
    """Day One priced as the defaults (₹999 early, ₹9,999 after), Razorpay and Stripe configured."""
    settings = make_billing_settings(auth={"public_url": PUBLIC}, plans={})
    return await make_rig(settings, fakes, clock)


async def buy_day_one(rig: Rig) -> str:
    answer = await rig.service.checkout(rig.user, "dayone", "INR")
    assert answer.orderId is not None
    return answer.orderId


async def verify(rig: Rig, order_id: str) -> Any:
    return await rig.service.verify_razorpay(
        rig.user,
        order_id=order_id,
        payment_id="pay_9",
        signature=razorpay_handoff(order_id)["razorpay_signature"],
    )


# ───────────────────────── checkout ─────────────────────────


async def test_day_one_is_sold_at_the_early_price_before_the_end_moment(rig: Rig) -> None:
    answer = await rig.service.checkout(rig.user, "dayone", "INR")
    assert (answer.provider, answer.amount, answer.description) == ("razorpay", 99_900, "Leafout: Day One")
    order = await rig.stored(answer.orderId or "")
    assert (order.product, order.amount_minor) == ("dayone", 99_900)
    assert rig.rzp.requests[0].json["amount"] == 99_900


async def test_day_one_is_sold_at_the_after_price_from_the_end_moment(rig: Rig) -> None:
    rig.clock.advance((ENDS - rig.clock.now()).total_seconds())
    answer = await rig.service.checkout(rig.user, "dayone", "INR")
    assert answer.amount == 999_900
    assert (await rig.stored(answer.orderId or "")).amount_minor == 999_900


async def test_day_one_is_not_sold_in_dollars_while_it_has_no_dollar_price(rig: Rig) -> None:
    with pytest.raises(Conflict) as refused:
        await rig.service.checkout(rig.user, "dayone", "USD")
    assert refused.value.code == "not_offered"
    assert not rig.stripe_http.requests


async def test_day_one_is_a_409_already_yours_on_an_account_that_has_it(rig: Rig) -> None:
    order_id = await buy_day_one(rig)
    await verify(rig, order_id)
    before = len(rig.rzp.requests)
    with pytest.raises(Conflict) as refused:
        await rig.service.checkout(rig.user, "dayone", "INR")
    assert (refused.value.status, refused.value.code) == (409, "already_yours")
    assert len(rig.rzp.requests) == before  # no order was made at Razorpay


# ───────────────────────── paid ─────────────────────────


async def test_the_handoff_answers_with_day_one_and_the_receipt(rig: Rig) -> None:
    order_id = await buy_day_one(rig)
    answer = await verify(rig, order_id)
    order = await rig.stored(order_id)
    assert answer.ok is True and answer.plan == "day_one"
    assert answer.receipt is not None
    assert answer.receipt.model_dump(exclude_none=True) == {
        "number": f"LF-261007-{order.id[:6].upper()}",
        "product": "dayone",
        "amount": 99_900,
        "currency": "INR",
        "paidAt": "2026-10-07T12:00:00+00:00",
        "paymentId": "pay_9",
    }
    summary = await rig.service.entitlements.summary(rig.user.id)
    assert (summary.plan, summary.matches_left) == ("day_one", None)


async def test_a_handoff_after_the_webhook_still_carries_the_receipt(rig: Rig) -> None:
    order_id = await buy_day_one(rig)
    assert await pay_razorpay(rig, order_id, amount=99_900, payment_id="pay_hook") == "granted"
    answer = await verify(rig, order_id)
    assert answer.ok and answer.receipt is not None and answer.receipt.paymentId == "pay_hook"


async def test_an_older_product_leaves_the_account_on_just_looking(rig: Rig) -> None:
    answer = await rig.service.checkout(rig.user, "pack3", "INR")  # priced in this rig, as in the older tests
    paid = await verify(rig, answer.orderId or "")
    assert paid.plan == "just_looking" and paid.receipt is not None and paid.receipt.product == "pack3"


def test_the_receipt_number_is_the_day_paid_and_the_order_id() -> None:
    order = StoredOrder(
        id="ab12cdef99",
        user_id="u",
        provider="razorpay",
        provider_order_id="order_x",
        product="dayone",
        currency="INR",
        amount_minor=99_900,
        status="paid",
        created_at=datetime(2026, 11, 30, tzinfo=UTC),
        # 1 Dec 01:00 in India is 30 Nov in UTC: the receipt is dated in UTC
        paid_at=datetime.fromisoformat("2026-12-01T01:00:00+05:30"),
    )
    assert receipt_number(order) == "LF-261130-AB12CD"
    receipt = receipt_of(order)
    assert receipt is not None and receipt.paymentId is None
    assert receipt.paidAt == "2026-12-01T01:00:00+05:30"


def test_an_order_that_is_not_paid_has_no_receipt() -> None:
    base: dict[str, Any] = {
        "id": "o1",
        "user_id": "u",
        "provider": "razorpay",
        "provider_order_id": "order_x",
        "product": "dayone",
        "currency": "INR",
        "amount_minor": 99_900,
        "created_at": datetime(2026, 11, 30, tzinfo=UTC),
    }
    assert receipt_of(StoredOrder(**base, status="created")) is None
    assert receipt_of(StoredOrder(**base, status="failed")) is None
    paid = datetime(2026, 11, 30, tzinfo=UTC)
    assert receipt_of(StoredOrder(**{**base, "product": "annual"}, status="paid", paid_at=paid)) is None
    assert receipt_of(StoredOrder(**{**base, "currency": "EUR"}, status="paid", paid_at=paid)) is None


async def test_orders_lists_the_paid_ones_newest_first(rig: Rig) -> None:
    assert (await rig.service.orders(rig.user)).orders == []
    unpaid = await buy_day_one(rig)
    first = await buy_day_one(rig)
    await verify(rig, first)
    rig.clock.advance(timedelta(days=1).total_seconds())
    later = (await rig.service.checkout(rig.user, "pack3", "INR")).orderId or ""
    await verify(rig, later)
    listed = (await rig.service.orders(rig.user)).orders
    assert [r.product for r in listed] == ["pack3", "dayone"]
    assert unpaid not in {r.number for r in listed}
    other = await rig.another_user()
    assert (await rig.service.orders(other)).orders == []


# ───────────────────────── tell me when it opens ─────────────────────────


async def test_notify_stores_the_wish_and_answers_with_every_wish(rig: Rig) -> None:
    on = await rig.service.notify(rig.user, "locked_in", on=True)
    both = await rig.service.notify(rig.user, "lowkey", on=True)
    off = await rig.service.notify(rig.user, "locked_in", on=False)
    assert (on.notify, both.notify, off.notify) == (["locked_in"], ["lowkey", "locked_in"], ["lowkey"])
    view = await rig.service.entitlements_view(rig.user)
    assert view.offer.notify == ["lowkey"]
    assert (await rig.service.entitlements_view(None)).offer.notify == []


# ───────────────────────── the entitlements view ─────────────────────────


async def test_the_view_carries_the_plan_the_counts_and_the_offer(rig: Rig) -> None:
    await rig.service.plans.use(rig.user.id, "searches", "run-1")
    view = (await rig.service.entitlements_view(rig.user)).to_dict()
    assert view["plan"] == "just_looking"
    assert view["usage"]["searches"] == {"limit": 5, "used": 1}
    assert view["usage"]["matches"] == {"limit": 3, "used": 0}
    assert view["usage"]["applications"] == {"limit": 5, "used": 0}
    assert view["resetsAt"] == "2026-11-01T00:00:00+00:00"
    assert view["prices"]["INR"] == {"pack3": 49_900, "pass30": 99_900, "dayone": 99_900}
    assert view["offer"] == {
        "lowkey": 39_900,
        "lockedIn": 129_900,
        "dayOne": 99_900,
        "dayOneEarly": 99_900,
        "dayOneAfter": 999_900,
        "dayOneEndsAt": "2026-12-15T23:59:00+05:30",
        "early": True,
        "refundDays": 7,
        "notify": [],
    }


async def test_after_the_end_moment_the_offer_is_the_after_price(rig: Rig) -> None:
    rig.clock.advance((ENDS - rig.clock.now()).total_seconds())
    offer = (await rig.service.entitlements_view(None)).offer
    assert (offer.dayOne, offer.early) == (999_900, False)


async def test_without_razorpay_the_offer_has_no_price_to_pay(
    make_billing_settings: MakeSettings, fakes: FakeRepositories, clock: FakeClock
) -> None:
    rig = await make_rig(make_billing_settings(secrets=False, plans={}), fakes, clock)
    view = (await rig.service.entitlements_view(None)).to_dict()
    assert "dayOne" not in view["offer"] and view["offer"]["dayOneEarly"] == 99_900
    assert view["usage"]["coach"] == {"limit": 30, "used": 0}
    assert view["plan"] == "just_looking"
