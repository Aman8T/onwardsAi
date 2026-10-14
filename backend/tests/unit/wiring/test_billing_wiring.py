"""Payments in the composition root and the app: the container builds the entitlement and billing services, a gateway
exists only for a provider whose secrets are set (Razorpay: its key pair, the webhook secret is optional; Stripe: both),
what is injected is used as given, the start-up warnings say what is half-configured, and the billing routes are
registered ahead of the web app's catch-all."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
import requests
import stripe

from tayyari.api.app import create_app
from tayyari.billing.entitlements import EntitlementService
from tayyari.billing.razorpay import RazorpayGateway
from tayyari.billing.service import BillingService
from tayyari.billing.stripe_gw import StripeGateway
from tayyari.container import Container, ContainerOverrides
from tayyari.core.settings import Settings
from tests.fakes.billing import (
    ALL_SECRETS,
    PRICES,
    RAZORPAY_KEYS,
    FakeRazorpay,
    FakeStripeHttp,
    captured_events,
)
from tests.fakes.repos import FakeRepositories
from tests.unit.wiring.support import FakeAnalysis, FakeChat

MakeSettings = Callable[..., Settings]


async def build(settings: Settings, **overrides: Any) -> Container:
    values: dict[str, Any] = {"analysis": FakeAnalysis(), "chat": FakeChat(), **overrides}
    return await Container.build(settings, overrides=ContainerOverrides(**values))


async def test_the_container_builds_both_services_with_no_gateway_when_nothing_is_configured(
    make_settings: MakeSettings,
) -> None:
    container = await build(make_settings())
    try:
        assert isinstance(container.entitlements, EntitlementService)
        assert isinstance(container.billing, BillingService)
        assert container.billing.entitlements is container.entitlements  # one service decides, everywhere
        assert container.billing._razorpay is None and container.billing._stripe is None
    finally:
        await container.aclose()


async def test_each_provider_is_built_only_when_its_secrets_are_set(
    make_settings: MakeSettings,
) -> None:
    razorpay_only = {k: v for k, v in ALL_SECRETS.items() if k.startswith("razorpay")}
    stripe_only = {k: v for k, v in ALL_SECRETS.items() if k.startswith("stripe")}
    keys_and_stripe_key = {
        k: v for k, v in ALL_SECRETS.items() if k not in ("razorpay_webhook_secret", "stripe_webhook_secret")
    }
    razorpay_half = {
        k: v for k, v in ALL_SECRETS.items() if k in ("razorpay_key_id", "razorpay_webhook_secret")
    }
    built: dict[str, Container] = {}
    for name, secrets in {
        "razorpay": razorpay_only,
        "stripe": stripe_only,
        "both": ALL_SECRETS,
        "keys only": keys_and_stripe_key,
        "razorpay half": razorpay_half,
    }.items():
        built[name] = await build(make_settings(**secrets))
    try:
        found = {
            name: (
                isinstance(c.billing._razorpay, RazorpayGateway),
                isinstance(c.billing._stripe, StripeGateway),
            )
            for name, c in built.items()
        }
        assert found == {
            "razorpay": (True, False),
            "stripe": (False, True),
            "both": (True, True),
            # Razorpay's key pair is enough (the hand-off is verified with the key secret); Stripe's hosted page has
            # no hand-off, so its webhook secret is mandatory.
            "keys only": (True, False),
            "razorpay half": (False, False),  # a key id and a webhook secret, but no key secret
        }
        webhooks = {
            name: c.billing._razorpay.webhooks_enabled  # type: ignore[union-attr]
            for name, c in built.items()
            if c.billing._razorpay is not None
        }
        assert webhooks == {"razorpay": True, "both": True, "keys only": False}
    finally:
        for container in built.values():
            await container.aclose()


async def test_the_entitlement_service_reads_the_prices_and_allowance_from_the_settings(
    make_settings: MakeSettings,
) -> None:
    container = await build(make_settings(billing={**PRICES, "free_matches_per_month": 0}, **ALL_SECRETS))
    try:
        assert container.settings.billing.pack3_inr == 49_900
        assert (await container.entitlements.summary("nobody")).free_match_available is False
    finally:
        await container.aclose()


async def test_the_container_uses_the_injected_sessions_for_the_two_providers(
    make_settings: MakeSettings,
) -> None:
    rzp, stripe_http = FakeRazorpay(), FakeStripeHttp()
    container = await build(
        make_settings(billing=dict(PRICES), auth={"public_url": "https://tayyari.example"}, **ALL_SECRETS),
        razorpay_session=rzp,
        stripe_http=stripe_http,
    )
    try:
        user = await container.repos.users.sign_in_email(email="ana@example.com", now=container.clock.now())
        await container.billing.checkout(user, "pack3", "INR")
        await container.billing.checkout(user, "pack3", "USD")
        assert len(rzp.requests) == 1 and len(stripe_http.requests) == 1
    finally:
        await container.aclose()


async def test_the_key_pair_alone_sells_through_the_container(make_settings: MakeSettings) -> None:
    rzp = FakeRazorpay()
    container = await build(make_settings(billing=dict(PRICES), **RAZORPAY_KEYS), razorpay_session=rzp)
    try:
        user = await container.repos.users.sign_in_email(email="ana@example.com", now=container.clock.now())
        answer = await container.billing.checkout(user, "pack3", "INR")
        assert (answer.provider, answer.orderId) == ("razorpay", "order_Rzp0001")
        assert container.billing.prices().model_dump(exclude_none=True)["INR"] == {
            "pack3": 49_900,
            "pass30": 99_900,
        }
    finally:
        await container.aclose()


async def test_injected_services_are_used_as_given(make_settings: MakeSettings) -> None:
    container = await build(make_settings())
    try:
        own = await build(make_settings(), entitlements=container.entitlements, billing=container.billing)
        try:
            assert own.entitlements is container.entitlements and own.billing is container.billing
        finally:
            await own.aclose()
    finally:
        await container.aclose()


async def test_posting_match_can_call_the_entitlement_api_on_the_container(
    make_settings: MakeSettings,
) -> None:
    """What wave M2 relies on: ``container.entitlements.consume(user_id, ref)`` and the matching grant."""
    fakes = FakeRepositories()
    container = await build(make_settings(), repos=fakes.bundle)
    try:
        used = await container.entitlements.consume("user-1", "match-1")
        assert (used.source, used.credits, used.replayed) == ("free_monthly", 0, False)
        await container.entitlements.grant("user-1", "pack3", "order-1")
        assert (await container.entitlements.consume("user-1", "match-2")).source == "credit"
        assert (
            fakes.billing.ledger_sum("user-1")
            == 2
            == (await container.entitlements.summary("user-1")).credits
        )
    finally:
        await container.aclose()


async def test_the_session_handed_in_for_razorpay_is_the_one_the_sdk_sends_through_and_stays_open(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    rzp = FakeRazorpay()
    closed: list[requests.Session] = []
    monkeypatch.setattr(rzp, "close", lambda: closed.append(rzp))
    container = await build(make_settings(**RAZORPAY_KEYS), razorpay_session=rzp)
    assert container.billing._razorpay._client.session is rzp  # type: ignore[union-attr]
    await container.aclose()
    assert closed == []  # the container closes only what it built itself


async def test_without_an_injected_session_the_sdk_builds_its_own(make_settings: MakeSettings) -> None:
    container = await build(make_settings(**RAZORPAY_KEYS))
    try:
        session = container.billing._razorpay._client.session  # type: ignore[union-attr]
        assert isinstance(session, requests.Session) and not isinstance(session, FakeRazorpay)
    finally:
        await container.aclose()


# ───────────────────────── what the container says at start-up ─────────────────────────


@pytest.fixture
def captured_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """The structured log events of everything built inside the test. The container's own logging set-up is
    replaced, or it would reconfigure structlog and drop the capture."""
    monkeypatch.setattr("tayyari.container.configure_logging", lambda _settings: None)
    with captured_events(monkeypatch, "tayyari.container") as logs:
        yield logs


def events(logs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == name]


async def test_the_container_warns_once_that_razorpay_has_no_webhook_secret(
    make_settings: MakeSettings, captured_logs: list[dict[str, Any]]
) -> None:
    container = await build(make_settings(**RAZORPAY_KEYS))
    await container.aclose()
    (warning,) = events(captured_logs, "razorpay_webhooks_off")
    assert warning["log_level"] == "warning" and "hand-off only" in warning["hint"]
    assert all(value not in str(captured_logs) for value in RAZORPAY_KEYS.values())  # no key reaches the log


async def test_the_container_is_quiet_about_webhooks_when_razorpay_is_complete_or_absent(
    make_settings: MakeSettings, captured_logs: list[dict[str, Any]]
) -> None:
    for settings in (
        make_settings(**ALL_SECRETS),
        make_settings(),
        make_settings(razorpay_webhook_secret="only-the-webhook-secret"),
    ):
        await (await build(settings)).aclose()
    assert events(captured_logs, "razorpay_webhooks_off") == []


async def test_the_container_warns_about_each_price_under_a_providers_minimum(
    make_settings: MakeSettings, captured_logs: list[dict[str, Any]]
) -> None:
    prices = {**PRICES, "pack3_inr": 99, "pass30_usd": 49}
    container = await build(make_settings(billing=prices, **ALL_SECRETS))
    try:
        warned = events(captured_logs, "price_below_provider_minimum")
        assert {(e["product"], e["currency"], e["price_minor"], e["minimum_minor"]) for e in warned} == {
            ("pack3", "INR", 99, 100),
            ("pass30", "USD", 49, 50),
        }
        assert len(warned) == 2 and all(e["log_level"] == "warning" for e in warned)
        assert container.billing.prices().model_dump(exclude_none=True) == {
            "INR": {"pass30": 99_900},
            "USD": {"pack3": 900},
        }
    finally:
        await container.aclose()


async def test_the_container_is_quiet_about_prices_that_are_fine_or_unset(
    make_settings: MakeSettings, captured_logs: list[dict[str, Any]]
) -> None:
    for settings in (make_settings(billing=dict(PRICES), **ALL_SECRETS), make_settings()):
        await (await build(settings)).aclose()
    assert events(captured_logs, "price_below_provider_minimum") == []


def test_the_stripe_package_is_the_one_the_gateway_expects() -> None:
    assert stripe.Webhook.construct_event and stripe.StripeClient and stripe.SignatureVerificationError


async def test_the_billing_routes_are_registered_before_the_web_apps_catch_all(
    make_settings: MakeSettings,
) -> None:
    app = create_app(make_settings())

    def flatten(routes: Any) -> list[tuple[str, tuple[str, ...]]]:
        out: list[tuple[str, tuple[str, ...]]] = []
        for route in routes:
            inner = getattr(getattr(route, "original_router", None), "routes", None)
            if inner is not None:
                out.extend(flatten(inner))
            else:
                out.append((route.path, tuple(sorted(route.methods or ()))))
        return out

    ordered = flatten(app.router.routes)
    expected = [
        ("/api/billing/entitlements", ("GET",)),
        ("/api/billing/checkout", ("POST",)),
        ("/api/billing/verify", ("POST",)),
        ("/api/billing/webhooks/razorpay", ("POST",)),
        ("/api/billing/webhooks/stripe", ("POST",)),
    ]
    catch_all = ordered.index(("/{path:path}", ("GET", "HEAD")))
    api_catch_all = ordered.index(
        ("/api/{_path:path}", ("DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"))
    )
    for entry in expected:
        assert entry in ordered, entry
        assert ordered.index(entry) < min(
            catch_all, api_catch_all
        )  # or the 404 / SPA fallback would answer first
