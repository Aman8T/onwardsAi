"""Discover's storage, run against every backend (the fake, SQLite file and memory, and Postgres when configured), so the fake
cannot drift from the SQL repository: upserts that keep what must be kept, closing only after a board was read, the single
flight of runs, and the retention of closed listings."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tayyari.core.settings import Settings
from tayyari.storage.maintenance import CLOSED_LISTING_RETENTION, run_maintenance
from tayyari.storage.ports import DiscoverListing

if TYPE_CHECKING:
    from tests.unit.storage.conftest import Env

DAY = timedelta(days=1)
STALE = timedelta(minutes=20)


def listing(listing_id: str, employer: str = "acme", **fields: object) -> DiscoverListing:
    values: dict[str, object] = {
        "id": listing_id,
        "employer_id": employer,
        "source": "greenhouse",
        "external_id": f"ext-{listing_id}",
        "title": f"Engineer {listing_id}",
        "title_norm": f"engineer {listing_id}",
        "work_mode": "hybrid",
        "city": "Bengaluru",
        "locality": "Koramangala",
        "lat": 12.935,
        "lng": 77.625,
        "precision": "locality",
        "remote_india": False,
        "location_text": "Bengaluru, India",
        "apply_url": f"https://acme.example/jobs/{listing_id}?gh_jid=1",
        "apply_host": "acme.example",
        "posted_at": datetime(2026, 10, 1, 8, 30, tzinfo=UTC),
        "first_seen_at": datetime(2000, 1, 1, tzinfo=UTC),  # a refresh sets this; storage must not trust it
        "last_seen_at": datetime(2000, 1, 1, tzinfo=UTC),
        "status": "open",
        "closed_at": None,
        "salary_text": "₹28–36 lakh a year",
        "years_min": 3,
        "entry_level": False,
        "content_hash": f"hash-{listing_id}-1",
        "posting_text": f"The full text of {listing_id}.\nReact and TypeScript.",
    }
    values.update(fields)
    return DiscoverListing(**values)  # type: ignore[arg-type]


async def open_ids(env: Env) -> list[str]:
    return sorted(item.id for item in await env.repos.discover.list_open())


# ───────────────────────── listings ─────────────────────────


async def test_an_upsert_inserts_new_listings_with_the_time_they_were_first_seen(env: Env) -> None:
    repo = env.repos.discover
    now = env.clock.now()
    counts = await repo.upsert_employer("acme", [listing("a"), listing("b")], now=now)
    assert (counts.inserted, counts.updated, counts.unchanged) == (2, 0, 0)
    stored = await repo.get("a")
    assert stored is not None
    assert (stored.first_seen_at, stored.last_seen_at, stored.status, stored.closed_at) == (
        now,
        now,
        "open",
        None,
    )
    assert stored.posting_text == "The full text of a.\nReact and TypeScript."
    assert (stored.lat, stored.lng, stored.locality, stored.remote_india) == (
        12.935,
        77.625,
        "Koramangala",
        False,
    )
    assert (
        stored.posted_at == datetime(2026, 10, 1, 8, 30, tzinfo=UTC) and stored.posted_at.tzinfo is not None
    )
    assert (
        stored.salary_text == "₹28–36 lakh a year" and stored.years_min == 3 and stored.entry_level is False
    )


async def test_a_listing_a_board_still_lists_is_seen_again_and_keeps_its_first_seen_time(env: Env) -> None:
    repo = env.repos.discover
    first = env.clock.now()
    await repo.upsert_employer("acme", [listing("a")], now=first)
    env.clock.advance(2 * DAY)
    later = env.clock.now()
    counts = await repo.upsert_employer("acme", [listing("a")], now=later)
    assert (counts.inserted, counts.updated, counts.unchanged) == (0, 0, 1)
    stored = await repo.get("a")
    assert stored is not None and (stored.first_seen_at, stored.last_seen_at) == (first, later)


async def test_a_changed_listing_gets_its_new_content_and_keeps_its_first_seen_time(env: Env) -> None:
    repo = env.repos.discover
    first = env.clock.now()
    await repo.upsert_employer("acme", [listing("a")], now=first)
    env.clock.advance(DAY)
    counts = await repo.upsert_employer(
        "acme",
        [
            listing(
                "a",
                title="Senior Engineer a",
                content_hash="hash-a-2",
                posting_text="New text.",
                salary_text=None,
            )
        ],
        now=env.clock.now(),
    )
    assert (counts.inserted, counts.updated, counts.unchanged) == (0, 1, 0)
    stored = await repo.get("a")
    assert stored is not None
    assert (stored.title, stored.posting_text, stored.salary_text) == ("Senior Engineer a", "New text.", None)
    assert stored.first_seen_at == first and stored.last_seen_at == env.clock.now()


async def test_the_lists_leave_out_the_text_and_only_open_listings_have_text_to_read(env: Env) -> None:
    repo = env.repos.discover
    await repo.upsert_employer("acme", [listing("a"), listing("b")], now=env.clock.now())
    assert all(item.posting_text is None for item in await repo.list_open())
    assert await repo.texts(["a", "b", "nope"]) == {
        "a": ("hash-a-1", "The full text of a.\nReact and TypeScript."),
        "b": ("hash-b-1", "The full text of b.\nReact and TypeScript."),
    }
    assert await repo.texts([]) == {}
    await repo.close_missing("acme", ["a"], now=env.clock.now())
    assert set(await repo.texts(["a", "b"])) == {"a"}


async def test_close_missing_closes_what_the_board_no_longer_lists_and_only_for_that_employer(
    env: Env,
) -> None:
    repo = env.repos.discover
    now = env.clock.now()
    await repo.upsert_employer("acme", [listing("a"), listing("b")], now=now)
    await repo.upsert_employer("other", [listing("c", "other")], now=now)
    env.clock.advance(DAY)
    closed = await repo.close_missing("acme", ["a"], now=env.clock.now())
    assert closed == 1
    assert await open_ids(env) == ["a", "c"]
    gone = await repo.get("b")
    assert gone is not None and gone.status == "closed" and gone.closed_at == env.clock.now()
    assert await repo.close_missing("acme", ["a"], now=env.clock.now()) == 0  # already closed: nothing more


async def test_a_board_that_lists_nothing_closes_all_of_that_employers_listings(env: Env) -> None:
    repo = env.repos.discover
    await repo.upsert_employer("acme", [listing("a"), listing("b")], now=env.clock.now())
    await repo.upsert_employer("other", [listing("c", "other")], now=env.clock.now())
    assert await repo.close_missing("acme", [], now=env.clock.now()) == 2
    assert await open_ids(env) == ["c"]


async def test_a_listing_that_comes_back_is_open_again(env: Env) -> None:
    repo = env.repos.discover
    first = env.clock.now()
    await repo.upsert_employer("acme", [listing("a")], now=first)
    env.clock.advance(DAY)
    await repo.close_missing("acme", [], now=env.clock.now())
    env.clock.advance(DAY)
    await repo.upsert_employer("acme", [listing("a")], now=env.clock.now())
    back = await repo.get("a")
    assert back is not None and (back.status, back.closed_at, back.first_seen_at) == ("open", None, first)
    assert await open_ids(env) == ["a"]


async def test_an_employer_that_left_the_registry_is_closed(env: Env) -> None:
    repo = env.repos.discover
    await repo.upsert_employer("acme", [listing("a")], now=env.clock.now())
    await repo.upsert_employer("old", [listing("b", "old")], now=env.clock.now())
    assert await repo.close_employers_except(["acme"], now=env.clock.now()) == 1
    assert await open_ids(env) == ["a"]
    assert await repo.close_employers_except([], now=env.clock.now()) == 1
    assert await open_ids(env) == []


async def test_counts_are_employers_with_an_open_listing_and_open_listings(env: Env) -> None:
    repo = env.repos.discover
    assert await repo.counts() == (0, 0)
    await repo.upsert_employer("acme", [listing("a"), listing("b")], now=env.clock.now())
    await repo.upsert_employer("other", [listing("c", "other")], now=env.clock.now())
    assert await repo.counts() == (2, 3)
    await repo.close_missing("other", [], now=env.clock.now())
    assert await repo.counts() == (1, 2)


async def test_an_empty_upsert_does_nothing(env: Env) -> None:
    counts = await env.repos.discover.upsert_employer("acme", [], now=env.clock.now())
    assert (counts.inserted, counts.updated, counts.unchanged) == (0, 0, 0)


async def test_an_unknown_listing_is_none(env: Env) -> None:
    assert await env.repos.discover.get("nope") is None


async def test_closed_listings_are_deleted_a_month_after_they_closed(env: Env) -> None:
    repo = env.repos.discover
    now = env.clock.now()
    await repo.upsert_employer("acme", [listing("old"), listing("recent"), listing("open")], now=now)
    await repo.close_missing("acme", ["recent", "open"], now=now - 40 * DAY)  # "old" closed 40 days ago
    await repo.close_missing("acme", ["open"], now=now - 5 * DAY)  # "recent" closed 5 days ago
    deleted = await repo.purge_closed(now - CLOSED_LISTING_RETENTION)
    assert deleted == 1
    assert (
        await repo.get("old") is None
        and await repo.get("recent") is not None
        and await repo.get("open") is not None
    )


async def test_the_retention_job_purges_closed_listings(env: Env) -> None:
    if env.db is None:
        pytest.skip("the retention job runs against a database")
    repo = env.repos.discover
    now = env.clock.now()
    await repo.upsert_employer("acme", [listing("old"), listing("open")], now=now)
    await repo.close_missing("acme", ["open"], now=now - (CLOSED_LISTING_RETENTION + DAY))
    counts = await run_maintenance(env.db, Settings(_env_file=None, env="test"), now)
    assert counts["discover_closed"] == 1 and counts["errors"] == 0
    assert await open_ids(env) == ["open"] and await repo.get("old") is None


# ───────────────────────── runs ─────────────────────────


async def test_a_run_is_recorded_and_closed_with_its_outcome(env: Env) -> None:
    repo = env.repos.discover
    assert (
        await repo.last_ok_run() is None
        and await repo.first_run() is None
        and await repo.latest_run() is None
    )
    started = env.clock.now()
    run = await repo.start_run(now=started, stale_after=STALE)
    assert (
        run is not None and run.status == "running" and run.started_at == started and run.finished_at is None
    )
    env.clock.advance(timedelta(minutes=2))
    await repo.finish_run(
        run.id,
        now=env.clock.now(),
        status="ok",
        employers_ok=8,
        employers_failed=1,
        listings_open=17,
        listings_closed=2,
        note="1 of 9 boards could not be read.",
    )
    done = await repo.last_ok_run()
    assert done is not None
    assert (done.id, done.status, done.employers_ok, done.employers_failed) == (run.id, "ok", 8, 1)
    assert (done.listings_open, done.listings_closed, done.note) == (
        17,
        2,
        "1 of 9 boards could not be read.",
    )
    assert done.finished_at == env.clock.now() and done.started_at == started


async def test_only_one_run_is_in_flight_at_a_time(env: Env) -> None:
    repo = env.repos.discover
    now = env.clock.now()
    first = await repo.start_run(now=now, stale_after=STALE)
    assert first is not None
    assert await repo.start_run(now=now + timedelta(minutes=5), stale_after=STALE) is None
    assert await asyncio.gather(*(repo.start_run(now=now, stale_after=STALE) for _ in range(5))) == [None] * 5
    await repo.finish_run(
        first.id,
        now=now,
        status="ok",
        employers_ok=1,
        employers_failed=0,
        listings_open=0,
        listings_closed=0,
        note=None,
    )
    second = await repo.start_run(now=now + timedelta(minutes=6), stale_after=STALE)
    assert second is not None and second.id != first.id


async def test_two_callers_at_once_start_exactly_one_run(env: Env) -> None:
    repo = env.repos.discover
    now = env.clock.now()
    started = await asyncio.gather(*(repo.start_run(now=now, stale_after=STALE) for _ in range(6)))
    assert len([run for run in started if run is not None]) == 1


async def test_a_run_that_never_finished_stops_blocking_after_twenty_minutes(env: Env) -> None:
    repo = env.repos.discover
    now = env.clock.now()
    dead = await repo.start_run(now=now, stale_after=STALE)
    assert dead is not None
    later = now + STALE + timedelta(seconds=1)
    fresh = await repo.start_run(now=later, stale_after=STALE)
    assert fresh is not None and fresh.id != dead.id
    first = await repo.first_run()
    assert (
        first is not None and first.id == dead.id and first.status == "failed"
    )  # the dead run is marked failed
    assert (await repo.latest_run() or dead).id == fresh.id


async def test_the_runs_are_found_oldest_newest_and_last_good(env: Env) -> None:
    repo = env.repos.discover
    ids = []
    for status in ("ok", "failed", "ok", "failed"):
        run = await repo.start_run(now=env.clock.now(), stale_after=STALE)
        assert run is not None
        await repo.finish_run(
            run.id, now=env.clock.now(), status=status, employers_ok=1, employers_failed=0,
            listings_open=0, listings_closed=0, note=None,
        )  # fmt: skip
        ids.append(run.id)
        env.clock.advance(timedelta(hours=1))
    first, latest, last_ok = await repo.first_run(), await repo.latest_run(), await repo.last_ok_run()
    assert first is not None and latest is not None and last_ok is not None
    assert (first.id, latest.id, last_ok.id) == (ids[0], ids[3], ids[2])


def test_a_listing_is_a_plain_value() -> None:
    first = listing("a")
    assert dataclasses.replace(first, title="x").title == "x" and first.title != "x"
