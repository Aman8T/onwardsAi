"""The plans through the real app over a real SQLite file (roadmap B2): the entitlements view's new fields, Day One's
checkout and hand-off with its receipt, ``already_yours``, the orders list, "tell me when it opens", and `/api/me`'s
plan and matches left. Only Razorpay's network edge is faked."""

from __future__ import annotations

from typing import Any

from tayyari.storage.tables import PlanInterestRow
from tests.fakes.billing import PRICES, razorpay_handoff
from tests.integration.test_billing_routes import start  # noqa: F401 - the fixture

PLANS: dict[str, Any] = {"plans": {}, "billing": dict(PRICES)}
"""The plans as configured by default: Day One priced, three posting matches a month."""


async def test_the_entitlements_view_carries_the_plans(start: Any) -> None:  # noqa: F811
    async with start(**PLANS) as rig:
        body = (await rig.client.get("/api/billing/entitlements")).json()
    assert body["plan"] == "just_looking"
    assert body["usage"]["searches"] == {"limit": 5, "used": 0}
    assert body["usage"]["matches"] == {"limit": 3, "used": 0}
    assert body["resetsAt"].endswith("T00:00:00+00:00")
    assert body["offer"]["dayOneEarly"] == 99_900 and body["offer"]["notify"] == []
    assert body["prices"]["INR"]["dayone"] in (99_900, 999_900)  # the price of the moment


async def test_day_one_checkout_handoff_receipt_and_orders(start: Any) -> None:  # noqa: F811
    async with start(**PLANS) as rig:
        _, headers = await rig.user()
        signed_out = await rig.client.get("/api/billing/orders")
        checkout = await rig.checkout(headers, product="dayone", currency="INR")
        order_id = checkout.json()["orderId"]
        handoff = await rig.client.post(
            "/api/billing/verify", json=razorpay_handoff(order_id), headers=headers
        )
        again = await rig.checkout(headers, product="dayone", currency="INR")
        orders = await rig.client.get("/api/billing/orders", headers=headers)
        mine = await rig.me(headers)
    assert signed_out.status_code == 401
    assert checkout.status_code == 200 and checkout.json()["description"] == "Leafout: Day One"
    assert handoff.status_code == 200
    paid = handoff.json()
    assert paid["ok"] is True and paid["plan"] == "day_one"
    receipt = paid["receipt"]
    assert receipt["number"].startswith("LF-") and receipt["product"] == "dayone"
    assert (receipt["currency"], receipt["paymentId"]) == ("INR", "pay_9")
    assert again.status_code == 409 and again.json() == {"error": "Day One is already yours."}
    assert orders.status_code == 200 and orders.headers["cache-control"] == "no-store"
    assert orders.json() == {"orders": [receipt]}
    assert mine["plan"] == "day_one" and "matchesLeft" not in mine


async def test_me_says_just_looking_and_the_matches_left(start: Any) -> None:  # noqa: F811
    async with start(**PLANS) as rig:
        _, headers = await rig.user()
        mine = await rig.me(headers)
    assert (mine["plan"], mine["matchesLeft"]) == ("just_looking", 3)


async def test_notify_stores_the_wish_for_a_signed_in_account(start: Any) -> None:  # noqa: F811
    async with start(**PLANS) as rig:
        user, headers = await rig.user()
        signed_out = await rig.client.put("/api/billing/notify", json={"plan": "lowkey", "on": True})
        on = await rig.client.put("/api/billing/notify", json={"plan": "lowkey", "on": True}, headers=headers)
        bad = await rig.client.put(
            "/api/billing/notify", json={"plan": "day_one", "on": True}, headers=headers
        )
        view = (await rig.client.get("/api/billing/entitlements", headers=headers)).json()
        rows = await rig.rows(PlanInterestRow, PlanInterestRow.user_id == user.id)
        off = await rig.client.put(
            "/api/billing/notify", json={"plan": "lowkey", "on": False}, headers=headers
        )
    assert signed_out.status_code == 401
    assert on.status_code == 200 and on.json() == {"notify": ["lowkey"]}
    assert bad.status_code in (400, 422)
    assert view["offer"]["notify"] == ["lowkey"]
    assert [row.plan for row in rows] == ["lowkey"]
    assert off.json() == {"notify": []}
