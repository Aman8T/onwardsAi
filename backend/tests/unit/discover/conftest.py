"""Fixtures for the Discover tests: a service over the fake repository, the fixture registry and hand-written boards.

The clock stands at 2026-10-16 12:00 UTC (`NOW`); the fixture boards are written against it ("Posted 3 days ago" is a
job published on the 13th). The service has no network: its transport is `FakeBoards`, which serves Greenhouse and Lever
JSON by board token and records every request.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tayyari.contract.discover import DiscoverQuery
from tayyari.contract.models import Profile
from tayyari.core.settings import DiscoverSettings
from tayyari.discover.service import DiscoverService
from tests.fakes.clock import FakeClock
from tests.fakes.discover import (
    NOW,
    FakeBoards,
    FakeDiscoverRepo,
    fixture_registry,
    profile_of,
    read_json,
)

BOARD_FILES = {
    "kestrelpay": "greenhouse-kestrelpay.json",
    "northwind": "lever-northwind.json",
    "quillandco": "greenhouse-quillandco.json",
    "indigogrid": "lever-indigogrid.json",
    "parcelpoint": "greenhouse-parcelpoint.json",
    "latticeloom": "lever-latticeloom.json",
    "nilgirimills": "greenhouse-nilgirimills.json",
    "kovaiauto": "lever-kovaiauto.json",
}
"""Every fixture board, by token. ``goneco`` (in the registry) is deliberately not served: its board is a 404."""


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def repo() -> FakeDiscoverRepo:
    return FakeDiscoverRepo()


@pytest.fixture
def boards() -> FakeBoards:
    served = FakeBoards()
    for token, name in BOARD_FILES.items():
        served.serve(token, read_json(name))
    return served


@pytest.fixture
def make_service(
    clock: FakeClock, repo: FakeDiscoverRepo, boards: FakeBoards
) -> Callable[..., DiscoverService]:
    def build(**settings: object) -> DiscoverService:
        return DiscoverService(
            settings=DiscoverSettings(**settings),  # type: ignore[arg-type]
            repo=repo,
            clock=clock,
            registry=fixture_registry(),
            transport=boards.transport,
        )

    return build


@pytest.fixture
def service(make_service: Callable[..., DiscoverService]) -> DiscoverService:
    return make_service()


@pytest.fixture
async def loaded(service: DiscoverService) -> DiscoverService:
    """The service after one refresh of every fixture board."""
    report = await service.refresh()
    assert report is not None and report.status == "ok"
    return service


@pytest.fixture
def frontend() -> Profile:
    return profile_of("frontend")


@pytest.fixture
def fresher() -> Profile:
    return profile_of("fresher")


def query(profile: Profile, **fields: object) -> DiscoverQuery:
    return DiscoverQuery.model_validate({"profileId": profile.id, **fields})
