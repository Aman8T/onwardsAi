"""Discover end to end through the real app over a real SQLite file: the three routes, who may open a profile, the first
refresh that starts in the background, the listing detail the match is started from, the switch that turns it off, and
the promise that the person's area is not kept. Only the employers' boards are faked: `FakeBoards` serves hand-written
Greenhouse and Lever JSON through the container's `discover_transport`, and nothing else is fetched."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from tayyari.contract.discover import DiscoverCitiesResponse, DiscoverResponse, ListingDetail
from tayyari.contract.stored import StoredContext
from tayyari.core.settings import Settings
from tayyari.storage.tables import metadata
from tests.fakes.discover import DATA, FakeBoards, profile_of, read_json
from tests.integration.support import Env, running
from tests.unit.discover.conftest import BOARD_FILES

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
FRONTEND_ID = profile_of("frontend").id
FRESHER_ID = profile_of("fresher").id
UNKNOWN_ID = "f" * 32
PROFILE_NOT_FOUND = {"error": "Profile not found"}
FIRST_READ = "Reading employers' job boards for the first time. This takes about a minute."


@dataclass
class Rig:
    env: Env
    boards: FakeBoards

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    async def profile(self, name: str = "frontend", *, sample: bool = False, owner: str | None = None) -> str:
        found = profile_of(name)
        container = self.env.container
        await container.repos.profiles.save(
            found.id, found.to_dict(), StoredContext(), goal=found.goal, owner_hash=None, is_sample=sample
        )
        if owner is not None:
            await container.repos.users.claim_profile(owner, found.id)
        return found.id

    async def account(self, email: str = "ana@example.com") -> tuple[str, dict[str, str]]:
        container = self.env.container
        user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
        token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        return user.id, {"cookie": f"{SESSION}={token}"}

    async def refreshed(self) -> None:
        """Wait for the refresh a request started in the background, or run one."""
        service = self.env.container.discover
        await service.wait_for_refresh()
        if await service._repo.last_ok_run() is None:
            report = await service.refresh()
            assert report is not None and report.status == "ok"

    def get(self, path: str, headers: dict[str, str] | None = None, **params: Any) -> Any:
        return self.client.get(path, params=params, headers=headers or {})

    async def screen(self, pid: str = FRONTEND_ID, **params: Any) -> DiscoverResponse:
        response = await self.get("/api/discover", profileId=pid, **params)
        assert response.status_code == 200, response.text
        return DiscoverResponse.model_validate(response.json())


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


# ───────────────────────── the screen ─────────────────────────


async def test_the_first_request_answers_at_once_and_starts_reading_the_boards_in_the_background(
    start: Any,
) -> None:
    async with start() as rig:
        pid = await rig.profile()
        first = await rig.get("/api/discover", profileId=pid)
        assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
        body = first.json()
        assert body["coverage"]["note"] == FIRST_READ and body["listings"] == [] and "refreshedAt" not in body
        await rig.refreshed()
        later = await rig.screen(pid, locality="HSR Layout")
        assert later.total == 5 and later.coverage.employers == 8 and later.coverage.listings == 17
        assert later.refreshedAt is not None and later.area.cityFrom == "resume"
        assert len(rig.boards.requests) == 9  # each board read once


async def test_the_whole_screen_is_the_contract(start: Any) -> None:
    async with start() as rig:
        pid = await rig.profile()
        await rig.get("/api/discover", profileId=pid)
        await rig.refreshed()
        response = await rig.get(
            "/api/discover",
            profileId=pid,
            city="Bengaluru",
            locality="HSR Layout",
            reach="25",
            breadth="stretch",
            order="posted",
        )
    assert response.status_code == 200
    body = response.json()
    parsed = DiscoverResponse.model_validate(body)
    assert parsed.to_dict() == body  # nothing extra on the wire, nothing missing
    assert (parsed.reach, parsed.breadth, parsed.order, parsed.lane) == ("25", "stretch", "posted", "near")
    assert parsed.total == 5 and parsed.suggestion == "The whole city adds 2 more."
    assert "%" not in response.text
    card = parsed.listings[0]
    assert card.applyUrl.startswith("https://") and card.applyHost and card.confidence == "employer"


async def test_blank_query_values_count_as_not_given(start: Any) -> None:
    async with start() as rig:
        pid = await rig.profile()
        await rig.get("/api/discover", profileId=pid)
        await rig.refreshed()
        body = await rig.get("/api/discover", profileId=pid, city="", locality=" ", reach="", lane="")
    assert body.status_code == 200 and body.json()["reach"] == "city" and body.json()["lane"] == "near"


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"profileId": ""},
        {"profileId": "a" * 40, "reach": "7"},
        {"profileId": "a" * 40, "breadth": "huge"},
        {"profileId": "a" * 40, "lane": "map"},
        {"profileId": "a" * 40, "order": "newest"},
        {"profileId": "a" * 40, "city": "x" * 200},
    ],
)
async def test_a_bad_query_is_a_400_before_anything_is_read(start: Any, params: dict[str, str]) -> None:
    async with start() as rig:
        response = await rig.get("/api/discover", **params)
    assert response.status_code == 400 and set(response.json()) == {"error"}


async def test_names_that_are_not_ours_are_a_400(start: Any) -> None:
    async with start() as rig:
        pid = await rig.profile()
        for params, message in (
            ({"city": "Atlantis"}, "That city is not one we know. Pick one from the list."),
            (
                {"city": "Pune", "locality": "HSR Layout"},
                "That locality is not in Pune. Pick one from the list.",
            ),
            ({"otherCity": "Gotham"}, "That city is not one we know. Pick one from the list."),
        ):
            response = await rig.get("/api/discover", profileId=pid, **params)
            assert response.status_code == 400 and response.json() == {"error": message}


# ───────────────────────── who may open a profile ─────────────────────────


async def test_an_unknown_profile_is_the_same_404_whatever_the_reason(start: Any) -> None:
    async with start() as rig:
        for pid in (UNKNOWN_ID, "not an id!", "../../etc"):
            response = await rig.get("/api/discover", profileId=pid)
            assert (response.status_code, response.json()) == (404, PROFILE_NOT_FOUND), pid
        too_long = await rig.get("/api/discover", profileId="x" * 200)
        assert too_long.status_code == 400  # not even the shape of an id


async def test_a_claimed_profile_is_only_for_its_owner(start: Any) -> None:
    async with start() as rig:
        owner_id, owner = await rig.account("owner@example.com")
        _, other = await rig.account("other@example.com")
        pid = await rig.profile(owner=owner_id)
        mine = await rig.get("/api/discover", headers=owner, profileId=pid)
        assert mine.status_code == 200
        for headers in ({}, other):
            refused = await rig.get("/api/discover", headers=headers, profileId=pid)
            assert refused.status_code == 404 and refused.json() == PROFILE_NOT_FOUND
        gone = await rig.get("/api/discover/listings/" + "0" * 32, headers=other, profileId=pid)
        assert gone.status_code == 404 and gone.json() == PROFILE_NOT_FOUND


async def test_a_sample_run_and_an_unclaimed_profile_are_open_to_whoever_holds_the_id(start: Any) -> None:
    async with start() as rig:
        sample = await rig.profile("fresher", sample=True)
        unclaimed = await rig.profile("frontend")
        assert (await rig.get("/api/discover", profileId=sample)).status_code == 200
        assert (await rig.get("/api/discover", profileId=unclaimed)).status_code == 200
        bundled = rig.env.sample_id
        assert (
            await rig.get("/api/discover", profileId=bundled)
        ).status_code == 200  # the bundled persona (Riya)


# ───────────────────────── one listing ─────────────────────────


async def test_a_listing_opens_with_its_posting_text_ready_for_the_match(start: Any) -> None:
    async with start() as rig:
        pid = await rig.profile()
        await rig.get("/api/discover", profileId=pid)
        await rig.refreshed()
        screen = await rig.screen(pid, locality="HSR Layout")
        card = next(c for c in screen.listings if c.title == "Frontend Engineer II")
        response = await rig.get(
            f"/api/discover/listings/{card.id}", profileId=pid, city="Bengaluru", locality="HSR Layout"
        )
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        detail = ListingDetail.model_validate(response.json())
        assert detail.card == card and detail.card.place.km == 0.0
        assert detail.postingText.startswith(
            "Frontend Engineer II\nNorthwind Analytics · HSR Layout, Bengaluru"
        )
        assert len(detail.postingText) <= 20_000


async def test_an_unknown_or_closed_listing_is_a_404(start: Any, served: FakeBoards) -> None:
    async with start() as rig:
        pid = await rig.profile()
        await rig.get("/api/discover", profileId=pid)
        await rig.refreshed()
        screen = await rig.screen(pid, locality="HSR Layout")
        gone = next(c for c in screen.listings if c.title == "UI Engineer")
        for listing_id in ("0" * 32, "nope", "A" * 32):
            missing = await rig.get(f"/api/discover/listings/{listing_id}", profileId=pid)
            assert missing.status_code == 404 and missing.json() == {"error": "Listing not found"}
        served.serve("indigogrid", [])  # the employer took it down; the next refresh closes it
        await rig.env.container.discover.refresh()
        closed = await rig.get(f"/api/discover/listings/{gone.id}", profileId=pid)
        assert closed.status_code == 404 and closed.json() == {"error": "Listing not found"}
        after = await rig.screen(pid, locality="HSR Layout")
        assert gone.id not in {c.id for c in after.listings}


async def test_the_listing_route_needs_a_profile(start: Any) -> None:
    async with start() as rig:
        assert (await rig.get("/api/discover/listings/" + "0" * 32)).status_code == 400


# ───────────────────────── switched off, and what is kept ─────────────────────────


async def test_when_discover_is_off_every_route_says_so_and_nothing_is_read(
    start: Any, served: FakeBoards
) -> None:
    async with start(discover={"enabled": False}) as rig:
        pid = await rig.profile()
        for path in ("/api/discover", "/api/discover/cities", f"/api/discover/listings/{'0' * 32}"):
            response = await rig.get(path, profileId=pid)
            assert response.status_code == 503 and response.json() == {"error": "discover_disabled"}
    assert served.requests == []


async def test_the_persons_area_is_not_stored_anywhere(start: Any) -> None:
    secret = "Malleshwaram"  # a Bengaluru locality no fixture listing is in
    async with start() as rig:
        pid = await rig.profile()
        await rig.get("/api/discover", profileId=pid)
        await rig.refreshed()
        answered = await rig.screen(pid, city="Bengaluru", locality=secret, otherCity="Hyderabad")
        assert answered.area.locality == secret
        assert rig.env.container.db is not None
        async with rig.env.container.db.session() as session:
            for table in metadata.sorted_tables:
                rows = (await session.execute(select(table))).fetchall()
                assert secret not in json.dumps([[str(cell) for cell in row] for row in rows]), table.name


async def test_the_browser_is_told_not_to_keep_the_screen(start: Any) -> None:
    async with start() as rig:
        pid = await rig.profile()
        response = await rig.get("/api/discover", profileId=pid)
    assert response.headers["cache-control"] == "no-store"


async def test_the_light_limiter_sheds_a_client_that_hammers_the_discover_routes(
    start: Any, build_settings: MakeSettings
) -> None:
    from tests.integration.support import roomy_guard

    async with start(guard=roomy_guard(chat_per_minute=1)) as rig:
        statuses = [(await rig.get("/api/discover/cities")).status_code for _ in range(4)]
    assert statuses[0] == 200 and 429 in statuses
