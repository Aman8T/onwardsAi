"""Auto-apply's settings: the Browser Use key is a secret, the A1 defaults, and the test isolation around them."""

from __future__ import annotations

import os

import pytest
from pydantic import SecretStr

from tayyari.core.settings import ApplySettings, BillingSettings, GuardSettings, Settings


def build(**kwargs: object) -> Settings:
    return Settings(_env_file=None, env="test", **kwargs)  # type: ignore[arg-type]


def test_settings_load_with_no_keys_present() -> None:
    s = build()
    assert s.browser_use_api_key is None and s.gemini_api_key is None and s.auto_apply_enabled is False
    assert s.summary()["browser_use_key"] is False


def test_the_browser_use_key_loads_as_a_secret_and_never_prints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSER_USE_API_KEY", "  bu-SECRET-value  ")
    s = Settings(_env_file=None, env="test")  # type: ignore[call-arg]
    assert isinstance(s.browser_use_api_key, SecretStr)
    assert s.browser_use_api_key.get_secret_value() == "bu-SECRET-value"  # trimmed like every other secret
    text = repr(s) + str(s) + repr(s.summary()) + repr(s.model_dump()) + repr(s.browser_use_api_key)
    assert "bu-SECRET-value" not in text
    assert s.summary()["browser_use_key"] is True


def test_a_blank_browser_use_key_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSER_USE_API_KEY", "   ")
    assert Settings(_env_file=None, env="test").browser_use_api_key is None  # type: ignore[call-arg]


def test_the_a1_defaults() -> None:
    apply = ApplySettings()
    assert apply.max_per_day == 10
    assert (apply.fill_browser_minutes, apply.submit_browser_minutes) == (7, 4)
    assert (apply.fill_model, apply.fill_fallback_model) == ("claude-haiku-5-5", "claude-sonnet-5-5")
    assert (apply.fill_max_rounds, apply.fill_deadline_s) == (4, 300.0)
    assert apply.worker_in_app is True and apply.worker_backoff_s > 0 and apply.requeue_cap >= 0
    assert apply.browser_use_base_url.startswith("https://")
    assert min(apply.est_browser_usd, apply.est_fill_call_usd, apply.est_answer_usd) > 0
    billing = BillingSettings()
    assert billing.free_applications == 10 and billing.apply_credit_cost is None
    assert GuardSettings().est_apply_email_usd > 0
    # the legacy agent settings stay until package Z removes them
    assert apply.agent_order == ["gemini", "claude"] and apply.gemini_model


def test_the_new_settings_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_APPLY__MAX_PER_DAY", "3")
    monkeypatch.setenv("TAYYARI_APPLY__WORKER_IN_APP", "false")
    monkeypatch.setenv("TAYYARI_APPLY__FILL_MAX_ROUNDS", "2")
    monkeypatch.setenv("TAYYARI_BILLING__FREE_APPLICATIONS", "0")
    monkeypatch.setenv("TAYYARI_BILLING__APPLY_CREDIT_COST", "1")
    monkeypatch.setenv("TAYYARI_GUARD__EST_APPLY_EMAIL_USD", "0.01")
    s = Settings(_env_file=None, env="test")  # type: ignore[call-arg]
    assert (s.apply.max_per_day, s.apply.worker_in_app, s.apply.fill_max_rounds) == (3, False, 2)
    assert (s.billing.free_applications, s.billing.apply_credit_cost) == (0, 1)
    assert s.guard.est_apply_email_usd == 0.01


def test_a_blank_apply_credit_cost_leaves_the_price_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_BILLING__APPLY_CREDIT_COST", "")
    assert Settings(_env_file=None, env="test").billing.apply_credit_cost is None  # type: ignore[call-arg]


def test_the_conftest_isolation_strips_the_auto_apply_switch_and_keys() -> None:
    """The autouse `_isolated_env` runs before every test: a developer's shell with auto-apply on and real keys set
    must not reach a test (it would start the in-app worker against Browser Use)."""
    from tests import conftest

    names = ("AUTO_APPLY_ENABLED", "BROWSER_USE_API_KEY", "GEMINI_API_KEY")
    assert set(names) <= set(conftest._ISOLATED_ENV)
    assert not any(name in os.environ for name in names)
    s = Settings()
    assert s.auto_apply_enabled is False and s.browser_use_api_key is None and s.gemini_api_key is None
