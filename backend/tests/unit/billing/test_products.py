"""Which products are on sale, and which currency a visitor is shown first."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tayyari.billing.products import (
    PRODUCTS,
    default_currency,
    is_indian_locale,
    is_indian_timezone,
    on_sale,
    price_book,
    price_minor,
    product_def,
)
from tayyari.core.settings import Settings
from tests.fakes.billing import ALL_SECRETS

MakeBilling = Callable[..., Settings]


def test_the_products_are_what_the_spec_says() -> None:
    assert (PRODUCTS["pack3"].credits, PRODUCTS["pack3"].pass_days) == (3, 0)
    assert (PRODUCTS["pass30"].credits, PRODUCTS["pass30"].pass_days) == (0, 30)
    assert product_def("pack3") is PRODUCTS["pack3"] and product_def("pack30") is None


def test_every_price_is_unset_by_default(make_settings: MakeBilling) -> None:
    settings = make_settings()
    assert [price_minor(settings.billing, p, c) for p in PRODUCTS for c in ("INR", "USD")] == [0, 0, 0, 0]
    assert price_book(settings).model_dump(exclude_none=True) == {"INR": {}, "USD": {}}


def test_a_product_is_on_sale_when_it_has_a_price_and_its_provider_has_secrets(
    make_billing_settings: MakeBilling,
) -> None:
    settings = make_billing_settings()
    assert on_sale(settings, "pack3", "INR") == 49_900
    assert on_sale(settings, "pass30", "USD") == 1_900
    assert price_book(settings).model_dump(exclude_none=True) == {
        "INR": {"pack3": 49_900, "pass30": 99_900},
        "USD": {"pack3": 900, "pass30": 1_900},
    }


def test_an_unset_price_hides_just_that_product(make_billing_settings: MakeBilling) -> None:
    settings = make_billing_settings(pass30_inr=0)
    assert on_sale(settings, "pass30", "INR") is None
    assert on_sale(settings, "pack3", "INR") == 49_900
    assert on_sale(settings, "pass30", "USD") == 1_900


def test_a_provider_without_all_its_secrets_offers_nothing(make_billing_settings: MakeBilling) -> None:
    only_stripe = make_billing_settings(
        secrets={k: v for k, v in ALL_SECRETS.items() if k.startswith("stripe")}
    )
    assert price_book(only_stripe).model_dump(exclude_none=True) == {
        "INR": {},
        "USD": {"pack3": 900, "pass30": 1_900},
    }
    no_webhook_secret = make_billing_settings(
        secrets={k: v for k, v in ALL_SECRETS.items() if k != "razorpay_webhook_secret"}
    )
    assert on_sale(no_webhook_secret, "pack3", "INR") is None  # payments it could not reconcile are not taken
    assert on_sale(no_webhook_secret, "pack3", "USD") == 900
    nothing = make_billing_settings(secrets=False)
    assert price_book(nothing).model_dump(exclude_none=True) == {"INR": {}, "USD": {}}


# ───────────────────────── the currency to show first ─────────────────────────


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("en-IN", True),
        ("en-IN,en;q=0.9", True),
        ("en-GB,en;q=0.9,hi;q=0.8", True),  # Hindi is a second language here
        ("hi", True),
        ("ta-IN", True),
        ("bn-IN,bn;q=0.9", True),
        ("EN_in", True),
        ("zh-Hans-IN", True),
        ("en-US,en;q=0.9", False),
        ("en-GB", False),
        ("bn-BD", False),  # Bengali alone is Bangladesh as much as India
        ("ta", False),
        ("id-ID", False),
        ("in-ID", False),  # "in" the old language code for Indonesian, not the region
        ("en-IN;q=0", False),  # explicitly not acceptable
        ("en-US,en-IN;q=0.0", False),
        ("*", False),
        ("", False),
        (None, False),
    ],
)
def test_an_indian_accept_language(header: str | None, expected: bool) -> None:
    assert is_indian_locale(header) is expected


@pytest.mark.parametrize(
    ("zone", "expected"),
    [
        ("Asia/Kolkata", True),
        ("Asia/Calcutta", True),
        ("asia/kolkata", True),
        (" Asia/Kolkata ", True),
        ("Asia/Karachi", False),
        ("Asia/Dhaka", False),
        ("America/New_York", False),
        ("", False),
        (None, False),
    ],
)
def test_an_indian_time_zone(zone: str | None, expected: bool) -> None:
    assert is_indian_timezone(zone) is expected


@pytest.mark.parametrize(
    ("saved", "language", "zone", "expected"),
    [
        ("USD", "en-IN", "Asia/Kolkata", "USD"),  # the saved choice wins over every guess
        ("INR", "en-US", "America/New_York", "INR"),
        (None, "en-IN", None, "INR"),
        (None, None, "Asia/Kolkata", "INR"),
        (None, "en-US", "Asia/Calcutta", "INR"),  # either signal is enough
        (None, "en-US", "Europe/London", "USD"),
        (None, None, None, "USD"),
        ("EUR", "en-IN", None, "INR"),  # a stored value we do not sell in is ignored
        ("", None, None, "USD"),
    ],
)
def test_the_default_currency(
    saved: str | None, language: str | None, zone: str | None, expected: str
) -> None:
    assert default_currency(saved=saved, accept_language=language, timezone=zone) == expected
