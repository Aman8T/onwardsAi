"""Reading the boards into the store: what is fetched (and what never is), single flight, and a failed board never closing
anything. No network: the transport is `FakeBoards`, which serves hand-written Greenhouse and Lever JSON."""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import httpx2
import pytest

from tayyari.core.settings import DiscoverSettings
from tayyari.discover.refresh import USER_AGENT, Refresher, board_url
from tayyari.discover.registry import EMPTY, Employer, Office, Registry
from tests.fakes.clock import FakeClock
from tests.fakes.discover import FakeBoards, FakeDiscoverRepo, fixture_registry, read_json
from tests.unit.discover.conftest import BOARD_FILES

# The registry has 9 employers; one (goneco) is not served.
SERVED_PER_BOARD = {
    "kestrelpay": 5,
    "northwind": 4,
    "quillandco": 1,
    "indigogrid": 1,
    "parcelpoint": 2,
    "latticeloom": 1,
    "nilgirimills": 2,
    "kovaiauto": 1,
}


def refresher(repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock, **settings: object) -> Refresher:
    return Refresher(
        repo=repo,
        settings=DiscoverSettings(**settings),
        clock=clock,
        transport=boards.transport,  # type: ignore[arg-type]
    )


async def test_a_refresh_reads_every_board_and_stores_the_india_listings(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    report = await refresher(repo, boards, clock).run(fixture_registry())
    assert report is not None and report.status == "ok"
    assert (report.employers_ok, report.employers_failed) == (8, 1)
    assert report.listings_open == sum(SERVED_PER_BOARD.values()) == 17 and report.inserted == 17
    assert report.failures == {"goneco": "the board was not found"}
    per_employer: dict[str, int] = {}
    for item in repo.listings.values():
        per_employer[item.employer_id] = per_employer.get(item.employer_id, 0) + 1
    assert per_employer == {
        "kestrel": 5,
        "northwind": 4,
        "quill": 1,
        "indigo": 1,
        "parcelpoint": 2,
        "lattice": 1,
        "nilgiri": 2,
        "kovai": 1,
    }
    run = await repo.last_ok_run()
    assert run is not None and (run.employers_ok, run.employers_failed, run.listings_open) == (8, 1, 17)
    assert run.note == "1 of 9 boards could not be read."


async def test_only_the_public_board_urls_of_registered_employers_are_fetched(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    await refresher(repo, boards, clock).run(fixture_registry())
    assert sorted(boards.urls) == sorted(
        [
            "https://boards-api.greenhouse.io/v1/boards/kestrelpay/jobs?content=true",
            "https://boards-api.greenhouse.io/v1/boards/quillandco/jobs?content=true",
            "https://boards-api.greenhouse.io/v1/boards/parcelpoint/jobs?content=true",
            "https://boards-api.greenhouse.io/v1/boards/nilgirimills/jobs?content=true",
            "https://boards-api.greenhouse.io/v1/boards/goneco/jobs?content=true",
            "https://api.lever.co/v0/postings/northwind?mode=json",
            "https://api.lever.co/v0/postings/indigogrid?mode=json",
            "https://api.lever.co/v0/postings/latticeloom?mode=json",
            "https://api.lever.co/v0/postings/kovaiauto?mode=json",
        ]
    )
    assert all(request.method == "GET" for request in boards.requests)
    # Nothing inside a posting is ever opened: the fixture holds an instruction and a link to a hostile host.
    assert not any("evil.example" in url for url in boards.urls)
    stored = " ".join(item.posting_text or "" for item in repo.listings.values())
    assert "evil.example" in stored  # kept as text, not followed


async def test_requests_say_who_is_asking_and_carry_no_credentials(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    await refresher(repo, boards, clock).run(fixture_registry())
    assert {request.headers["user-agent"] for request in boards.requests} == {USER_AGENT}
    assert USER_AGENT == "Leafout/1.0 (+https://app.leafout.in) job discovery"
    for request in boards.requests:
        assert "authorization" not in request.headers and "cookie" not in request.headers


def test_a_board_token_cannot_escape_its_place_in_the_url() -> None:
    odd = Employer("x", "X", "greenhouse", "a/b?c", "https://x.example", ())
    assert board_url(odd) == "https://boards-api.greenhouse.io/v1/boards/a%2Fb%3Fc/jobs?content=true"


async def test_a_failed_board_never_closes_that_employers_listings(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    run = refresher(repo, boards, clock)
    await run.run(fixture_registry())
    before = {k: v for k, v in repo.listings.items() if v.employer_id == "kestrel"}
    assert len(before) == 5
    clock.advance(6 * 3600)
    boards.fail("kestrelpay", 500, 500)  # the retry fails too
    report = await run.run(fixture_registry())
    assert report is not None and report.status == "ok" and "kestrel" in report.failures
    assert report.failures["kestrel"] == "the board answered 500"
    after = {k: v for k, v in repo.listings.items() if v.employer_id == "kestrel"}
    assert set(after) == set(before) and all(item.status == "open" for item in after.values())
    # Their "last seen" did not move, so the card keeps saying how long ago they were checked.
    assert all(item.last_seen_at == before[k].last_seen_at for k, item in after.items())


async def test_a_listing_the_board_stopped_listing_is_closed_when_the_board_was_read(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    run = refresher(repo, boards, clock)
    await run.run(fixture_registry())
    clock.advance(6 * 3600)
    kept = read_json("greenhouse-kestrelpay.json")
    kept["jobs"] = [job for job in kept["jobs"] if job["id"] != 1002]
    boards.serve("kestrelpay", kept)
    report = await run.run(fixture_registry())
    assert report is not None and report.listings_closed == 1
    closed = [item for item in repo.listings.values() if item.status == "closed"]
    assert [item.title for item in closed] == ["Staff Frontend Engineer"]
    assert closed[0].closed_at == clock.now()


async def test_an_employer_that_left_the_registry_is_closed(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    run = refresher(repo, boards, clock)
    await run.run(fixture_registry())
    smaller = Registry(1, "", tuple(e for e in fixture_registry().employers if e.id != "kovai"))
    clock.advance(6 * 3600)
    await run.run(smaller)
    assert [i.title for i in repo.listings.values() if i.status == "closed"] == ["Audit Assistant"]


async def test_a_timeout_or_a_server_error_is_tried_once_more(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    boards.fail("kestrelpay", "timeout")
    boards.fail("northwind", 503)
    boards.fail("quillandco", 429)
    report = await refresher(repo, boards, clock).run(fixture_registry())
    assert report is not None and set(report.failures) == {"goneco"}
    asked = [request.url.path for request in boards.requests]
    assert asked.count("/v1/boards/kestrelpay/jobs") == 2 and asked.count("/v0/postings/northwind") == 2
    assert clock.sleeps  # a pause between the two tries


async def test_a_missing_board_or_a_client_error_is_not_retried(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    boards.fail("kestrelpay", 403)
    report = await refresher(repo, boards, clock).run(fixture_registry())
    assert report is not None and report.failures["kestrel"] == "the board answered 403"
    assert report.failures["goneco"] == "the board was not found"
    assert [r.url.path for r in boards.requests].count("/v1/boards/kestrelpay/jobs") == 1


@pytest.mark.parametrize(
    ("script", "why"),
    [
        (("garbage",), "the board did not answer with JSON"),
        (("timeout", "timeout"), "the board timed out"),
    ],
)
async def test_what_goes_wrong_with_a_board_is_a_short_sentence_never_a_response_body(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock, script: tuple[str, ...], why: str
) -> None:
    boards.fail("kestrelpay", *script)
    report = await refresher(repo, boards, clock).run(fixture_registry())
    assert report is not None and report.failures["kestrel"] == why


async def test_a_board_of_the_wrong_shape_is_a_failure_not_an_empty_board(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    run = refresher(repo, boards, clock)
    await run.run(fixture_registry())
    boards.serve("kestrelpay", {"error": "maintenance"})
    clock.advance(6 * 3600)
    report = await run.run(fixture_registry())
    assert report is not None and report.failures["kestrel"] == "the board did not list jobs"
    assert len([i for i in repo.listings.values() if i.employer_id == "kestrel" and i.status == "open"]) == 5


async def test_a_second_run_while_one_is_in_flight_does_nothing(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    held = await repo.start_run(now=clock.now(), stale_after=timedelta(minutes=20))
    assert held is not None
    assert await refresher(repo, boards, clock).run(fixture_registry()) is None
    assert boards.requests == [] and repo.listings == {}


async def test_a_dead_run_does_not_block_the_next_one(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    await repo.start_run(now=clock.now(), stale_after=timedelta(minutes=20))
    clock.advance(21 * 60)
    report = await refresher(repo, boards, clock).run(fixture_registry())
    assert report is not None and report.status == "ok"


async def test_a_second_refresh_changes_only_what_changed(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    run = refresher(repo, boards, clock)
    first = await run.run(fixture_registry())
    assert first is not None
    changed = read_json("lever-indigogrid.json")
    changed[0]["text"] = "Senior UI Engineer"
    boards.serve("indigogrid", changed)
    clock.advance(6 * 3600)
    second = await run.run(fixture_registry())
    assert second is not None and (second.inserted, second.updated, second.listings_closed) == (0, 1, 0)
    assert len(repo.listings) == 17
    assert {item.first_seen_at for item in repo.listings.values()} == {first_seen(repo)}


def first_seen(repo: FakeDiscoverRepo):  # type: ignore[no-untyped-def]
    return min(item.first_seen_at for item in repo.listings.values())


async def test_the_cap_is_applied_and_reported(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    report = await refresher(repo, boards, clock, max_listings_per_employer=3).run(fixture_registry())
    assert report is not None and report.dropped == 2 + 1  # kestrel keeps 3 of 5, northwind 3 of 4
    kestrel = [i for i in repo.listings.values() if i.employer_id == "kestrel"]
    assert len(kestrel) == 3
    # Known cities first (newest first: the undated full-stack role goes last), then remote India, then the rest.
    assert {i.title for i in kestrel} == {
        "Senior Frontend Engineer",
        "Staff Frontend Engineer",
        "Software Engineer Intern",
    }
    assert not any(i.remote_india for i in kestrel)


async def test_a_run_with_no_board_read_is_failed_and_an_empty_registry_says_so(
    repo: FakeDiscoverRepo, clock: FakeClock
) -> None:
    nothing = FakeBoards()
    report = await refresher(repo, nothing, clock).run(fixture_registry())
    assert report is not None and report.status == "failed" and report.employers_ok == 0
    assert await repo.last_ok_run() is None
    clock.advance(3600)
    empty = await refresher(repo, nothing, clock).run(EMPTY)
    assert empty is not None and empty.status == "failed" and empty.note == "The employer registry is empty."


async def test_an_unreadable_registry_records_a_failed_run_and_fetches_nothing(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    report = await refresher(repo, boards, clock).run(
        EMPTY, problem="employers[0]: ats must be one of greenhouse, lever"
    )
    assert report is not None and report.status == "failed" and "ats must be one of" in (report.note or "")
    assert boards.requests == []


async def test_a_storage_error_on_one_employer_does_not_stop_the_others(
    repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = repo.upsert_employer

    async def flaky(employer_id: str, listings, *, now):  # type: ignore[no-untyped-def]
        if employer_id == "kestrel":
            raise RuntimeError("disk full")
        return await original(employer_id, listings, now=now)

    monkeypatch.setattr(repo, "upsert_employer", flaky)
    report = await refresher(repo, boards, clock).run(fixture_registry())
    assert report is not None and report.status == "ok" and report.employers_ok == 7
    assert report.failures["kestrel"] == "could not be stored (RuntimeError)"
    assert "disk full" not in str(report.failures)


def test_every_fixture_board_is_served_by_token() -> None:
    assert set(BOARD_FILES) == {e.board for e in fixture_registry().employers} - {"goneco"}
    assert dataclasses.is_dataclass(Office("Pune"))
    assert isinstance(httpx2.MockTransport(lambda r: httpx2.Response(200)), httpx2.AsyncBaseTransport)
