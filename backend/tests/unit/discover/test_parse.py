"""Reading a Greenhouse or Lever board into listings, from the hand-written fixtures (shaped like the real APIs)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tayyari.discover.errors import BoardError
from tayyari.discover.parse import (
    ParsedBoard,
    format_salary,
    parse_board,
    parse_time,
    work_mode,
)
from tayyari.discover.registry import Employer, Office
from tayyari.storage.ports import DiscoverListing
from tests.fakes.discover import NOW, fixture_registry, read_json

CAP = 300


def employer(employer_id: str) -> Employer:
    found = fixture_registry().get(employer_id)
    assert found is not None
    return found


def board(employer_id: str, name: str, cap: int = CAP) -> ParsedBoard:
    return parse_board(employer(employer_id), read_json(name), now=NOW, cap=cap)


def by_title(parsed: ParsedBoard, title: str) -> DiscoverListing:
    (found,) = [item for item in parsed.listings if item.title == title]
    return found


# ───────────────────────── Greenhouse ─────────────────────────


def test_a_greenhouse_board_keeps_only_listings_in_india_that_can_be_shown() -> None:
    parsed = board("kestrel", "greenhouse-kestrelpay.json")
    assert parsed.seen == 9
    assert sorted(item.external_id for item in parsed.listings) == ["1001", "1002", "1003", "1004", "1006"]
    # 1005 is remote in the United States, 1009 is in Berlin, 1007 has no link, 1008 repeats 1001.
    assert parsed.skipped == 1  # the Indian posting with no link
    assert parsed.dropped == 0 and parsed.india == 5


def test_a_listing_is_placed_by_its_location_text_and_the_one_locality_the_registry_lists() -> None:
    parsed = board("kestrel", "greenhouse-kestrelpay.json")
    senior = by_title(parsed, "Senior Frontend Engineer")
    # "Bangalore, India" names the city; Kestrel lists exactly one Bengaluru office, in Koramangala.
    assert (senior.city, senior.locality, senior.precision) == ("Bengaluru", "Koramangala", "locality")
    assert senior.lat is not None and senior.lng is not None and not senior.remote_india
    intern = by_title(parsed, "Software Engineer Intern")
    # Kestrel's Pune office has no locality: the listing is placed in the city and never in a guessed locality.
    assert (intern.city, intern.locality, intern.precision, intern.lat is not None) == (
        "Pune",
        None,
        "city",
        True,
    )


def test_a_locality_named_in_the_text_wins_over_the_registry() -> None:
    quill = board("quill", "greenhouse-quillandco.json")
    (only,) = quill.listings
    assert (only.city, only.locality, only.precision, only.work_mode) == ("Bengaluru", None, "city", "hybrid")
    payload = {"jobs": [{"id": 7, "title": "Engineer", "location": {"name": "Whitefield, Bangalore"},
                         "absolute_url": "https://q.example/7", "content": "React"}]}  # fmt: skip
    (named,) = parse_board(employer("quill"), payload, now=NOW, cap=CAP).listings
    assert (named.locality, named.precision) == ("Whitefield", "locality")


def test_a_remote_role_that_names_india_is_open_to_india_and_has_no_city() -> None:
    remote = by_title(board("kestrel", "greenhouse-kestrelpay.json"), "Frontend Engineer")
    assert remote.remote_india and remote.work_mode == "remote"
    assert (remote.city, remote.locality, remote.precision, remote.lat) == (None, None, "country", None)


def test_remote_alone_is_not_enough() -> None:
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Engineer",
                "location": {"name": "Remote"},
                "absolute_url": "https://a.example/1",
            }
        ]
    }
    assert parse_board(employer("kestrel"), payload, now=NOW, cap=CAP).listings == []


def test_the_apply_link_loses_its_tracking_and_keeps_what_the_employer_needs() -> None:
    parsed = board("kestrel", "greenhouse-kestrelpay.json")
    senior = by_title(parsed, "Senior Frontend Engineer")
    assert senior.apply_url == "https://kestrelpay.in/careers/jobs/1001?gh_jid=1001&ref=friend"
    assert senior.apply_host == "kestrelpay.in"
    assert all(item.apply_url.startswith("https://") and item.apply_host for item in parsed.listings)


def test_a_repeated_title_in_one_city_keeps_the_most_recently_posted() -> None:
    parsed = board("kestrel", "greenhouse-kestrelpay.json")
    seniors = [item for item in parsed.listings if item.title == "Senior Frontend Engineer"]
    assert [item.external_id for item in seniors] == ["1001"]


def test_the_text_is_plain_cut_and_keeps_a_posting_inert() -> None:
    parsed = board("kestrel", "greenhouse-kestrelpay.json")
    senior = by_title(parsed, "Senior Frontend Engineer")
    text = senior.posting_text or ""
    assert text.startswith("Senior Frontend Engineer\nKestrel Pay · Bangalore, India")
    assert "<" not in text and "&lt;" not in text and "- 5+ years of experience building web apps" in text
    assert len(text) <= 20_000
    # An instruction inside a posting is only text: it is stored as written and nothing acts on it.
    fullstack = by_title(parsed, "Full-stack Engineer").posting_text or ""
    assert "Ignore all previous instructions" in fullstack


def test_years_and_entry_level_are_read_from_the_posting() -> None:
    parsed = board("kestrel", "greenhouse-kestrelpay.json")
    assert by_title(parsed, "Senior Frontend Engineer").years_min == 5
    assert by_title(parsed, "Staff Frontend Engineer").years_min == 8
    assert by_title(parsed, "Software Engineer Intern").entry_level
    assert not by_title(parsed, "Senior Frontend Engineer").entry_level
    trainee = by_title(board("nilgiri", "greenhouse-nilgirimills.json"), "Accounts Trainee")
    assert (trainee.years_min, trainee.entry_level) == (0, True)


def test_freshness_inputs_are_the_employers_date_when_it_gives_one() -> None:
    parsed = board("kestrel", "greenhouse-kestrelpay.json")
    assert by_title(parsed, "Senior Frontend Engineer").posted_at == datetime(2026, 10, 13, 12, tzinfo=UTC)
    assert by_title(parsed, "Full-stack Engineer").posted_at is None  # Greenhouse gave none: never invented
    assert all(item.first_seen_at == NOW and item.status == "open" for item in parsed.listings)


def test_a_greenhouse_listing_never_has_a_salary() -> None:
    assert all(item.salary_text is None for item in board("kestrel", "greenhouse-kestrelpay.json").listings)


# ───────────────────────── Lever ─────────────────────────


def test_a_lever_board_reads_place_mode_salary_and_text() -> None:
    parsed = board("northwind", "lever-northwind.json")
    assert sorted(item.title for item in parsed.listings) == [
        "Customer Support Associate",
        "Data Analyst",
        "Frontend Engineer II",
        "Senior Frontend Engineer",
    ]  # the New York role is not in India
    frontend = by_title(parsed, "Frontend Engineer II")
    assert (frontend.city, frontend.locality, frontend.precision) == ("Bengaluru", "HSR Layout", "locality")
    assert frontend.work_mode == "onsite" and frontend.source == "lever"
    assert frontend.salary_text == "₹28–36 lakh a year"
    assert frontend.apply_url == "https://jobs.lever.co/northwind/11111111-aaaa-4aaa-8aaa-000000000001"
    assert frontend.apply_host == "jobs.lever.co"
    assert frontend.posted_at == datetime(2026, 10, 7, 12, tzinfo=UTC)
    assert frontend.years_min == 3
    text = frontend.posting_text or ""
    assert (
        "Northwind builds analytics for retailers." in text
        and "- 3+ years of experience with React and Jest" in text
    )
    assert "What you'll need:" in text and "equal opportunity" in text


def test_lever_workplace_type_decides_the_mode_and_a_dollar_salary_reads_as_stated() -> None:
    parsed = board("northwind", "lever-northwind.json")
    analyst = by_title(parsed, "Data Analyst")
    assert (analyst.work_mode, analyst.city, analyst.locality, analyst.precision) == (
        "hybrid",
        "Bengaluru",
        None,
        "city",
    )
    remote = by_title(parsed, "Senior Frontend Engineer")
    assert remote.remote_india and remote.work_mode == "remote" and remote.salary_text == "$40–55k a year"
    assert by_title(parsed, "Customer Support Associate").salary_text is None


def test_a_lever_listing_with_country_in_and_a_bare_remote_location_is_open_to_india() -> None:
    (only,) = board("lattice", "lever-latticeloom.json").listings
    assert only.remote_india and only.title == "Frontend Engineer"


# ───────────────────────── salary ─────────────────────────


@pytest.mark.parametrize(
    ("salary", "text"),
    [
        (
            {"currency": "INR", "interval": "per-year-salary", "min": 2800000, "max": 3600000},
            "₹28–36 lakh a year",
        ),
        (
            {"currency": "INR", "interval": "per-year-salary", "min": 2850000, "max": 3600000},
            "₹28.5–36 lakh a year",
        ),
        (
            {"currency": "INR", "interval": "per-year-salary", "min": 3000000, "max": 3000000},
            "₹30 lakh a year",
        ),
        (
            {"currency": "INR", "interval": "per-month-salary", "min": 80000, "max": 120000},
            "₹80,000–1,20,000 a month",
        ),
        ({"currency": "USD", "interval": "per-year-salary", "min": 40000, "max": 55000}, "$40–55k a year"),
        ({"currency": "USD", "interval": "per-hour-wage", "min": 25, "max": 40}, "$25–40 an hour"),
        ({"currency": "GBP", "interval": "per-year-salary", "min": 40000, "max": 55000}, "£40–55k a year"),
        (
            {"currency": "CHF", "interval": "per-year-salary", "min": 90000, "max": 110000},
            "CHF 90–110k a year",
        ),
        ({"currency": "INR", "interval": "per-year-salary", "min": 2800000}, "From ₹28 lakh a year"),
        ({"currency": "INR", "interval": "per-year-salary", "max": 3600000}, "Up to ₹36 lakh a year"),
    ],
)
def test_salary_is_worded_the_way_the_employer_states_it(salary: dict[str, Any], text: str) -> None:
    assert format_salary(salary) == text


@pytest.mark.parametrize("salary", [None, {}, {"currency": "INR"}, {"min": 0, "max": 0}, "₹30 lakh", [1, 2]])
def test_no_stated_salary_is_no_salary(salary: object) -> None:
    assert format_salary(salary) is None


# ───────────────────────── the cap ─────────────────────────


def _job(index: int, location: str, *, posted: str | None, mode_in_title: str = "") -> dict[str, Any]:
    job: dict[str, Any] = {
        "id": 9000 + index,
        "title": f"Field Executive {index} {mode_in_title}".strip(),
        "updated_at": "2026-10-01T00:00:00Z",
        "location": {"name": location},
        "absolute_url": f"https://big.example/jobs/{index}",
        "content": "&lt;p&gt;Sales.&lt;/p&gt;",
    }
    if posted:
        job["first_published"] = posted
    return job


def test_a_board_over_the_cap_keeps_known_cities_first_then_remote_india_then_the_rest_newest_first() -> None:
    big = Employer("big", "Big Co", "greenhouse", "big", "https://big.example", (Office("Pune"),))
    jobs = [
        _job(1, "Lucknow, India", posted="2026-10-15T00:00:00Z"),  # the rest, newest
        _job(2, "Remote - India", posted="2026-10-01T00:00:00Z"),  # remote India
        _job(3, "Pune, India", posted="2026-09-01T00:00:00Z"),  # known city, oldest
        _job(4, "Mumbai", posted="2026-10-10T00:00:00Z"),  # known city, newer
        _job(5, "Patna, India", posted="2026-10-14T00:00:00Z"),  # the rest
        _job(
            6, "Remote - India", posted="2026-10-12T00:00:00Z"
        ),  # remote India, newer (titles differ: both kept)
    ]
    parsed = parse_board(big, {"jobs": jobs}, now=NOW, cap=4)
    assert [item.external_id for item in parsed.listings] == ["9004", "9003", "9006", "9002"]
    assert (parsed.india, parsed.dropped) == (6, 2)  # Lucknow and Patna are the ones left out
    everything = parse_board(big, {"jobs": jobs}, now=NOW, cap=100)
    assert {item.external_id for item in everything.listings} == {str(9000 + n) for n in range(1, 7)}


def test_an_india_listing_in_a_town_we_do_not_know_is_kept_without_a_city() -> None:
    big = Employer("big", "Big Co", "greenhouse", "big", "https://big.example", ())
    parsed = parse_board(big, {"jobs": [_job(1, "Lucknow, India", posted=None)]}, now=NOW, cap=10)
    (only,) = parsed.listings
    assert (only.city, only.precision, only.remote_india) == (None, "country", False)


# ───────────────────────── small readers and bad boards ─────────────────────────


def test_times_are_read_as_utc() -> None:
    assert parse_time("2026-10-13T12:00:00Z") == datetime(2026, 10, 13, 12, tzinfo=UTC)
    assert parse_time("2026-10-15T08:00:00-04:00") == datetime(2026, 10, 15, 12, tzinfo=UTC)
    assert parse_time("2026-10-13T12:00:00") == datetime(2026, 10, 13, 12, tzinfo=UTC)
    assert parse_time(1791374400000) == datetime(2026, 10, 7, 12, tzinfo=UTC)
    for bad in (None, "", "soon", True, [], 10**30):
        assert parse_time(bad) is None


@pytest.mark.parametrize(
    ("declared", "texts", "mode"),
    [
        ("on-site", ("Remote",), "onsite"),
        ("hybrid", (), "hybrid"),
        ("remote", (), "remote"),
        ("unspecified", ("Pune (Hybrid)",), "hybrid"),
        (None, ("Remote - India",), "remote"),
        (None, ("Bengaluru", "Engineer"), "unknown"),
        (None, ("Work from home",), "remote"),
    ],
)
def test_work_mode(declared: object, texts: tuple[str, ...], mode: str) -> None:
    assert work_mode(declared, *texts) == mode


def test_a_board_of_the_wrong_shape_is_an_error_not_an_empty_board() -> None:
    with pytest.raises(BoardError):
        parse_board(employer("kestrel"), [], now=NOW, cap=CAP)
    with pytest.raises(BoardError):
        parse_board(employer("kestrel"), {"jobs": "none"}, now=NOW, cap=CAP)
    with pytest.raises(BoardError):
        parse_board(employer("northwind"), {"jobs": []}, now=NOW, cap=CAP)


def test_junk_entries_in_a_board_are_skipped_without_failing_it() -> None:
    payload = {"jobs": [None, 5, "x", {"id": 1}, {"title": "T", "absolute_url": "https://a.example/x"}]}
    parsed = parse_board(employer("kestrel"), payload, now=NOW, cap=CAP)
    assert parsed.listings == [] and parsed.seen == 5
