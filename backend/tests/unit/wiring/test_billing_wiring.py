"""Payments in the composition root and the app: the container builds the entitlement and billing services, a gateway
exists only for a provider whose secrets are all set, what is injected is used as given, shutdown closes what the
container opened, and the billing routes are registered ahead of the web app's catch-all."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx2
import pytest
import stripe

from tayyari.api.app import create_app
from tayyari.billing.entitlements import EntitlementService
from tayyari.billing.razorpay import RazorpayGateway
from tayyari.billing.service import BillingService
from tayyari.billing.stripe_gw import StripeGateway
from tayyari.container import Container, ContainerOverrides
from tayyari.core.settings import Settings
from tests.fakes.billing import ALL_SECRETS, PRICES, FakeRazorpay, FakeStripeHttp
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


async def test_each_provider_is_built_only_when_all_of_its_secrets_are_set(
    make_settings: MakeSettings,
) -> None:
    razorpay_only = {k: v for k, v in ALL_SECRETS.items() if k.startswith("razorpay")}
    stripe_only = {k: v for k, v in ALL_SECRETS.items() if k.startswith("stripe")}
    half = {
        k: v for k, v in ALL_SECRETS.items() if k not in ("razorpay_webhook_secret", "stripe_webhook_secret")
    }
    built: dict[str, Container] = {}
    for name, secrets in {
        "razorpay": razorpay_only,
        "stripe": stripe_only,
        "both": ALL_SECRETS,
        "half": half,
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
            "half": (False, False),  # keys without the webhook secret: payments that could not be reconciled
        }
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


async def test_the_container_uses_the_injected_transports_for_the_two_providers(
    make_settings: MakeSettings,
) -> None:
    rzp, stripe_http = FakeRazorpay(), FakeStripeHttp()
    container = await build(
        make_settings(billing=dict(PRICES), auth={"public_url": "https://tayyari.example"}, **ALL_SECRETS),
        billing_transport=rzp.transport,
        stripe_http=stripe_http,
    )
    try:
        user = await container.repos.users.sign_in_email(email="ana@example.com", now=container.clock.now())
        await container.billing.checkout(user, "pack3", "INR")
        await container.billing.checkout(user, "pack3", "USD")
        assert len(rzp.requests) == 1 and len(stripe_http.requests) == 1
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


async def test_the_http_client_it_opened_for_razorpay_is_closed_with_the_container(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[httpx2.AsyncClient] = []
    closed: list[httpx2.AsyncClient] = []
    original_init, original_close = httpx2.AsyncClient.__init__, httpx2.AsyncClient.aclose

    def init(self: httpx2.AsyncClient, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        opened.append(self)

    async def close(self: httpx2.AsyncClient) -> None:
        closed.append(self)
        await original_close(self)

    monkeypatch.setattr(httpx2.AsyncClient, "__init__", init)
    monkeypatch.setattr(httpx2.AsyncClient, "aclose", close)
    container = await build(make_settings(**ALL_SECRETS))
    razorpay_client = container.billing._razorpay._client  # type: ignore[union-attr]
    assert razorpay_client in opened
    await container.aclose()
    assert razorpay_client in closed


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
