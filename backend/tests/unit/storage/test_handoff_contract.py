"""Hand-offs, run against every backend (the fake, a SQLite file and memory, and Postgres when configured), so
`FakeHandoffRepo` cannot drift from `SqlHandoffRepo`: a hand-off is taken once, an unknown id is nothing, the expired are
purged and only they are, and bytes and links come back as they went in."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest_asyncio

from tayyari.storage.ports import HandoffRepo, StoredHandoff
from tayyari.storage.repos.handoff import SqlHandoffRepo
from tests.fakes.handoff import FakeHandoffRepo
from tests.unit.storage.conftest import T0, open_db

PDF = b"%PDF-1.7\x00\xff binary \x00"
HID = "h" * 32


@pytest_asyncio.fixture
async def repo(backend: str, tmp_path: Path) -> AsyncIterator[HandoffRepo]:
    if backend == "fake":
        yield FakeHandoffRepo()
        return
    async with open_db(backend, tmp_path) as db:
        yield SqlHandoffRepo(db)


def item(handoff_id: str = HID, *, expires_in: int = 900) -> StoredHandoff:
    return StoredHandoff(
        id=handoff_id,
        created_at=T0,
        expires_at=T0 + timedelta(seconds=expires_in),
        links=["https://github.com/you", "https://you.dev"],
        goal="Backend roles",
        notes="Open to relocate",
        resume_name="cv.pdf",
        resume_type="application/pdf",
        resume_bytes=PDF,
    )


async def test_a_hand_off_comes_back_whole_and_only_once(repo: HandoffRepo) -> None:
    await repo.create(item())
    assert await repo.take(HID) == item()
    assert await repo.take(HID) is None


async def test_optional_parts_stay_empty(repo: HandoffRepo) -> None:
    bare = StoredHandoff(id=HID, created_at=T0, expires_at=T0 + timedelta(seconds=5), links=[])
    await repo.create(bare)
    assert await repo.take(HID) == bare


async def test_an_unknown_id_is_nothing(repo: HandoffRepo) -> None:
    assert await repo.take("nope") is None


async def test_two_takers_never_both_get_it(repo: HandoffRepo) -> None:
    await repo.create(item())
    got = await asyncio.gather(*(repo.take(HID) for _ in range(5)))
    assert sum(1 for g in got if g is not None) == 1


async def test_purge_removes_only_the_expired(repo: HandoffRepo) -> None:
    await repo.create(item("a" * 32, expires_in=10))
    await repo.create(item("b" * 32, expires_in=1000))
    assert await repo.purge_expired(T0 + timedelta(seconds=11)) == 1
    assert await repo.take("a" * 32) is None
    assert await repo.take("b" * 32) is not None
    assert await repo.purge_expired(T0 + timedelta(days=1)) == 0
