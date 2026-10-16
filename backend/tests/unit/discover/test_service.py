"""The Discover service over the fake store and the hand-written boards: the city list, the switch that turns it off, and
the background refresh that never blocks a request. (What it reads and stores is `test_refresh` and `test_parse`; the
screen on top of the store is Search with AI, `tests/unit/jobsearch`.)"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from tayyari.discover.errors import DiscoverDisabled
from tayyari.discover.service import DiscoverService
from tayyari.storage.ports import DiscoverListing, DiscoverRun
from tests.fakes.clock import FakeClock
from tests.fakes.discover import FakeBoards, FakeDiscoverRepo

BOARDS = 9
"""Employers in the fixture registry: eight served boards and ``goneco``, whose board is a 404."""


def test_the_places_are_listed_for_the_pickers(service: DiscoverService) -> None:
    cities = service.cities().cities
    assert [c.name for c in cities][:3] == ["Bengaluru", "Hyderabad", "Pune"] and len(cities) == 14
    assert "HSR Layout" in cities[0].localities and len(cities[0].localities) == 18
    assert "%" not in json.dumps(service.cities().to_dict())


# ───────────────────────── switched off, and nothing about a person is kept ─────────────────────────


async def test_when_discover_is_switched_off_nothing_is_read_or_answered(
    make_service: Callable[..., DiscoverService], boards: FakeBoards
) -> None:
    off = make_service(enabled=False)
    assert off.enabled is False
    with pytest.raises(DiscoverDisabled) as raised:
        off.require_enabled()
    assert raised.value.status == 503 and raised.value.to_body() == {"error": "discover_disabled"}
    assert await off.maybe_refresh() is False and boards.requests == []
    on = make_service()
    assert on.enabled is True
    on.require_enabled()


async def test_the_board_store_has_nowhere_to_keep_a_person(
    service: DiscoverService, repo: FakeDiscoverRepo
) -> None:
    report = await service.refresh()
    assert report is not None and report.status == "ok" and repo.listings
    fields = {f.name for f in dataclasses.fields(DiscoverListing)} | {
        f.name for f in dataclasses.fields(DiscoverRun)
    }
    assert not {f for f in fields if "area" in f or "person" in f or "profile" in f or "user" in f}


# ───────────────────────── the background refresh ─────────────────────────


async def test_a_check_that_finds_nothing_stored_starts_a_refresh_and_does_not_wait_for_it(
    make_service: Callable[..., DiscoverService], clock: FakeClock, repo: FakeDiscoverRepo
) -> None:
    release = asyncio.Event()
    asked: list[str] = []

    async def slow(request: Any) -> Any:
        asked.append(str(request.url))
        await release.wait()
        return httpx2.Response(200, json={"jobs": []})

    built = make_service()
    service = DiscoverService(
        settings=built._settings,
        repo=repo,
        clock=clock,
        registry=built.registry,
        transport=httpx2.MockTransport(slow),
    )
    assert await asyncio.wait_for(service.maybe_refresh(), timeout=5) is True
    await asyncio.sleep(0.05)
    assert service._task is not None and not service._task.done()  # still reading: the request did not wait
    assert await service.maybe_refresh() is False  # and a second check does not start another
    release.set()
    await service.wait_for_refresh()
    assert (await repo.last_ok_run()) is not None
    assert len(asked) == BOARDS  # one read per board
    await service.aclose()


async def test_a_check_starts_nothing_while_the_data_is_fresh(
    service: DiscoverService, boards: FakeBoards, clock: FakeClock
) -> None:
    assert await service.maybe_refresh() is True
    await service.wait_for_refresh()
    seen = len(boards.requests)
    assert seen == BOARDS
    clock.advance(5 * 3600)
    assert await service.maybe_refresh() is False  # five hours old: nothing started
    await service.wait_for_refresh()
    assert len(boards.requests) == seen


async def test_a_check_finds_old_data_and_refreshes_in_the_background(
    service: DiscoverService, boards: FakeBoards, clock: FakeClock, repo: FakeDiscoverRepo
) -> None:
    await service.maybe_refresh()
    await service.wait_for_refresh()
    first = await repo.last_ok_run()
    assert first is not None and first.finished_at is not None
    assert first.finished_at.isoformat() == "2026-10-16T12:00:00+00:00"
    clock.advance(7 * 3600)
    assert await service.maybe_refresh() is True
    await service.wait_for_refresh()
    assert len(boards.requests) == 2 * BOARDS  # a second refresh read every board again
    latest = await repo.last_ok_run()
    assert latest is not None and latest.finished_at is not None
    assert latest.finished_at.isoformat() == "2026-10-16T19:00:00+00:00"


async def test_a_failing_refresh_is_not_retried_by_every_check(
    make_service: Callable[..., DiscoverService], clock: FakeClock, repo: FakeDiscoverRepo
) -> None:
    down = FakeBoards()  # serves nothing: every board is a 404
    built = make_service()
    service = DiscoverService(
        settings=built._settings,
        repo=repo,
        clock=clock,
        registry=built.registry,
        transport=down.transport,
    )
    await service.maybe_refresh()
    await service.wait_for_refresh()
    first = len(down.requests)
    assert first == BOARDS
    for _ in range(3):
        assert await service.maybe_refresh() is False
        await service.wait_for_refresh()
    assert len(down.requests) == first  # the pause between attempts holds
    clock.advance(11 * 60)
    assert await service.maybe_refresh() is True
    await service.wait_for_refresh()
    assert len(down.requests) == 2 * first
    await service.aclose()


async def test_shutdown_cancels_a_refresh_that_is_still_reading(
    make_service: Callable[..., DiscoverService], clock: FakeClock, repo: FakeDiscoverRepo
) -> None:
    started = asyncio.Event()

    async def never(request: Any) -> Any:
        started.set()
        await asyncio.Event().wait()
        return httpx2.Response(200)

    built = make_service()
    service = DiscoverService(
        settings=built._settings,
        repo=repo,
        clock=clock,
        registry=built.registry,
        transport=httpx2.MockTransport(never),
    )
    assert await service.maybe_refresh() is True
    await asyncio.wait_for(started.wait(), timeout=5)
    task = service._task
    assert task is not None
    await asyncio.wait_for(service.aclose(), timeout=5)
    assert task.cancelled() or task.done()
