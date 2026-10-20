"""The tips and quotes (written in code, rotated by issue number) and the India clock the schedule runs on."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tayyari.weekly.copy import QUOTES, TIPS, quote_for, tip_for
from tayyari.weekly.times import days_due, in_india, iso_date, week_of

# ───────────────────────── copy ─────────────────────────


def test_there_are_enough_of_each() -> None:
    assert len(TIPS) >= 8 and len(QUOTES) >= 8
    assert len(set(TIPS)) == len(TIPS) and len(set(QUOTES)) == len(QUOTES)


def test_the_tip_and_the_quote_rotate_by_issue_and_wrap() -> None:
    assert tip_for(1) == TIPS[0] and tip_for(2) == TIPS[1]
    assert tip_for(len(TIPS) + 1) == TIPS[0]
    assert quote_for(1) == QUOTES[0] and quote_for(len(QUOTES) + 1) == QUOTES[0]
    assert tip_for(0) == TIPS[0]  # never a negative or zero index


def test_no_quote_names_a_person() -> None:
    for text in QUOTES:
        assert "—" not in text and " - " not in text and "~" not in text
        assert text.endswith(".") and text[0].isupper()


def test_no_tip_carries_a_score_or_a_percentage() -> None:
    for text in TIPS:
        assert "%" not in text and "percent" not in text.lower() and "score" not in text.lower()


# ───────────────────────── times ─────────────────────────


def at(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_india_is_five_and_a_half_hours_ahead() -> None:
    assert (in_india(at(2026, 10, 19, 0, 30)).hour, in_india(at(2026, 10, 19, 0, 30)).minute) == (6, 0)
    assert in_india(datetime(2026, 10, 19, 0, 30)).hour == 6  # a naive value is UTC


@pytest.mark.parametrize(
    ("when", "day", "expected"),
    [
        (at(2026, 10, 19, 1, 0), "mon", date(2026, 10, 19)),
        (at(2026, 10, 19, 1, 0), "fri", date(2026, 10, 23)),
        (at(2026, 10, 25, 18, 0), "mon", date(2026, 10, 19)),  # Sunday evening UTC is still that India week
        (at(2026, 10, 25, 19, 0), "mon", date(2026, 10, 26)),  # Monday 00:30 in India: the next week
        (at(2026, 10, 23, 5, 0), "fri", date(2026, 10, 23)),
    ],
)
def test_the_week_of_is_the_monday_or_friday_of_the_india_week(
    when: datetime, day: str, expected: date
) -> None:
    assert week_of(when, day) == expected


def test_the_send_starts_at_six_in_the_morning_india_time() -> None:
    assert days_due(at(2026, 10, 18, 23, 59)) == []  # Monday 05:29 IST
    assert days_due(at(2026, 10, 19, 0, 29)) == []  # 05:59 IST
    assert days_due(at(2026, 10, 19, 0, 30)) == ["mon"]  # 06:00 IST
    assert days_due(at(2026, 10, 19, 17, 0)) == ["mon"]  # still Monday in India at 22:30
    assert days_due(at(2026, 10, 19, 18, 30)) == []  # Tuesday 00:00 IST
    assert days_due(at(2026, 10, 20, 6, 0)) == []
    assert days_due(at(2026, 10, 23, 1, 0)) == ["fri"]


def test_the_india_date_of_a_moment() -> None:
    assert iso_date(at(2026, 10, 18, 20, 0)) == "2026-10-19"
