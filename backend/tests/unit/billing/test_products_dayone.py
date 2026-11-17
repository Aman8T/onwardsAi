"""Day One as a product (roadmap B2): a lifetime pass, priced by the moment asked about, on sale only with Razorpay."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from tayyari.billing.products import (
    LIFETIME_DAYS,
    LIFETIME_MIN_DAYS,
    PRODUCTS,
    PriceBelowFloor,
    below_floor,
    is_lifetime,
    on_sale,
    price_book,
    price_minor,
)
from tayyari.core.settings import Settings
from tests.fakes.billing import ALL_SECRETS, RAZORPAY_KEYS

MakeSettings = Callable[..., Settings]
ENDS = datetime.fromisoformat("2026-12-15T23:59:00+05:30")
BEFORE = ENDS - timedelta(seconds=1)
NOW = datetime(2026, 11, 17, tzinfo=UTC)


def test_day_one_is_a_lifetime_pass() -> None:
    spec = PRODUCTS["dayone"]
    assert (spec.credits, spec.pass_days, spec.title) == (0, LIFETIME_DAYS, "Leafout: Day One")
    assert LIFETIME_DAYS == 36_500 and LIFETIME_MIN_DAYS == 7_300


def test_only_a_pass_far_past_now_is_lifetime() -> None:
    assert is_lifetime(None, NOW) is False
    assert is_lifetime(NOW + timedelta(days=30), NOW) is False
    assert is_lifetime(NOW + timedelta(days=LIFETIME_MIN_DAYS), NOW) is False
    assert is_lifetime(NOW + timedelta(days=LIFETIME_MIN_DAYS, seconds=1), NOW) is True
    assert is_lifetime(NOW + timedelta(days=LIFETIME_DAYS), NOW) is True


def test_day_one_is_priced_by_the_moment(make_settings: MakeSettings) -> None:
    settings = make_settings()
    assert price_minor(settings, "dayone", "INR", now=BEFORE) == 99_900
    assert price_minor(settings, "dayone", "INR", now=ENDS) == 999_900
    assert price_minor(settings, "dayone", "USD", now=BEFORE) == 0


def test_day_one_is_on_sale_with_razorpay_at_the_price_of_the_moment(make_settings: MakeSettings) -> None:
    settings = make_settings(**RAZORPAY_KEYS)
    assert on_sale(settings, "dayone", "INR", now=BEFORE) == 99_900
    assert on_sale(settings, "dayone", "INR", now=ENDS) == 999_900  # still sold after the early window
    assert on_sale(settings, "dayone", "USD", now=BEFORE) is None  # no dollar price
    assert price_book(settings, now=BEFORE).model_dump(exclude_none=True) == {
        "INR": {"dayone": 99_900},
        "USD": {},
    }


def test_day_one_is_not_on_sale_without_razorpay_or_without_a_price(make_settings: MakeSettings) -> None:
    assert on_sale(make_settings(), "dayone", "INR", now=BEFORE) is None
    unpriced = make_settings(**RAZORPAY_KEYS, plans={"dayone_inr": 0})
    assert on_sale(unpriced, "dayone", "INR", now=BEFORE) is None
    assert on_sale(unpriced, "dayone", "INR", now=ENDS) == 999_900


def test_day_one_in_dollars_needs_its_own_price(make_settings: MakeSettings) -> None:
    settings = make_settings(**ALL_SECRETS, plans={"dayone_usd": 1_200, "dayone_after_usd": 12_000})
    assert on_sale(settings, "dayone", "USD", now=BEFORE) == 1_200
    assert on_sale(settings, "dayone", "USD", now=ENDS) == 12_000


def test_the_older_products_are_never_on_sale_by_default(make_settings: MakeSettings) -> None:
    settings = make_settings(**ALL_SECRETS)
    assert on_sale(settings, "pack3", "INR") is None
    assert on_sale(settings, "pass30", "INR") is None


def test_both_day_one_prices_are_checked_against_the_floor(make_settings: MakeSettings) -> None:
    settings = make_settings(plans={"dayone_inr": 50, "dayone_after_inr": 99_900, "dayone_after_usd": 10})
    assert below_floor(settings) == [
        PriceBelowFloor("dayone", "INR", 50, 100),
        PriceBelowFloor("dayone", "USD", 10, 50),
    ]
