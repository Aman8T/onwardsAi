"""The fake interview repo keeps the SQL repo's promises, so the services that run on it prove something: a profile only sees
its own sessions, `save` is a compare-and-swap on the version, and what is read back is a copy."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tayyari.storage.ports import StoredInterview
from tests.fakes.interview import FakeInterviewRepo

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)


def row(
    session_id: str = "a" * 32, profile: str = "p1", *, at: datetime = NOW, **over: object
) -> StoredInterview:
    values: dict[str, object] = {
        "id": session_id,
        "profile_id": profile,
        "kind": "role",
        "status": "active",
        "created_at": at,
        "updated_at": at,
        "version": 1,
        "state": {"turns": []},
        "debrief": None,
    }
    return StoredInterview(**{**values, **over})  # type: ignore[arg-type]


async def test_a_profile_only_sees_its_own_sessions() -> None:
    repo = FakeInterviewRepo()
    await repo.create(row())
    assert (await repo.get("p1", "a" * 32)) is not None
    assert await repo.get("p2", "a" * 32) is None and await repo.get("p1", "b" * 32) is None
    assert await repo.delete("p2", "a" * 32) is False and await repo.delete("p1", "a" * 32) is True
    assert await repo.delete("p1", "a" * 32) is False


async def test_an_id_cannot_be_created_twice() -> None:
    repo = FakeInterviewRepo()
    await repo.create(row())
    with pytest.raises(ValueError):
        await repo.create(row())


async def test_save_writes_only_when_the_stored_version_is_one_less() -> None:
    repo = FakeInterviewRepo()
    await repo.create(row())
    later = NOW + timedelta(seconds=5)
    assert (
        await repo.save(
            row(version=2, state={"turns": [1]}, status="done", debrief={"x": 1}, updated_at=later)
        )
        is True
    )
    stored = await repo.get("p1", "a" * 32)
    assert stored is not None
    assert (stored.version, stored.state, stored.status, stored.debrief, stored.updated_at) == (
        2,
        {"turns": [1]},
        "done",
        {"x": 1},
        later,
    )
    assert stored.created_at == NOW and stored.kind == "role"  # what a save does not touch
    assert await repo.save(row(version=2, state={"turns": [2]})) is False  # someone saved version 2 first
    assert await repo.save(row(version=4)) is False  # skipped a version
    assert await repo.save(row(version=1)) is False
    assert (await repo.get("p1", "a" * 32)) == stored
    assert await repo.save(row("c" * 32, version=2)) is False  # no such session
    assert await repo.save(row(profile="p2", version=3)) is False  # not this profile's


async def test_what_is_read_back_is_a_copy() -> None:
    repo = FakeInterviewRepo()
    item = row()
    await repo.create(item)
    item.state["turns"].append("changed after create")
    stored = await repo.get("p1", "a" * 32)
    assert stored is not None and stored.state == {"turns": []}
    stored.state["turns"].append("changed after get")
    again = await repo.get("p1", "a" * 32)
    assert again is not None and again.state == {"turns": []}


async def test_list_is_newest_first_limited_and_the_profiles_own() -> None:
    repo = FakeInterviewRepo()
    for i in range(4):
        await repo.create(row(f"{i:032x}", at=NOW + timedelta(hours=i)))
    await repo.create(row("f" * 32, "p2", at=NOW + timedelta(hours=9)))
    listed = await repo.list("p1", 3)
    assert [r.id for r in listed] == [f"{i:032x}" for i in (3, 2, 1)]
    assert (
        await repo.list("p1", 0) == []
        and len(await repo.list("p1", 50)) == 4
        and len(await repo.list("p3", 5)) == 0
    )


async def test_count_since_and_purge() -> None:
    repo = FakeInterviewRepo()
    for i in range(4):
        await repo.create(row(f"{i:032x}", at=NOW + timedelta(days=i)))
    await repo.create(row("f" * 32, "p2", at=NOW + timedelta(days=3)))
    assert await repo.count_since("p1", NOW + timedelta(days=2)) == 2
    assert await repo.count_since("p1", NOW + timedelta(days=2, seconds=1)) == 1
    assert await repo.count_since("p2", NOW) == 1 and await repo.count_since("p3", NOW) == 0
    assert await repo.purge(NOW + timedelta(days=2)) == 2  # created before the cutoff, whoever owns them
    assert [r.id for r in await repo.list("p1", 10)] == [f"{i:032x}" for i in (3, 2)]
    naive = (NOW + timedelta(days=3)).replace(tzinfo=None)
    assert await repo.purge(naive) == 1  # a naive cutoff reads as UTC


def test_the_fake_row_helper_is_a_frozen_dataclass() -> None:
    with pytest.raises(AttributeError):
        replace(row(), id="x").version = 3  # type: ignore[misc]
