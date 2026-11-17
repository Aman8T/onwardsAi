"""The plans' settings (roadmap B2): the defaults, the environment, a naive end date refused, and the one place that
picks Day One's early or after price; and the 402 body with and without a limit's keys."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tayyari.core.errors import PaymentRequired
from tayyari.core.settings import BillingSettings, PlansSettings, Settings

ENDS = datetime.fromisoformat("2026-12-15T23:59:00+05:30")


def build(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_the_defaults_are_the_decided_plans() -> None:
    p = build().plans
    assert (p.lowkey_inr, p.locked_in_inr, p.dayone_inr, p.dayone_after_inr) == (
        39_900,
        129_900,
        99_900,
        999_900,
    )
    assert (p.dayone_usd, p.dayone_after_usd) == (0, 0)
    assert p.dayone_ends_at == ENDS and p.dayone_ends_at.utcoffset() == timedelta(hours=5, minutes=30)
    assert p.refund_days == 7
    assert (p.free_analyses, p.free_searches, p.free_practice, p.free_coach) == (2, 5, 3, 30)


def test_the_two_ledger_limits_have_their_new_defaults() -> None:
    b = BillingSettings()
    assert (b.free_matches_per_month, b.free_applications) == (3, 5)


def test_every_plan_value_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_PLANS__DAYONE_INR", "149900")
    monkeypatch.setenv("TAYYARI_PLANS__DAYONE_ENDS_AT", "2027-01-31T18:30:00+00:00")
    monkeypatch.setenv("TAYYARI_PLANS__FREE_SEARCHES", "7")
    monkeypatch.setenv("TAYYARI_PLANS__LOCKED_IN_INR", " ")  # blank keeps the default
    p = build().plans
    assert p.dayone_inr == 149_900
    assert p.dayone_ends_at == datetime(2027, 1, 31, 18, 30, tzinfo=UTC)
    assert p.free_searches == 7
    assert p.locked_in_inr == 129_900


@pytest.mark.parametrize("naive", ["2026-12-15T23:59:00", datetime(2026, 12, 15, 23, 59)])
def test_an_end_date_without_a_time_zone_is_refused(naive: object) -> None:
    with pytest.raises(ValidationError):
        PlansSettings(dayone_ends_at=naive)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field", ["dayone_inr", "dayone_after_inr", "free_coach", "refund_days", "lowkey_inr"]
)
def test_a_plan_value_cannot_be_negative(field: str) -> None:
    with pytest.raises(ValidationError):
        PlansSettings(**{field: -1})  # type: ignore[arg-type]


def test_the_early_price_holds_until_the_end_moment_and_not_at_it() -> None:
    p = PlansSettings(dayone_usd=1_200, dayone_after_usd=12_000)
    just_before = ENDS - timedelta(seconds=1)
    assert p.dayone_early(just_before) is True
    assert p.dayone_price(just_before, "INR") == 99_900
    assert p.dayone_price(just_before, "USD") == 1_200
    assert p.dayone_early(ENDS) is False
    assert p.dayone_price(ENDS, "INR") == 999_900
    assert p.dayone_price(ENDS.astimezone(UTC), "USD") == 12_000  # the same moment, in another zone


# ───────────────────────── the 402 body ─────────────────────────


def test_a_no_credits_body_has_no_more_keys() -> None:
    assert PaymentRequired().to_body() == {"error": "payment_required", "reason": "no_credits"}
    assert PaymentRequired("no_credits", user_message="x").to_body() == {
        "error": "payment_required",
        "reason": "no_credits",
    }


def test_a_limit_body_carries_only_the_keys_it_was_given() -> None:
    monthly = PaymentRequired(
        "limit_reached", feature="searches", limit=5, resets_at="2026-12-01T00:00:00+00:00"
    )
    assert monthly.to_body() == {
        "error": "payment_required",
        "reason": "limit_reached",
        "feature": "searches",
        "limit": 5,
        "resetsAt": "2026-12-01T00:00:00+00:00",
    }
    once = PaymentRequired("limit_reached", feature="applications", limit=5)
    assert once.to_body() == {
        "error": "payment_required",
        "reason": "limit_reached",
        "feature": "applications",
        "limit": 5,
    }
    assert once.status == 402
