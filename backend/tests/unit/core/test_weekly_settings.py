"""The Leafout Weekly's settings group (`TAYYARI_WEEKLY__*`): defaults, the environment, the domain list and the blank sender."""

from __future__ import annotations

import pytest

from tayyari.core.settings import Settings, WeeklySettings


def test_the_defaults() -> None:
    weekly = WeeklySettings()
    assert weekly.enabled is True
    assert weekly.sender is None and weekly.unsubscribe_mailto is None
    assert weekly.app_url == "https://app.leafout.in"
    assert (weekly.news, weekly.news_searches, weekly.news_items) == (True, 3, 3)
    assert weekly.max_paid_editions_per_week == 500
    assert (weekly.batch_size, weekly.max_tries, weekly.tick_seconds) == (10, 3, 900.0)
    assert "techcrunch.com" in weekly.news_domains and "thenewstack.io" in weekly.news_domains
    assert all(domain == domain.lower() and "/" not in domain for domain in weekly.news_domains)


def test_the_environment_sets_the_group(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_WEEKLY__ENABLED", "true")
    monkeypatch.setenv("TAYYARI_WEEKLY__SENDER", "The Leafout Weekly <weekly@leafout.in>")
    monkeypatch.setenv("TAYYARI_WEEKLY__MAX_PAID_EDITIONS_PER_WEEK", "20")
    monkeypatch.setenv("TAYYARI_WEEKLY__NEWS_DOMAINS", "TechCrunch.com, reuters.com;wired.com")
    monkeypatch.setenv("TAYYARI_GUARD__EST_WEEKLY_USD", "0.08")
    settings = Settings(_env_file=None)
    assert settings.weekly.enabled is True
    assert settings.weekly.sender == "The Leafout Weekly <weekly@leafout.in>"
    assert settings.weekly.max_paid_editions_per_week == 20
    assert settings.weekly.news_domains == ["techcrunch.com", "reuters.com", "wired.com"]
    assert settings.guard.est_weekly_usd == 0.08


def test_the_test_environment_switches_the_loop_off_by_default() -> None:
    assert Settings(_env_file=None).weekly.enabled is False


def test_the_domains_may_be_a_json_list_and_blank_entries_vanish() -> None:
    assert WeeklySettings(news_domains='["A.com", "b.com", ""]').news_domains == ["a.com", "b.com"]  # type: ignore[arg-type]
    assert WeeklySettings(news_domains=" ,, ").news_domains == []  # type: ignore[arg-type]
    assert WeeklySettings(news_domains=["x.com", "x.com"]).news_domains == ["x.com"]


def test_a_blank_sender_counts_as_unset() -> None:
    assert WeeklySettings(sender="  ", unsubscribe_mailto="").sender is None
    assert WeeklySettings(sender="  ", unsubscribe_mailto="").unsubscribe_mailto is None


@pytest.mark.parametrize("field", ["news_searches", "news_items", "batch_size", "max_tries"])
def test_counts_have_sane_limits(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        WeeklySettings(**{field: 0})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=field):
        WeeklySettings(**{field: 1000})  # type: ignore[arg-type]
