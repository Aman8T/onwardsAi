"""Payments end to end through the real app over a real SQLite file: the pricing endpoint, checkout for both providers,
the signed webhooks (and what a forged one cannot do), the browser hand-off, `/api/me`'s entitlements, the cross-site
guard's exemption for webhooks, and what deleting an account leaves behind. Only the providers' network edges are faked,
and a request nobody mocked fails the test."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import requests
from sqlalchemy import func, select

from tayyari.core.errors import PaymentRequired
from tayyari.core.settings import Settings
from tayyari.storage.ports import StoredUser
from tayyari.storage.tables import CreditLedgerRow, EntitlementRow, OrderRow
from tests.fakes.billing import (
    ALL_SECRETS,
    PRICES,
    RAZORPAY_KEYS,
    Answer,
    FakeRazorpay,
    FakeStripeHttp,
    razorpay_event,
    razorpay_handoff,
    razorpay_paid,
    razorpay_payment,
    sign_razorpay,
    sign_stripe,
    stripe_event,
)
from tests.integration.support import Env, running

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
PUBLIC = "https://tayyari.example"
RAZORPAY_HOOK = "/api/billing/webhooks/razorpay"
STRIPE_HOOK = "/api/billing/webhooks/stripe"
JSON_HEADERS = {"content-type": "application/json"}


@dataclass
class Rig:
    env: Env
    rzp: FakeRazorpay
    stripe_http: FakeStripeHttp

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    async def user(self, email: str = "ana@example.com") -> tuple[StoredUser, dict[str, str]]:
        """An account and the cookie header that signs a request in as it."""
        container = self.env.container
        user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
        token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        return user, {"cookie": f"{SESSION}={token}"}

    async def me(self, headers: dict[str, str]) -> dict[str, Any]:
        response = await self.client.get("/api/me", headers=headers)
        assert response.status_code == 200
        return response.json()["entitlements"]  # type: ignore[no-any-return]

    async def checkout(self, headers: dict[str, str], **body: Any) -> httpx.Response:
        return await self.client.post("/api/billing/checkout", json=body, headers=headers)

    async def rows(self, table: Any, *where: Any) -> list[Any]:
        return await self.env.rows(table, *where)


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    """``async with start(secrets=True, **settings) as rig``: the app with prices set, fake providers behind it."""

    @asynccontextmanager
    async def run(
        *, secrets: bool | dict[str, str] = True, prices: bool = True, **overrides: Any
    ) -> AsyncIterator[Rig]:
        values: dict[str, Any] = {"auth": {"public_url": PUBLIC}}
        if prices:
            values["billing"] = dict(PRICES)
        if secrets is True:
            values.update(ALL_SECRETS)
        elif isinstance(secrets, dict):
            values.update(secrets)
        values.update(overrides)
        rzp, stripe_http = FakeRazorpay(), FakeStripeHttp()
        async with running(build_settings(**values), razorpay_session=rzp, stripe_http=stripe_http) as env:
            yield Rig(env, rzp, stripe_http)

    return run


# ───────────────────────── the pricing page's data ─────────────────────────


async def test_anyone_can_read_the_prices_and_nothing_is_cached(start: Any) -> None:
    async with start() as rig:
        response = await rig.client.get("/api/billing/entitlements")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "credits": 0,
        "freeMatchAvailable": False,
        "currency": "USD",
        "prices": {
            "INR": {"pack3": 49_900, "pass30": 99_900},
            "USD": {"pack3": 900, "pass30": 1_900},
        },
    }


async def test_prices_that_are_not_set_or_cannot_be_taken_are_left_out(start: Any) -> None:
    async with start(prices=False) as rig:
        unset = (await rig.client.get("/api/billing/entitlements")).json()["prices"]
    async with start(
        secrets={"stripe_secret_key": "sk", "stripe_webhook_secret": "wh"},
        billing={"pack3_inr": 100, "pack3_usd": 900},
    ) as rig:
        partly = (await rig.client.get("/api/billing/entitlements")).json()["prices"]
    assert unset == {"INR": {}, "USD": {}}
    assert partly == {"INR": {}, "USD": {"pack3": 900}}  # INR is priced but Razorpay has no secrets


async def test_razorpay_prices_are_offered_on_the_key_pair_alone(start: Any) -> None:
    async with start(secrets=RAZORPAY_KEYS) as rig:
        prices = (await rig.client.get("/api/billing/entitlements")).json()["prices"]
    assert prices == {"INR": {"pack3": 49_900, "pass30": 99_900}, "USD": {}}


@pytest.mark.parametrize("missing", ["razorpay_key_id", "razorpay_key_secret"])
async def test_razorpay_prices_are_left_out_when_one_half_of_the_key_is_missing(
    start: Any, missing: str
) -> None:
    async with start(secrets={k: v for k, v in ALL_SECRETS.items() if k != missing}) as rig:
        prices = (await rig.client.get("/api/billing/entitlements")).json()["prices"]
    assert prices["INR"] == {} and prices["USD"] == {"pack3": 900, "pass30": 1_900}


async def test_a_price_under_the_providers_minimum_is_left_out_not_offered_and_failing(start: Any) -> None:
    low = {"pack3_inr": 99, "pass30_inr": 100, "pack3_usd": 49, "pass30_usd": 50}
    async with start(billing=low) as rig:
        prices = (await rig.client.get("/api/billing/entitlements")).json()["prices"]
        _, headers = await rig.user()
        refused = [
            await rig.checkout(headers, product="pack3", currency="INR"),
            await rig.checkout(headers, product="pack3", currency="USD"),
        ]
        offered = [
            await rig.checkout(headers, product="pass30", currency="INR"),
            await rig.checkout(headers, product="pass30", currency="USD"),
        ]
        orders = await rig.rows(OrderRow)
    assert prices == {"INR": {"pass30": 100}, "USD": {"pass30": 50}}
    assert [r.status_code for r in refused] == [409, 409]  # not a 503 from the provider
    assert [r.status_code for r in offered] == [200, 200]
    assert [(o.product, o.amount_minor) for o in orders] == [("pass30", 100), ("pass30", 50)]
    assert [request.json["amount"] for request in rig.rzp.requests] == [100]  # the 99 was never sent


@pytest.mark.parametrize(
    ("headers", "query", "expected"),
    [
        ({}, "", "USD"),
        ({"accept-language": "en-IN,en;q=0.9"}, "", "INR"),
        ({"accept-language": "en-US"}, "?timezone=Asia/Kolkata", "INR"),
        ({"accept-language": "en-US"}, "?timezone=Asia/Calcutta", "INR"),
        ({"accept-language": "en-US"}, "?timezone=America/New_York", "USD"),
        ({}, "?timezone=" + "x" * 500, "USD"),
    ],
)
async def test_the_currency_to_show_first_follows_the_locale_and_time_zone(
    start: Any, headers: dict[str, str], query: str, expected: str
) -> None:
    async with start() as rig:
        response = await rig.client.get(f"/api/billing/entitlements{query}", headers=headers)
    assert response.status_code == 200 and response.json()["currency"] == expected


async def test_a_saved_currency_beats_every_guess_and_is_saved_by_the_account_route(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        patched = await rig.client.patch("/api/me", json={"currency": "USD"}, headers=headers)
        assert patched.status_code == 200 and patched.json()["user"]["currency"] == "USD"
        seen = await rig.client.get(
            "/api/billing/entitlements?timezone=Asia/Kolkata", headers={**headers, "accept-language": "en-IN"}
        )
    assert seen.json()["currency"] == "USD"


# ───────────────────────── /api/me ─────────────────────────


async def test_me_reports_the_entitlements_from_the_ledger(start: Any) -> None:
    async with start() as rig:
        user, headers = await rig.user()
        assert await rig.me(headers) == {"credits": 0, "freeMatchAvailable": True}  # no passUntil key at all

        used = await rig.env.container.entitlements.consume(user.id, "match-1")
        assert used.source == "free_monthly"
        assert await rig.me(headers) == {"credits": 0, "freeMatchAvailable": False}

        await rig.env.container.entitlements.grant(user.id, "pack3", "order-a")
        await rig.env.container.entitlements.grant(user.id, "pass30", "order-b")
        mine = await rig.me(headers)
        assert (mine["credits"], mine["freeMatchAvailable"]) == (3, False)
        assert mine["passUntil"] > "2026"  # an ISO timestamp


async def test_an_anonymous_visitor_has_no_entitlements_to_report(start: Any) -> None:
    async with start() as rig:
        body = (await rig.client.get("/api/me")).json()
    assert body["entitlements"] == {"credits": 0, "freeMatchAvailable": False}


async def test_a_use_with_nothing_left_is_a_402_whose_body_names_the_reason(start: Any) -> None:
    async with start() as rig:
        user, _ = await rig.user()
        entitlements = rig.env.container.entitlements
        await entitlements.consume(user.id, "free")
        with pytest.raises(PaymentRequired) as refused:
            await entitlements.consume(user.id, "second")
    assert refused.value.status == 402 and refused.value.to_body() == {
        "error": "payment_required",
        "reason": "no_credits",
    }


# ───────────────────────── checkout ─────────────────────────


async def test_checkout_needs_a_sign_in(start: Any) -> None:
    async with start() as rig:
        response = await rig.checkout({}, product="pack3", currency="INR")
    assert response.status_code == 401
    assert not rig.rzp.requests and not rig.stripe_http.requests


async def test_a_razorpay_checkout_returns_what_checkout_js_needs_and_stores_the_order(start: Any) -> None:
    async with start() as rig:
        user, headers = await rig.user()
        response = await rig.checkout(headers, product="pack3", currency="INR")
        orders = await rig.rows(OrderRow)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "provider": "razorpay",
        "orderId": "order_Rzp0001",
        "amount": 49_900,
        "currency": "INR",
        "keyId": ALL_SECRETS["razorpay_key_id"],
        "name": "Leafout",
        "description": "Leafout: three postings",
        "prefillEmail": "ana@example.com",
    }
    (order,) = orders
    assert (order.user_id, order.provider_order_id, order.status) == (user.id, "order_Rzp0001", "created")
    assert ALL_SECRETS["razorpay_key_secret"] not in response.text  # only the key id is public


async def test_a_stripe_checkout_returns_the_payment_page(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        response = await rig.checkout(headers, product="pass30", currency="USD")
        orders = await rig.rows(OrderRow)
    assert response.status_code == 200
    assert response.json() == {"provider": "stripe", "url": "https://checkout.stripe.com/c/pay/cs_test_0001"}
    assert [(o.provider, o.product, o.amount_minor, o.currency) for o in orders] == [
        ("stripe", "pass30", 1_900, "USD")
    ]


async def test_the_currency_falls_back_to_the_visitors_default(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        indian = await rig.checkout({**headers, "accept-language": "en-IN"}, product="pack3")
        elsewhere = await rig.checkout(headers, product="pack3", timezone="Europe/London")
        kolkata = await rig.checkout(headers, product="pack3", timezone="Asia/Kolkata")
    assert [r.json()["provider"] for r in (indian, elsewhere, kolkata)] == ["razorpay", "stripe", "razorpay"]


async def test_the_price_comes_from_the_server_never_from_the_request(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        response = await rig.checkout(
            headers, product="pack3", currency="INR", amount=1, price=1, amount_minor=1
        )
    assert response.json()["amount"] == 49_900
    assert rig.rzp.requests[0].json["amount"] == 49_900


async def test_checkout_is_a_409_for_a_plan_without_a_price(start: Any) -> None:
    async with start(prices=False) as rig:
        _, headers = await rig.user()
        responses = [
            await rig.checkout(headers, product=product, currency=currency)
            for product in ("pack3", "pass30")
            for currency in ("INR", "USD")
        ]
        orders = await rig.rows(OrderRow)
    assert [r.status_code for r in responses] == [409] * 4
    assert not orders and not rig.rzp.requests and not rig.stripe_http.requests


@pytest.mark.parametrize(
    ("kept", "blocked"),
    [("razorpay", "USD"), ("stripe", "INR"), (None, "INR"), (None, "USD")],
)
async def test_checkout_is_a_409_for_a_provider_without_its_secrets(
    start: Any, kept: str | None, blocked: str
) -> None:
    secrets = {k: v for k, v in ALL_SECRETS.items() if kept is not None and k.startswith(kept)}
    async with start(secrets=secrets) as rig:
        _, headers = await rig.user()
        response = await rig.checkout(headers, product="pack3", currency=blocked)
    assert response.status_code == 409
    assert not rig.rzp.requests and not rig.stripe_http.requests


@pytest.mark.parametrize("missing", ["razorpay_key_id", "razorpay_key_secret"])
async def test_checkout_is_a_409_when_razorpay_has_only_half_of_its_key(start: Any, missing: str) -> None:
    async with start(secrets={k: v for k, v in ALL_SECRETS.items() if k != missing}) as rig:
        _, headers = await rig.user()
        response = await rig.checkout(headers, product="pack3", currency="INR")
    assert response.status_code == 409 and not rig.rzp.requests


async def test_checkout_works_on_the_razorpay_key_pair_alone_and_the_secret_stays_private(start: Any) -> None:
    async with start(secrets=RAZORPAY_KEYS) as rig:
        _, headers = await rig.user()
        response = await rig.checkout(headers, product="pack3", currency="INR")
        stripe_response = await rig.checkout(headers, product="pack3", currency="USD")
    assert response.status_code == 200 and response.json()["orderId"] == "order_Rzp0001"
    assert response.json()["keyId"] == ALL_SECRETS["razorpay_key_id"]
    assert ALL_SECRETS["razorpay_key_secret"] not in response.text
    assert stripe_response.status_code == 409  # Stripe still wants both of its secrets
    (request,) = rig.rzp.requests
    assert request.basic_auth == f"{ALL_SECRETS['razorpay_key_id']}:{ALL_SECRETS['razorpay_key_secret']}"
    assert request.timeout == 10.0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"product": "annual"},
        {"product": "pack3", "currency": "EUR"},
        {"product": "pack3", "timezone": "x" * 65},
    ],
)
async def test_a_checkout_request_must_name_a_real_plan(start: Any, body: dict[str, Any]) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        response = await rig.checkout(headers, **body)
    assert response.status_code == 400


async def test_checkout_needs_a_json_body(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        response = await rig.client.post(
            "/api/billing/checkout",
            content='{"product": "pack3"}',
            headers={**headers, "content-type": "text/plain"},
        )
    assert response.status_code == 415


async def test_a_provider_outage_is_a_503_and_leaves_no_order(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        rig.rzp.reply = lambda _request: requests.ConnectionError("down")
        response = await rig.checkout(headers, product="pack3", currency="INR")
        orders = await rig.rows(OrderRow)
    assert response.status_code == 503 and not orders
    assert "down" not in response.text and len(rig.rzp.requests) == 1  # not retried


async def test_a_rejected_razorpay_key_is_a_503_that_does_not_echo_razorpay(start: Any) -> None:
    async with start(secrets=RAZORPAY_KEYS) as rig:
        _, headers = await rig.user()
        rig.rzp.reply = lambda _request: Answer(
            401, {"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}}
        )
        response = await rig.checkout(headers, product="pack3", currency="INR")
        orders = await rig.rows(OrderRow)
    assert response.status_code == 503 and not orders
    assert "Authentication" not in response.text


async def test_checkout_refuses_a_cross_site_request(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        response = await rig.checkout(
            {**headers, "sec-fetch-site": "cross-site", "origin": "https://evil.example"},
            product="pack3",
            currency="USD",
        )
    assert response.status_code == 403 and not rig.stripe_http.requests


# ───────────────────────── Razorpay: pay ─────────────────────────


async def razorpay_checkout(rig: Rig, headers: dict[str, str], product: str = "pack3") -> str:
    response = await rig.checkout(headers, product=product, currency="INR")
    assert response.status_code == 200, response.text
    return str(response.json()["orderId"])


async def post_razorpay(rig: Rig, raw: bytes, signature: str | None, **headers: str) -> httpx.Response:
    sent = {"content-type": "application/json", **headers}
    if signature is not None:
        sent["x-razorpay-signature"] = signature
    return await rig.client.post(RAZORPAY_HOOK, content=raw, headers=sent)


async def test_a_signed_razorpay_webhook_pays_the_order_and_replays_change_nothing(start: Any) -> None:
    async with start() as rig:
        user, headers = await rig.user()
        order_id = await razorpay_checkout(rig, headers)
        raw, signature = sign_razorpay(razorpay_paid(order_id))

        answers = [await post_razorpay(rig, raw, signature) for _ in range(3)]
        mine = await rig.me(headers)
        orders = await rig.rows(OrderRow)
        ledger = await rig.rows(CreditLedgerRow, CreditLedgerRow.user_id == user.id)

    assert [(a.status_code, a.json()) for a in answers] == [(200, {"ok": True})] * 3
    assert mine == {"credits": 3, "freeMatchAvailable": True}
    (order,) = orders
    assert (order.status, order.paid_at is not None, order.raw["paid"]) == (
        "paid",
        True,
        {"payment_id": "pay_9", "event": "order.paid"},
    )
    assert [(row.reason, row.delta, row.ref) for row in ledger] == [("purchase", 3, order.id)]
    assert "ana@example.com" not in json.dumps(order.raw)


async def test_a_pass_bought_through_razorpay_runs_thirty_days(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        order_id = await razorpay_checkout(rig, headers, "pass30")
        raw, signature = sign_razorpay(razorpay_paid(order_id, amount=99_900))
        assert (await post_razorpay(rig, raw, signature)).status_code == 200
        mine = await rig.me(headers)
    assert mine["credits"] == 0 and mine["passUntil"]


@pytest.mark.parametrize("signature", [None, "", "0" * 64, "x" * 1000])
async def test_a_razorpay_webhook_without_a_good_signature_is_a_400_and_changes_nothing(
    start: Any, signature: str | None
) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        order_id = await razorpay_checkout(rig, headers)
        refused = await post_razorpay(rig, razorpay_paid(order_id), signature)
        mine = await rig.me(headers)
        (order,) = await rig.rows(OrderRow)
    assert refused.status_code == 400
    assert mine["credits"] == 0 and order.status == "created"


async def test_a_razorpay_body_changed_after_signing_is_refused(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        order_id = await razorpay_checkout(rig, headers)
        raw, signature = sign_razorpay(razorpay_paid(order_id))
        refused = await post_razorpay(rig, raw.replace(b"49900", b"1"), signature)
        assert refused.status_code == 400 and (await rig.me(headers))["credits"] == 0


async def test_the_webhook_is_exempt_from_the_cross_site_guard_but_the_signature_still_decides(
    start: Any,
) -> None:
    cross_site = {"origin": "https://evil.example", "sec-fetch-site": "cross-site"}
    async with start() as rig:
        _, headers = await rig.user()
        order_id = await razorpay_checkout(rig, headers)
        raw, signature = sign_razorpay(razorpay_paid(order_id))
        forged = await post_razorpay(rig, raw, "0" * 64, **cross_site)
        genuine = await post_razorpay(rig, raw, signature, **cross_site)
        guarded = await rig.client.post("/api/billing/verify", json={}, headers={**headers, **cross_site})
    assert forged.status_code == 400  # not 403: it got past the guard and failed the signature
    assert genuine.status_code == 200
    assert guarded.status_code == 403  # the exemption is for the webhooks only


async def test_a_webhook_needs_no_content_type_and_no_cookie(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        order_id = await razorpay_checkout(rig, headers)
        raw, signature = sign_razorpay(razorpay_paid(order_id))
        response = await rig.client.post(
            RAZORPAY_HOOK,
            content=raw,
            headers={"x-razorpay-signature": signature, "content-type": "text/plain"},
        )
    assert response.status_code == 200 and "set-cookie" not in response.headers


async def test_a_signed_event_for_an_order_we_do_not_know_is_a_200(start: Any) -> None:
    async with start() as rig:
        raw, signature = sign_razorpay(razorpay_paid("order_NotOurs"))
        response = await post_razorpay(rig, raw, signature)
        assert response.status_code == 200 and not await rig.rows(CreditLedgerRow)


async def test_events_we_do_not_act_on_and_failed_payments_are_a_200(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        order_id = await razorpay_checkout(rig, headers)
        failed = razorpay_event(
            "payment.failed", payment=razorpay_payment(order_id=order_id, status="failed")
        )
        other = razorpay_event("refund.processed", payment=razorpay_payment(order_id=order_id))
        for body in (failed, other):
            raw, signature = sign_razorpay(body)
            assert (await post_razorpay(rig, raw, signature)).status_code == 200
        (order,) = await rig.rows(OrderRow)
        assert order.status == "failed" and (await rig.me(headers))["credits"] == 0
        raw, signature = sign_razorpay(razorpay_paid(order_id))  # the customer retried and paid
        await post_razorpay(rig, raw, signature)
        assert (await rig.me(headers))["credits"] == 3


async def test_an_oversized_webhook_body_is_refused_before_it_is_read(start: Any) -> None:
    async with start() as rig:
        response = await rig.client.post(
            RAZORPAY_HOOK, content=b"x" * (512 * 1024 + 1), headers={"x-razorpay-signature": "0" * 64}
        )
    assert response.status_code == 413


async def test_a_webhook_for_a_provider_that_is_not_configured_is_a_503(start: Any) -> None:
    async with start(secrets=False) as rig:
        raw, signature = sign_razorpay(razorpay_paid("order_x"))
        response = await post_razorpay(rig, raw, signature)
        stripe_raw = stripe_event("checkout.session.completed")
        stripe_response = await rig.client.post(
            STRIPE_HOOK, content=stripe_raw, headers={"stripe-signature": sign_stripe(stripe_raw)}
        )
    assert (response.status_code, stripe_response.status_code) == (503, 503)


@pytest.mark.parametrize("signature", [None, "", "0" * 64, "signed-with-a-secret-we-do-not-have"])
async def test_a_razorpay_webhook_is_a_503_when_only_the_key_pair_is_set(
    start: Any, signature: str | None
) -> None:
    """Nothing to check a signature against: not a 400 (Razorpay would read that as ours to fix), and no grant."""
    async with start(secrets=RAZORPAY_KEYS) as rig:
        _, headers = await rig.user()
        order_id = await razorpay_checkout(rig, headers)
        raw, good = sign_razorpay(razorpay_paid(order_id))
        refused = await post_razorpay(rig, raw, good if signature is None else signature)
        mine = await rig.me(headers)
        (order,) = await rig.rows(OrderRow)
    assert refused.status_code == 503 and "Webhooks are not set up" in refused.text
    assert mine["credits"] == 0 and order.status == "created"


async def test_the_webhook_secret_switches_the_razorpay_webhook_from_503_to_a_signature_check(
    start: Any,
) -> None:
    async with start(secrets=RAZORPAY_KEYS) as without:
        off = await post_razorpay(without, b"{}", "0" * 64)
    async with start() as with_secret:
        _, headers = await with_secret.user()
        order_id = await razorpay_checkout(with_secret, headers)
        raw, signature = sign_razorpay(razorpay_paid(order_id))
        bad = await post_razorpay(with_secret, raw, "0" * 64)
        good = await post_razorpay(with_secret, raw, signature)
        credits = (await with_secret.me(headers))["credits"]
    assert (off.status_code, bad.status_code, good.status_code) == (503, 400, 200)
    assert credits == 3


@pytest.mark.parametrize("body", [b"\xff\xfe not utf-8", "café ☕".encode()])
async def test_a_razorpay_webhook_body_with_odd_bytes_is_a_clean_400_without_a_good_signature(
    start: Any, body: bytes
) -> None:
    async with start() as rig:
        refused = await post_razorpay(rig, body, "0" * 64)
        non_ascii_signature = (
            await rig.client.post(  # a header byte above 0x7f, read as latin-1 by the server
                RAZORPAY_HOOK, content=body, headers={"x-razorpay-signature": "café".encode("latin-1")}
            )
        )
    assert (refused.status_code, non_ascii_signature.status_code) == (400, 400)


# ───────────────────────── Razorpay: the browser hand-off ─────────────────────────


async def test_the_handoff_grants_at_once_and_the_webhook_afterwards_changes_nothing(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        order_id = await razorpay_checkout(rig, headers)
        handoff = await rig.client.post(
            "/api/billing/verify", json=razorpay_handoff(order_id), headers=headers
        )
        raw, signature = sign_razorpay(razorpay_paid(order_id))
        webhook = await post_razorpay(rig, raw, signature)
        mine = await rig.me(headers)
    assert handoff.status_code == 200 and handoff.headers["cache-control"] == "no-store"
    assert handoff.json() == {"ok": True, "credits": 3, "freeMatchAvailable": True}
    assert webhook.status_code == 200 and mine["credits"] == 3


async def test_the_handoff_grants_at_once_on_the_key_pair_alone(start: Any) -> None:
    """A laptop with no webhook: the hand-off is the proof, and the balance appears when the customer returns."""
    async with start(secrets=RAZORPAY_KEYS) as rig:
        user, headers = await rig.user()
        order_id = await razorpay_checkout(rig, headers)
        handoff = await rig.client.post(
            "/api/billing/verify", json=razorpay_handoff(order_id), headers=headers
        )
        again = await rig.client.post("/api/billing/verify", json=razorpay_handoff(order_id), headers=headers)
        mine = await rig.me(headers)
        (order,) = await rig.rows(OrderRow)
        ledger = await rig.rows(CreditLedgerRow, CreditLedgerRow.user_id == user.id)
    assert handoff.status_code == 200 and handoff.json() == {
        "ok": True,
        "credits": 3,
        "freeMatchAvailable": True,
    }
    assert again.status_code == 200 and again.json()["credits"] == 3  # a reload does not pay twice
    assert mine["credits"] == 3
    assert (order.status, order.raw["paid"]) == ("paid", {"payment_id": "pay_9", "event": "checkout.handoff"})
    assert [(row.reason, row.delta) for row in ledger] == [("purchase", 3)]


async def test_a_handoff_signed_with_anything_but_the_key_secret_is_a_400_on_the_key_pair_alone(
    start: Any,
) -> None:
    async with start(secrets=RAZORPAY_KEYS) as rig:
        _, headers = await rig.user()
        order_id = await razorpay_checkout(rig, headers)
        forged = {
            **razorpay_handoff(order_id),
            "razorpay_signature": sign_razorpay(f"{order_id}|pay_9".encode())[1],  # the webhook secret's HMAC
        }
        refused = await rig.client.post("/api/billing/verify", json=forged, headers=headers)
        odd = await rig.client.post(
            "/api/billing/verify",
            json={**razorpay_handoff(order_id), "razorpay_signature": "café"},
            headers=headers,
        )
        mine = await rig.me(headers)
    assert (refused.status_code, odd.status_code) == (400, 400) and mine["credits"] == 0


async def test_a_tampered_handoff_is_a_400(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        order_id = await razorpay_checkout(rig, headers)
        sent = {**razorpay_handoff(order_id), "razorpay_signature": "0" * 64}
        refused = await rig.client.post("/api/billing/verify", json=sent, headers=headers)
        assert refused.status_code == 400 and (await rig.me(headers))["credits"] == 0


async def test_the_handoff_needs_a_sign_in_and_the_signed_in_account_must_own_the_order(start: Any) -> None:
    async with start() as rig:
        _, ana = await rig.user("ana@example.com")
        _, raj = await rig.user("raj@example.com")
        order_id = await razorpay_checkout(rig, ana)
        body = razorpay_handoff(order_id)
        anonymous = await rig.client.post("/api/billing/verify", json=body)
        stolen = await rig.client.post("/api/billing/verify", json=body, headers=raj)
        assert (anonymous.status_code, stolen.status_code) == (401, 404)
        assert (await rig.me(ana))["credits"] == 0 and (await rig.me(raj))["credits"] == 0
        assert (await rig.client.post("/api/billing/verify", json=body, headers=ana)).status_code == 200


@pytest.mark.parametrize(
    "body", [{}, {"razorpay_order_id": "o"}, {"razorpay_order_id": "o", "razorpay_payment_id": "p"}]
)
async def test_a_handoff_needs_all_three_values(start: Any, body: dict[str, str]) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        response = await rig.client.post("/api/billing/verify", json=body, headers=headers)
    assert response.status_code == 400


# ───────────────────────── Stripe ─────────────────────────


async def post_stripe(rig: Rig, raw: bytes, header: str | None, **headers: str) -> httpx.Response:
    sent = dict(headers)
    if header is not None:
        sent["stripe-signature"] = header
    return await rig.client.post(STRIPE_HOOK, content=raw, headers=sent)


async def test_a_signed_stripe_webhook_pays_the_order_and_replays_change_nothing(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        assert (await rig.checkout(headers, product="pack3", currency="USD")).status_code == 200
        raw = stripe_event("checkout.session.completed", rig.stripe_http.last_session_id)
        header = sign_stripe(raw)

        answers = [await post_stripe(rig, raw, header) for _ in range(3)]
        mine = await rig.me(headers)
        (order,) = await rig.rows(OrderRow)
    assert [(a.status_code, a.json()) for a in answers] == [(200, {"ok": True})] * 3
    assert mine["credits"] == 3 and order.status == "paid"
    assert order.raw["paid"] == {"payment_id": "pi_77", "event": "checkout.session.completed"}


@pytest.mark.parametrize("kind", ["missing", "empty", "wrong secret", "tampered", "stale"])
async def test_a_stripe_webhook_without_a_good_signature_is_a_400_and_changes_nothing(
    start: Any, kind: str
) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        await rig.checkout(headers, product="pack3", currency="USD")
        raw = stripe_event("checkout.session.completed", rig.stripe_http.last_session_id)
        header = {
            "missing": None,
            "empty": "",
            "wrong secret": sign_stripe(raw, secret="whsec_guess"),
            "tampered": sign_stripe(raw.replace(b"900", b"1")),
            "stale": sign_stripe(raw, at=1_000_000_000),
        }[kind]
        refused = await post_stripe(rig, raw, header)
        mine = await rig.me(headers)
        (order,) = await rig.rows(OrderRow)
    assert refused.status_code == 400
    assert mine["credits"] == 0 and order.status == "created"


async def test_the_stripe_webhook_is_exempt_from_the_cross_site_guard(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        await rig.checkout(headers, product="pass30", currency="USD")
        raw = stripe_event("checkout.session.completed", rig.stripe_http.last_session_id, amount_total=1_900)
        response = await post_stripe(
            rig, raw, sign_stripe(raw), origin="https://evil.example", **{"sec-fetch-site": "cross-site"}
        )
        mine = await rig.me(headers)
    assert response.status_code == 200 and mine["passUntil"]


# ───────────────────────── the app around the routes ─────────────────────────


async def test_the_webhook_routes_come_before_the_web_apps_catch_all(start: Any) -> None:
    async with start() as rig:
        unknown = await rig.client.post("/api/billing/webhooks/paypal", content=b"{}")
        wrong_method = await rig.client.get(RAZORPAY_HOOK)
        known = await rig.client.post(RAZORPAY_HOOK, content=b"{}")
    assert unknown.status_code == 404 and unknown.json() == {"error": "Not found"}  # JSON, never index.html
    assert wrong_method.status_code in (404, 405) and "text/html" not in wrong_method.headers["content-type"]
    assert known.status_code == 400  # reached the route: no signature


async def test_deleting_the_account_clears_its_balance_but_keeps_the_payment_record(start: Any) -> None:
    async with start() as rig:
        user, headers = await rig.user()
        order_id = await razorpay_checkout(rig, headers)
        raw, signature = sign_razorpay(razorpay_paid(order_id))
        await post_razorpay(rig, raw, signature)
        await rig.env.container.entitlements.consume(user.id, "m1")

        deleted = await rig.client.delete("/api/me", headers=headers)

        assert deleted.status_code == 204
        async with rig.env.container.db.session() as s:  # type: ignore[union-attr]
            assert (await s.execute(select(func.count()).select_from(EntitlementRow))).scalar_one() == 0
            assert (await s.execute(select(func.count()).select_from(CreditLedgerRow))).scalar_one() == 0
        (order,) = await rig.rows(OrderRow)
        assert (order.user_id, order.status) == (None, "paid")
        # a late replay of the webhook finds nobody to credit and still answers 200
        assert (await post_razorpay(rig, raw, signature)).status_code == 200


async def test_the_secrets_never_appear_in_any_answer(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.user()
        answers = [
            await rig.client.get("/api/billing/entitlements", headers=headers),
            await rig.checkout(headers, product="pack3", currency="INR"),
            await rig.checkout(headers, product="pack3", currency="USD"),
            await post_razorpay(rig, b"{}", "0" * 64),
            await post_stripe(rig, b"{}", "t=1,v1=0"),
        ]
    private = [v for k, v in ALL_SECRETS.items() if k != "razorpay_key_id"]
    for answer in answers:
        assert not any(secret in answer.text for secret in private)
