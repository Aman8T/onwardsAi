"""Shared fixtures for every test area.

The autouse `_isolated_env` fixture makes sure no test can read the developer's real environment or the
repo's `.env` (which holds live API keys): credentials, cloud markers and every ``TAYYARI_*`` variable are
removed, the settings cache is cleared, and `Settings` stops reading `.env` files altogether. Use the
`settings` or `make_settings` fixtures to get a `Settings` object that is safe for tests: env ``test``,
pacing off and a throw-away data folder.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tayyari.core.settings import Settings, get_settings

_ISOLATED_ENV = (
    "ANTHROPIC_API_KEY",
    "JEV_API_KEY",
    "TYPESAFE_API_KEY",
    "GITHUB_TOKEN",
    "DATABASE_URL",
    "LOGFIRE_TOKEN",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "RESEND_API_KEY",
    "SARVAM_API_KEY",
    "RESEND_FROM",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "FASTAPICLOUD_DEPLOYMENT_ID",
    "FASTAPI_PUBLIC_URL",
    "PORT",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _ISOLATED_ENV:
        monkeypatch.delenv(name, raising=False)
    for name in [n for n in os.environ if n.upper().startswith("TAYYARI_")]:
        monkeypatch.delenv(name, raising=False)
    # The Leafout Weekly's background loop would email and spend: off unless a test turns it on with its own settings.
    monkeypatch.setenv("TAYYARI_WEEKLY__ENABLED", "false")
    # Settings() anywhere in the process (get_settings included) must not read the repo's .env files.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def make_settings(tmp_path: Path) -> Callable[..., Settings]:
    """Build `Settings` for a test: ``make_settings(guard=GuardSettings(enabled=False), env="prod")``."""

    def build(**overrides: Any) -> Settings:
        values: dict[str, Any] = {"env": "test", "pacing_scale": 0.0, "data_dir": tmp_path / "data"}
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return build


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    """Default test settings: env ``test``, no pacing, data folder under ``tmp_path``, no credentials."""
    return make_settings()
