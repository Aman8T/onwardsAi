"""Settings for accounts: `AuthSettings`, the Google and Resend credentials, and that no secret is ever printed."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from tayyari.core.settings import AuthSettings, Settings


def build(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_the_defaults_match_the_spec() -> None:
    s = build()
    assert s.auth == AuthSettings(
        session_days=30,
        magic_link_minutes=15,
        magic_per_email=3,
        magic_per_ip=3,
        cookie_name="tayyari_session",
        public_url="http://localhost:5173",
    )
    assert (s.google_client_id, s.google_client_secret, s.resend_api_key, s.resend_from) == (
        None,
        None,
        None,
        None,
    )
    assert s.google_enabled is False


def test_the_public_url_comes_from_the_platform_when_there_is_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FASTAPI_PUBLIC_URL", "https://tayyari.fastapicloud.dev/")
    assert build().auth.public_url == "https://tayyari.fastapicloud.dev"
    monkeypatch.setenv("FASTAPI_PUBLIC_URL", "tayyari.fastapicloud.dev")  # no scheme: https
    assert build().auth.public_url == "https://tayyari.fastapicloud.dev"


def test_the_public_url_can_be_set_explicitly_and_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FASTAPI_PUBLIC_URL", "https://platform.example")
    monkeypatch.setenv("TAYYARI_AUTH__PUBLIC_URL", "https://tayyari.example/")
    assert build().auth.public_url == "https://tayyari.example"
    monkeypatch.setenv("TAYYARI_AUTH__PUBLIC_URL", "   ")  # blank counts as unset
    assert build().auth.public_url == "https://platform.example"


def test_the_nested_variables_override_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_AUTH__SESSION_DAYS", "7")
    monkeypatch.setenv("TAYYARI_AUTH__MAGIC_LINK_MINUTES", "10")
    monkeypatch.setenv("TAYYARI_AUTH__MAGIC_PER_EMAIL", "5")
    monkeypatch.setenv("TAYYARI_AUTH__COOKIE_NAME", "sid")
    s = build()
    assert (s.auth.session_days, s.auth.magic_link_minutes, s.auth.magic_per_email, s.auth.cookie_name) == (
        7,
        10,
        5,
        "sid",
    )


@pytest.mark.parametrize("field", ["session_days", "magic_link_minutes"])
def test_lifetimes_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        AuthSettings(**{field: 0})


def test_the_credentials_come_from_their_third_party_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id-123")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("RESEND_API_KEY", "re_789")
    monkeypatch.setenv("RESEND_FROM", "Tayyari <login@tayyari.example>")
    s = build()
    assert isinstance(s.google_client_id, SecretStr) and s.google_client_id.get_secret_value() == "id-123"
    assert s.google_client_secret is not None and s.google_client_secret.get_secret_value() == "secret-456"
    assert s.resend_api_key is not None and s.resend_api_key.get_secret_value() == "re_789"
    assert s.resend_from == "Tayyari <login@tayyari.example>"
    assert s.google_enabled is True


def test_blank_credentials_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "RESEND_API_KEY", "RESEND_FROM"):
        monkeypatch.setenv(name, "  ")
    s = build()
    assert s.google_client_id is None and s.resend_api_key is None and s.resend_from is None
    assert s.google_enabled is False


def test_google_needs_both_halves() -> None:
    assert build(google_client_id="only-id").google_enabled is False
    assert build(google_client_secret="only-secret").google_enabled is False
    assert build(google_client_id="id", google_client_secret="secret").google_enabled is True


def test_no_secret_is_printed_by_repr_str_or_the_startup_summary() -> None:
    s = build(
        google_client_id="gid-SECRET",
        google_client_secret="gsecret-SECRET",
        resend_api_key="re_SECRET",
        resend_from="Tayyari <login@tayyari.example>",
    )
    text = repr(s) + str(s) + repr(s.summary()) + repr(s.model_dump())
    for secret in ("gid-SECRET", "gsecret-SECRET", "re_SECRET"):
        assert secret not in text
    assert s.summary()["google_oauth"] is True and s.summary()["resend_key"] is True
    assert build().summary()["google_oauth"] is False and build().summary()["resend_key"] is False
