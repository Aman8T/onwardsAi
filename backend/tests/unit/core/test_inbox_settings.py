"""The application inbox's settings: off by default, on only with the flag and both Resend secrets, read from the
documented environment names, the domain tidied, and the startup summary reporting presence only. Values are made up."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import SecretStr

from tayyari.core.settings import Settings

FAKE_SECRET = "whsec_dGVzdA=="
FAKE_KEY = "re_test_fake"


def test_off_by_default(settings: Settings) -> None:
    assert settings.inbound_email_enabled is False and settings.inbound_email_active is False
    assert settings.inbound_email_domain == "apply.leafout.in"
    assert settings.inbound_copy_from == "Leafout <inbox@apply.leafout.in>"
    assert settings.resend_webhook_secret is None


@pytest.mark.parametrize(
    ("enabled", "key", "secret", "active"),
    [
        (True, FAKE_KEY, FAKE_SECRET, True),
        (True, FAKE_KEY, None, False),
        (True, None, FAKE_SECRET, False),
        (False, FAKE_KEY, FAKE_SECRET, False),
    ],
)
def test_on_only_with_the_flag_and_both_secrets(
    make_settings: Callable[..., Settings], enabled: bool, key: str | None, secret: str | None, active: bool
) -> None:
    built = make_settings(
        inbound_email_enabled=enabled,
        resend_api_key=SecretStr(key) if key else None,
        resend_webhook_secret=SecretStr(secret) if secret else None,
    )
    assert built.inbound_email_active is active


def test_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, make_settings: Callable[..., Settings]
) -> None:
    monkeypatch.setenv("INBOUND_EMAIL_ENABLED", "true")
    monkeypatch.setenv("INBOUND_EMAIL_DOMAIN", " @Inbox.Example.COM. ")
    monkeypatch.setenv("RESEND_API_KEY", FAKE_KEY)
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", f"  {FAKE_SECRET}  ")
    monkeypatch.setenv("INBOUND_COPY_FROM", "Copies <copies@inbox.example.com>")
    built = make_settings()
    assert built.inbound_email_active
    assert built.inbound_email_domain == "inbox.example.com"
    assert built.resend_webhook_secret is not None
    assert built.resend_webhook_secret.get_secret_value() == FAKE_SECRET
    assert built.inbound_copy_from == "Copies <copies@inbox.example.com>"


def test_a_blank_secret_is_unset(
    monkeypatch: pytest.MonkeyPatch, make_settings: Callable[..., Settings]
) -> None:
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "   ")
    assert make_settings().resend_webhook_secret is None


def test_the_summary_says_presence_never_values(make_settings: Callable[..., Settings]) -> None:
    built = make_settings(
        inbound_email_enabled=True,
        resend_api_key=SecretStr(FAKE_KEY),
        resend_webhook_secret=SecretStr(FAKE_SECRET),
    )
    summary = built.summary()
    assert summary["inbound_email"] is True and summary["inbound_email_active"] is True
    assert summary["resend_webhook_secret"] is True
    assert FAKE_SECRET not in repr(summary) and FAKE_SECRET not in repr(built)
