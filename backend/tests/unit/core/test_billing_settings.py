"""Settings for payments: `BillingSettings`, the Razorpay and Stripe secrets, and that no secret is ever printed."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from tayyari.core.settings import BillingSettings, Settings

SECRET_NAMES = (
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
)


def build(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_nothing_is_priced_and_one_posting_a_month_is_free_by_default() -> None:
    s = build()
    assert s.billing == BillingSettings(
        pack3_inr=0, pass30_inr=0, pack3_usd=0, pass30_usd=0, free_matches_per_month=1
    )
    assert [getattr(s, name.lower()) for name in SECRET_NAMES] == [None] * 5
    assert (s.razorpay_enabled, s.stripe_enabled) == (False, False)


def test_the_prices_come_from_the_nested_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_BILLING__PACK3_INR", "49900")
    monkeypatch.setenv("TAYYARI_BILLING__PASS30_INR", "99900")
    monkeypatch.setenv("TAYYARI_BILLING__PACK3_USD", "900")
    monkeypatch.setenv("TAYYARI_BILLING__PASS30_USD", "1900")
    monkeypatch.setenv("TAYYARI_BILLING__FREE_MATCHES_PER_MONTH", "2")
    b = build().billing
    assert (b.pack3_inr, b.pass30_inr, b.pack3_usd, b.pass30_usd, b.free_matches_per_month) == (
        49_900,
        99_900,
        900,
        1_900,
        2,
    )


def test_a_blank_price_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_BILLING__PACK3_INR", "  ")
    assert build().billing.pack3_inr == 0


@pytest.mark.parametrize(
    "field", ["pack3_inr", "pass30_inr", "pack3_usd", "pass30_usd", "free_matches_per_month"]
)
def test_a_price_cannot_be_negative(field: str) -> None:
    with pytest.raises(ValidationError):
        BillingSettings(**{field: -1})


def test_the_secrets_come_from_their_third_party_names(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SECRET_NAMES:
        monkeypatch.setenv(name, f"value-of-{name}")
    s = build()
    for name in SECRET_NAMES:
        got = getattr(s, name.lower())
        assert isinstance(got, SecretStr) and got.get_secret_value() == f"value-of-{name}"
    assert (s.razorpay_enabled, s.stripe_enabled) == (True, True)


def test_blank_secrets_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SECRET_NAMES:
        monkeypatch.setenv(name, "   ")
    s = build()
    assert [getattr(s, name.lower()) for name in SECRET_NAMES] == [None] * 5
    assert (s.razorpay_enabled, s.stripe_enabled) == (False, False)


def test_a_provider_needs_every_one_of_its_secrets() -> None:
    razorpay = {"razorpay_key_id": "a", "razorpay_key_secret": "b", "razorpay_webhook_secret": "c"}
    for missing in razorpay:
        assert build(**{k: v for k, v in razorpay.items() if k != missing}).razorpay_enabled is False
    assert build(**razorpay).razorpay_enabled is True

    stripe = {"stripe_secret_key": "a", "stripe_webhook_secret": "b"}
    for missing in stripe:
        assert build(**{k: v for k, v in stripe.items() if k != missing}).stripe_enabled is False
    assert build(**stripe).stripe_enabled is True
    assert build(**stripe).razorpay_enabled is False  # one provider's secrets never switch on the other


def test_no_secret_is_printed_by_repr_str_dump_or_the_startup_summary() -> None:
    s = build(
        razorpay_key_id="rzp-id-SECRET",
        razorpay_key_secret="rzp-key-SECRET",
        razorpay_webhook_secret="rzp-hook-SECRET",
        stripe_secret_key="sk-SECRET",
        stripe_webhook_secret="whsec-SECRET",
    )
    text = repr(s) + str(s) + repr(s.summary()) + repr(s.model_dump())
    for secret in ("rzp-id-SECRET", "rzp-key-SECRET", "rzp-hook-SECRET", "sk-SECRET", "whsec-SECRET"):
        assert secret not in text
    assert (s.summary()["razorpay"], s.summary()["stripe"]) == (True, True)
    assert (build().summary()["razorpay"], build().summary()["stripe"]) == (False, False)
