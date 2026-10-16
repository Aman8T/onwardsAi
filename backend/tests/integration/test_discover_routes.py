"""Discover end to end through the real app over a real SQLite file: the one route that is left (the city list Search with AI
shows), the refresh a request starts in the background, and the switch that turns it off. Only the employers' boards are
faked: `FakeBoards` serves hand-written Greenhouse and Lever JSON through the container's `discover_transport`, and nothing
else is fetched. The screen on top of the store is Search with AI (`test_jobsearch_routes.py`)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tayyari.contract.discover import DiscoverCitiesResponse
from tayyari.core.settings import Settings
from tests.fakes.discover import DATA, FakeBoards, read_json
from tests.integration.support import Env, running
from tests.unit.discover.conftest import BOARD_FILES

MakeSettings = Callable[..., Settings]
BOARDS = 9
"""Employers in the fixture registry: eight served boards and ``goneco``, whose board is a 404."""


@dataclass
class Rig:
    env: Env
    boards: FakeBoards

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    def get(self, path: str, **params: Any) -> Any:
        return self.client.get(path, params=params)


@pytest.fixture
def served() -> FakeBoards:
    boards = FakeBoards()
    for token, name in BOARD_FILES.items():
        boards.serve(token, read_json(name))
    return boards


@pytest.fixture
def start(build_settings: MakeSettings, served: FakeBoards):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(**overrides: Any) -> AsyncIterator[Rig]:
        discover = {"registry_path": str(DATA / "registry.json"), **overrides.pop("discover", {})}
        async with running(
            build_settings(discover=discover, **overrides), discover_transport=served.transport
        ) as env:
            yield Rig(env, served)

    return run


# ───────────────────────── the places ─────────────────────────


async def test_the_cities_are_listed_for_everyone_and_never_cached(start: Any) -> None:
    async with start() as rig:
        response = await rig.get("/api/discover/cities")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    cities = DiscoverCitiesResponse.model_validate(response.json()).cities
    assert len(cities) == 14 and cities[0].name == "Bengaluru" and "HSR Layout" in cities[0].localities


def _paths(routes: Any) -> set[str]:
    """Every route path; FastAPI keeps an included router as one entry, so look inside it."""
    out: set[str] = set()
    for route in routes:
        inner = getattr(getattr(route, "original_router", None), "routes", None)
        out |= _paths(inner) if inner is not None else {route.path}
    return out


async def test_the_old_list_and_the_listing_detail_are_gone(start: Any) -> None:
    async with start() as rig:
        paths = _paths(rig.env.app.router.routes)
    assert {p for p in paths if p.startswith("/api/discover")} == {"/api/discover/cities"}


# ───────────────────────── the board store ─────────────────────────


async def test_the_first_request_answers_at_once_and_starts_reading_the_boards_in_the_background(
    start: Any,
) -> None:
    async with start() as rig:
        repos = rig.env.container.repos
        assert await repos.discover.last_ok_run() is None
        first = await rig.get("/api/discover/cities")
        assert first.status_code == 200  # answered without waiting for the boards
        await rig.env.container.discover.wait_for_refresh()
        assert await repos.discover.last_ok_run() is not None
        assert len(await repos.discover.list_open()) == 17
        assert len(rig.boards.requests) == BOARDS  # each board read once
        again = await rig.get("/api/discover/cities")
        await rig.env.container.discover.wait_for_refresh()
        assert again.status_code == 200 and len(rig.boards.requests) == BOARDS  # fresh: nothing read again


# ───────────────────────── switched off ─────────────────────────


async def test_when_discover_is_off_the_route_says_so_and_nothing_is_read(
    start: Any, served: FakeBoards
) -> None:
    async with start(discover={"enabled": False}) as rig:
        response = await rig.get("/api/discover/cities")
        assert response.status_code == 503 and response.json() == {"error": "discover_disabled"}
        await rig.env.container.discover.wait_for_refresh()
    assert served.requests == []


async def test_the_light_limiter_sheds_a_client_that_hammers_the_discover_routes(start: Any) -> None:
    from tests.integration.support import roomy_guard

    async with start(guard=roomy_guard(chat_per_minute=1)) as rig:
        statuses = [(await rig.get("/api/discover/cities")).status_code for _ in range(4)]
    assert statuses[0] == 200 and 429 in statuses
