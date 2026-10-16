"""Freshness: where it counts from, and the words for it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tayyari.discover.freshness import (
    UNKNOWN_FRESHNESS,
    age_from,
    checked_label,
    first_run_end,
    freshness_label,
)

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("days", "label"),
    [
        (0, "Posted today"),
        (1, "Posted yesterday"),
        (2, "Posted 2 days ago"),
        (14, "Posted 14 days ago"),
        (15, "Open 2 weeks"),
        (28, "Open 4 weeks"),
        (45, "Open 6 weeks"),
        (46, "Worth checking it's still hiring"),
        (90, "Worth checking it's still hiring"),
        (91, "Open over 3 months"),
        (400, "Open over 3 months"),
    ],
)
def test_the_words_for_an_age(days: int, label: str) -> None:
    assert freshness_label(NOW, NOW - timedelta(days=days)) == label


def test_an_unknown_age_says_only_that_the_listing_is_on_the_board() -> None:
    assert freshness_label(NOW, None) == UNKNOWN_FRESHNESS == "On the employer's board now"


def test_a_date_in_the_future_reads_as_today() -> None:
    assert freshness_label(NOW, NOW + timedelta(days=3)) == "Posted today"


def test_the_employers_date_wins_and_a_first_seen_date_counts_only_after_the_first_refresh() -> None:
    posted = NOW - timedelta(days=30)
    seen = NOW - timedelta(days=2)
    first_run_ended = NOW - timedelta(days=10)
    assert age_from(posted, seen, first_run_ended) == posted
    assert age_from(None, seen, first_run_ended) == seen  # seen after the first run: it appeared later
    assert (
        age_from(None, NOW - timedelta(days=20), first_run_ended) is None
    )  # found by the first run: says nothing
    assert age_from(None, first_run_ended, first_run_ended) is None  # not later than it
    assert age_from(None, seen, None) is None  # no run at all


def test_the_first_run_is_over_when_it_finished_or_would_count_as_abandoned() -> None:
    started = NOW - timedelta(hours=1)
    assert first_run_end(started, NOW, timedelta(minutes=20)) == NOW
    assert first_run_end(started, None, timedelta(minutes=20)) == started + timedelta(minutes=20)


@pytest.mark.parametrize(
    ("days", "label"),
    [
        (0, "Employer's own listing, checked today"),
        (1, "Employer's own listing, checked yesterday"),
        (5, "Employer's own listing, checked 5 days ago"),
    ],
)
def test_the_source_line_says_when_the_board_was_last_read(days: int, label: str) -> None:
    assert checked_label(NOW, NOW - timedelta(days=days)) == label
