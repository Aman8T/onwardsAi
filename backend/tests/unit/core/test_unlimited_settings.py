"""`TAYYARI_GUARD__UNLIMITED_EMAILS`: how the list is read from the real environment (a comma string and a JSON list, which
pydantic-settings would otherwise try to decode), how it is normalised, who counts as on it, and that no address is printed."""

from __future__ import annotations

from pathlib import Path

import pytest

from tayyari.core.settings import GuardSettings, Settings, parse_email_list

VAR = "TAYYARI_GUARD__UNLIMITED_EMAILS"


def build(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


# ───────────────────────── from the environment ─────────────────────────


def test_a_comma_separated_variable_is_read_as_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VAR, "tester@example.com,second@example.org")
    guard = build().guard
    assert guard.unlimited_email_list == ("tester@example.com", "second@example.org")
    assert guard.has_unlimited is True


def test_a_json_list_variable_is_read_as_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VAR, '["tester@example.com", "Second@Example.org"]')
    assert build().guard.unlimited_email_list == ("tester@example.com", "second@example.org")


def test_the_variable_works_from_an_env_file_too(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{VAR}=tester@example.com, other@example.com\n", encoding="utf-8")
    assert Settings(_env_file=env_file).guard.unlimited_email_list == (
        "tester@example.com",
        "other@example.com",
    )


def test_unset_and_blank_variables_mean_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    assert build().guard.unlimited_email_list == ()
    assert build().guard.has_unlimited is False
    for blank in ("", "   ", ",", " , ; ", "[]", "[ ]"):
        monkeypatch.setenv(VAR, blank)
        guard = build().guard
        assert guard.unlimited_email_list == (), repr(blank)
        assert guard.has_unlimited is False, repr(blank)
        assert guard.is_unlimited("tester@example.com") is False


def test_a_list_given_in_code_is_accepted_too() -> None:
    assert GuardSettings(unlimited_emails=["A@x.com", " b@y.com "]).unlimited_email_list == (
        "a@x.com",
        "b@y.com",
    )  # type: ignore[arg-type]
    assert GuardSettings(unlimited_emails=("a@x.com",)).unlimited_email_list == ("a@x.com",)  # type: ignore[arg-type]
    assert GuardSettings(unlimited_emails=None).unlimited_email_list == ()  # type: ignore[arg-type]


# ───────────────────────── normalising ─────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("tester@example.com", ("tester@example.com",)),
        ("  Tester@Example.COM  ", ("tester@example.com",)),
        ("a@x.com, b@y.com ,c@z.com", ("a@x.com", "b@y.com", "c@z.com")),
        ("a@x.com;b@y.com", ("a@x.com", "b@y.com")),
        ("a@x.com\nb@y.com\r\n", ("a@x.com", "b@y.com")),
        ("a@x.com,A@X.com, a@x.com ,b@y.com,a@x.com", ("a@x.com", "b@y.com")),
        ("a@x.com,,,b@y.com,", ("a@x.com", "b@y.com")),
        ('["a@x.com","B@y.com","a@X.com"]', ("a@x.com", "b@y.com")),
        ('["a@x.com", 7, null, "b@y.com"]', ("a@x.com", "b@y.com")),
        ('["a@x.com,b@y.com"]', ("a@x.com", "b@y.com")),
        ("not-an-address, a@x.com", ("a@x.com",)),
        (
            '["a@x.com", "b@y.com"',
            ("a@x.com", "b@y.com"),
        ),  # broken JSON: read as plain text, never a failed start
        ("", ()),
    ],
)
def test_the_list_is_trimmed_lower_cased_and_without_repeats(raw: str, expected: tuple[str, ...]) -> None:
    assert parse_email_list(raw) == expected
    assert GuardSettings(unlimited_emails=raw).unlimited_email_list == expected


# ───────────────────────── who is on it ─────────────────────────


def test_is_unlimited_matches_the_whole_address_ignoring_case_and_padding() -> None:
    guard = GuardSettings(unlimited_emails="tester@example.com,Second@Example.org")
    assert guard.is_unlimited("tester@example.com") is True
    assert guard.is_unlimited("  TESTER@Example.com ") is True
    assert guard.is_unlimited("second@example.org") is True


@pytest.mark.parametrize(
    "email",
    [
        None,
        "",
        "   ",
        "other@example.com",
        "tester@example.com.evil.test",
        "xtester@example.com",
        "tester+alias@example.com",  # an alias is another address: list it if it should count
        "tester@example.co",
        "@example.com",
        "example.com",
    ],
)
def test_nobody_else_is_unlimited(email: str | None) -> None:
    assert GuardSettings(unlimited_emails="tester@example.com").is_unlimited(email) is False


def test_an_empty_list_makes_nobody_unlimited() -> None:
    guard = GuardSettings()
    assert guard.is_unlimited("tester@example.com") is False
    assert guard.is_unlimited(None) is False


# ───────────────────────── never printed ─────────────────────────


def test_no_address_is_printed_by_repr_str_dump_or_the_startup_summary() -> None:
    s = build(guard=GuardSettings(unlimited_emails="secret.person@example.com,another.one@example.org"))
    text = repr(s) + str(s) + repr(s.guard) + str(s.guard) + repr(s.summary()) + repr(s.model_dump())
    assert "secret.person" not in text and "another.one" not in text
    assert s.summary()["unlimited_accounts"] == 2
    assert build().summary()["unlimited_accounts"] == 0
