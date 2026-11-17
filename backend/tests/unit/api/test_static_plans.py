"""`fill_prices` and the plans (roadmap B2): Day One's price of the moment, the printed prices, the end date."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from tayyari.api.static import fill_prices
from tayyari.core.settings import Settings
from tests.fakes.billing import RAZORPAY_KEYS

ENDS = datetime.fromisoformat("2026-12-15T23:59:00+05:30")
BEFORE = ENDS - timedelta(minutes=1)
PAGE = (
    "Day One {{price:dayone}}, then {{price:dayoneafter}} after {{date:dayone}}. "
    "Lowkey {{price:lowkey}}, Locked In {{price:lockedin}}. Old: {{price:pack3}} {{price:pass30}}."
)


def settings(*, keys: bool = True, **fields: Any) -> Settings:
    return Settings(_env_file=None, env="test", **({**RAZORPAY_KEYS} if keys else {}), **fields)  # type: ignore[arg-type]


def test_every_plan_placeholder_is_filled_before_the_end() -> None:
    assert fill_prices(PAGE, settings(), now=BEFORE) == (
        "Day One ₹999, then ₹9,999 after 15 Dec 2026. "
        "Lowkey ₹399, Locked In ₹1,299. Old: Coming soon Coming soon."
    )


def test_from_the_end_moment_day_one_is_the_after_price() -> None:
    assert fill_prices("{{price:dayone}}", settings(), now=ENDS) == "₹9,999"


def test_without_razorpay_day_one_is_coming_soon_but_the_printed_prices_stay() -> None:
    filled = fill_prices(PAGE, settings(keys=False), now=BEFORE)
    assert filled.startswith("Day One Coming soon, then ₹9,999 after 15 Dec 2026.")
    assert "Lowkey ₹399, Locked In ₹1,299." in filled


def test_an_unset_printed_price_reads_coming_soon_and_unknown_names_leave_nothing_raw() -> None:
    page = "{{price:lowkey}} {{price:annual}} [{{date:other}}] {{date:dayone}}"
    custom = settings(plans={"lowkey_inr": 0, "dayone_ends_at": "2027-01-31T23:59:00+05:30"})
    assert fill_prices(page, custom, now=BEFORE) == "Coming soon Coming soon [] 31 Jan 2027"


def test_the_older_products_still_print_their_price_when_one_is_set() -> None:
    priced = settings(billing={"pack3_inr": 49_900})
    assert fill_prices("{{price:pack3}} {{price:pass30}}", priced) == "₹499 Coming soon"


def test_the_refund_days_placeholder_is_a_plain_number() -> None:
    assert fill_prices("within {{days:refund}} days", settings()) == "within 7 days"
    custom = settings(plans={"refund_days": 14})
    assert fill_prices("{{days:refund}} / {{days:refund}}", custom) == "14 / 14"
