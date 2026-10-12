"""Fixtures for the payment tests: a manual clock, fake repositories, prices, and the services over them. Nothing
here can reach Razorpay or Stripe: the gateways get a `MockTransport` / an in-memory `stripe.HTTPClient`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tayyari.billing.entitlements import EntitlementService
from tayyari.core.settings import BillingSettings, Settings
from tests.fakes.billing import ALL_SECRETS, PRICES
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fakes(clock: FakeClock) -> FakeRepositories:
    return FakeRepositories(clock)


@pytest.fixture
def billing_settings() -> BillingSettings:
    return BillingSettings(**PRICES)


@pytest.fixture
def entitlements(
    fakes: FakeRepositories, billing_settings: BillingSettings, clock: FakeClock
) -> EntitlementService:
    return EntitlementService(fakes.billing, billing_settings, clock)


@pytest.fixture
def make_billing_settings(make_settings: Callable[..., Settings]) -> Callable[..., Settings]:
    """``make_billing_settings(secrets=False, pack3_inr=0, auth={...})``: prices and secrets on; a price name changes
    that price (0 = unset), ``secrets`` is False for none or a dict of the ones to set, anything else is a setting."""

    def build(*, secrets: bool | dict[str, Any] = True, **fields: Any) -> Settings:
        prices = {name: fields.pop(name) for name in list(fields) if name in PRICES}
        values: dict[str, Any] = {"billing": {**PRICES, **prices}, **fields}
        if secrets is True:
            values.update(ALL_SECRETS)
        elif isinstance(secrets, dict):
            values.update(secrets)
        return make_settings(**values)

    return build
