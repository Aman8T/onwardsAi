"""Practice interviews' storage, run against every backend (the fake, a SQLite file and memory, and Postgres when
configured), so `FakeInterviewRepo` cannot drift from `SqlInterviewRepo`: sessions only a profile's own, the version
compare-and-swap that keeps two tabs from overwriting each other, newest-first listing, "left today", and the retention
purge (also through `run_maintenance`)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from tayyari.core.settings import InterviewSettings, Settings
from tayyari.storage.db import Database
from tayyari.storage.maintenance import INTERVIEW_RETENTION, run_maintenance
from tayyari.storage.ports import InterviewRepo, StoredInterview
from tayyari.storage.repos import build_repositories
from tayyari.storage.repos.interview import SqlInterviewRepo
from tests.unit.storage.conftest import T0, open_db

DAY = timedelta(days=1)
SID = "a" * 32
OTHER = "b" * 32


@pytest_asyncio.fixture
async def repo(backend: str, tmp_path: Path) -> AsyncIterator[InterviewRepo]:
    """The interview repository on each backend. The fake half is skipped until `tests/fakes/interview.py` exists."""
    if backend == "fake":
        try:
            from tests.fakes.interview import FakeInterviewRepo
        except ImportError:
            pytest.skip("tests/fakes/interview.py (FakeInterviewRepo) is not part of this checkout yet")
        yield FakeInterviewRepo()
        return
    async with open_db(backend, tmp_path) as db:
        yield SqlInterviewRepo(db)


def item(
    session_id: str = SID,
    profile_id: str = "p1",
    *,
    kind: str = "role",
    status: str = "active",
    created_at: datetime = T0,
    version: int = 1,
    state: dict[str, Any] | None = None,
    debrief: dict[str, Any] | None = None,
) -> StoredInterview:
    return StoredInterview(
        id=session_id,
        profile_id=profile_id,
        kind=kind,
        status=status,
        created_at=created_at,
        updated_at=created_at,
        version=version,
        state={"plan": [{"id": "s1", "topic": "Performance"}], "turns": []} if state is None else state,
        debrief=debrief,
    )


async def stored(repo: InterviewRepo, session_id: str = SID, profile_id: str = "p1") -> StoredInterview:
    found = await repo.get(profile_id, session_id)
    assert found is not None
    return found


# ───────────────────────── create and get ─────────────────────────


async def test_a_created_session_reads_back_as_it_was_written(repo: InterviewRepo) -> None:
    state = {
        "title": "Frontend Engineer at Headout “Q3”",
        "turns": [{"speaker": "you", "text": "I cut the café checkout from 4s to 1.2s", "at": 31}],
        "rung": None,
        "n": 3,
        "ratio": 0.5,
        "ok": True,
    }
    await repo.create(item(state=state))
    found = await stored(repo)
    assert (found.id, found.profile_id, found.kind, found.status, found.version) == (
        SID,
        "p1",
        "role",
        "active",
        1,
    )
    assert found.state == state
    assert found.debrief is None
    assert found.created_at == T0 and found.updated_at == T0
    assert found.created_at.tzinfo is not None and found.created_at.utcoffset() == timedelta(0)


async def test_a_session_is_visible_only_to_its_own_profile(repo: InterviewRepo) -> None:
    await repo.create(item())
    assert await repo.get("p2", SID) is None
    assert await repo.get("p1", OTHER) is None
    assert [s.id for s in await repo.list("p2", 10)] == []


async def test_what_comes_back_is_a_copy(repo: InterviewRepo) -> None:
    await repo.create(item(state={"turns": [{"text": "one"}]}, debrief={"results": []}))
    first = await stored(repo)
    first.state["turns"].append({"text": "changed"})
    assert first.debrief is not None
    first.debrief["results"].append("changed")
    again = await stored(repo)
    assert again.state == {"turns": [{"text": "one"}]}
    assert again.debrief == {"results": []}


async def test_what_was_handed_in_is_copied_too(repo: InterviewRepo) -> None:
    state: dict[str, Any] = {"turns": []}
    await repo.create(item(state=state))
    state["turns"].append("after the write")
    assert (await stored(repo)).state == {"turns": []}


# ───────────────────────── save: the version is a compare-and-swap ─────────────────────────


async def test_a_save_on_the_next_version_lands_and_changes_only_what_a_save_may_change(
    repo: InterviewRepo,
) -> None:
    await repo.create(item(created_at=T0))
    later = T0 + timedelta(minutes=5)
    done = replace(
        item(version=2, status="done", state={"turns": [1, 2, 3]}, debrief={"counts": {"defended": 1}}),
        kind="stories",  # a save does not change the kind
        created_at=later,  # nor the creation time
        updated_at=later,
    )
    assert await repo.save(done) is True
    found = await stored(repo)
    assert (found.version, found.status, found.state) == (2, "done", {"turns": [1, 2, 3]})
    assert found.debrief == {"counts": {"defended": 1}}
    assert found.updated_at == later
    assert found.kind == "role" and found.created_at == T0


async def test_a_save_over_a_stale_version_writes_nothing(repo: InterviewRepo) -> None:
    await repo.create(item())
    assert await repo.save(item(version=2, state={"who": "first tab"})) is True
    assert await repo.save(item(version=2, state={"who": "second tab"})) is False  # it read version 1 too
    found = await stored(repo)
    assert (found.version, found.state) == (2, {"who": "first tab"})


async def test_a_save_that_skips_a_version_or_repeats_one_is_refused(repo: InterviewRepo) -> None:
    await repo.create(item())
    assert await repo.save(item(version=3, state={"skipped": True})) is False
    assert await repo.save(item(version=1, state={"again": True})) is False
    assert (await stored(repo)).state == item().state
    assert await repo.save(item(version=2, state={"two": True})) is True
    assert await repo.save(item(version=3, state={"three": True})) is True
    assert (await stored(repo)).version == 3


async def test_a_save_for_a_session_that_is_not_there_is_refused(repo: InterviewRepo) -> None:
    assert await repo.save(item(version=2)) is False
    assert await repo.get("p1", SID) is None  # and it did not create one


async def test_a_save_cannot_reach_another_profiles_session(repo: InterviewRepo) -> None:
    await repo.create(item())
    assert await repo.save(item(profile_id="p2", version=2, state={"stolen": True})) is False
    found = await stored(repo)
    assert (found.version, found.state) == (1, item().state)


async def test_two_saves_of_the_same_version_at_once_cannot_both_land(repo: InterviewRepo) -> None:
    await repo.create(item())
    mine = item(version=2, state={"who": "a"})
    yours = item(version=2, state={"who": "b"})
    won_a, won_b = await asyncio.gather(repo.save(mine), repo.save(yours))
    assert sorted([won_a, won_b]) == [False, True]
    found = await stored(repo)
    assert found.version == 2 and found.state == ({"who": "a"} if won_a else {"who": "b"})


async def test_a_debrief_is_stored_with_the_session_and_read_back(repo: InterviewRepo) -> None:
    debrief = {
        "sessionId": SID,
        "results": [{"slotId": "s1", "verdict": "defended", "say": None}],
        "minutes": 7,
    }
    await repo.create(item())
    assert await repo.save(item(version=2, status="done", debrief=debrief)) is True
    assert (await stored(repo)).debrief == debrief
    assert await repo.save(item(version=3, status="done", debrief=debrief)) is True  # a later fact flag, say
    assert (await stored(repo)).version == 3


# ───────────────────────── list, delete and counting ─────────────────────────


async def test_the_list_is_newest_first_limited_and_the_profiles_own(repo: InterviewRepo) -> None:
    for n in range(5):
        await repo.create(item(f"{n:032x}", created_at=T0 + timedelta(hours=n), state={"n": n}))
    await repo.create(item("f" * 32, "p2", created_at=T0 + timedelta(hours=9)))
    listed = await repo.list("p1", 3)
    assert [s.state["n"] for s in listed] == [4, 3, 2]
    assert [s.id for s in await repo.list("p1", 50)] == [f"{n:032x}" for n in (4, 3, 2, 1, 0)]
    assert await repo.list("p1", 0) == []
    assert [s.id for s in await repo.list("p2", 50)] == ["f" * 32]
    assert await repo.list("nobody", 50) == []


async def test_a_listed_session_carries_its_state_and_debrief(repo: InterviewRepo) -> None:
    await repo.create(item(status="done", debrief={"minutes": 4}, state={"turns": ["x"]}))
    (found,) = await repo.list("p1", 5)
    assert (found.status, found.state, found.debrief, found.version) == (
        "done",
        {"turns": ["x"]},
        {"minutes": 4},
        1,
    )


async def test_delete_removes_one_session_of_this_profile_once(repo: InterviewRepo) -> None:
    await repo.create(item())
    await repo.create(item(OTHER))
    assert await repo.delete("p2", SID) is False  # another profile's
    assert await repo.delete("p1", "c" * 32) is False  # not there
    assert await repo.delete("p1", SID) is True
    assert await repo.delete("p1", SID) is False
    assert await repo.get("p1", SID) is None
    assert [s.id for s in await repo.list("p1", 10)] == [OTHER]


async def test_count_since_counts_this_profiles_sessions_from_that_moment_on(repo: InterviewRepo) -> None:
    midnight = datetime(2026, 10, 7, tzinfo=UTC)
    await repo.create(item("1" * 32, created_at=midnight - timedelta(seconds=1)))
    await repo.create(item("2" * 32, created_at=midnight))
    await repo.create(item("3" * 32, created_at=midnight + timedelta(hours=9)))
    await repo.create(item("4" * 32, "p2", created_at=midnight + timedelta(hours=9)))
    assert await repo.count_since("p1", midnight) == 2  # the one at midnight counts, the one before does not
    assert await repo.count_since("p1", midnight + timedelta(hours=10)) == 0
    assert await repo.count_since("p1", midnight - DAY) == 3
    assert await repo.count_since("p2", midnight) == 1
    assert await repo.count_since("nobody", midnight - DAY) == 0


async def test_a_deleted_session_no_longer_counts_against_today(repo: InterviewRepo) -> None:
    await repo.create(item())
    assert await repo.count_since("p1", T0 - DAY) == 1
    await repo.delete("p1", SID)
    assert await repo.count_since("p1", T0 - DAY) == 0


# ───────────────────────── retention ─────────────────────────


async def test_purge_deletes_sessions_created_before_the_cutoff_and_says_how_many(
    repo: InterviewRepo,
) -> None:
    cutoff = T0
    await repo.create(item("1" * 32, created_at=cutoff - timedelta(days=40)))
    await repo.create(item("2" * 32, "p2", created_at=cutoff - timedelta(seconds=1)))
    await repo.create(item("3" * 32, created_at=cutoff))  # created exactly at the cutoff stays
    await repo.create(item("4" * 32, created_at=cutoff + DAY))
    assert await repo.purge(cutoff) == 2
    assert sorted(s.id for s in await repo.list("p1", 10)) == ["3" * 32, "4" * 32]
    assert await repo.list("p2", 10) == []
    assert await repo.purge(cutoff) == 0


async def test_an_interview_updated_recently_is_still_purged_by_its_age(repo: InterviewRepo) -> None:
    """The window runs from when the interview was created, whatever was saved since (its transcript is 30 days old)."""
    await repo.create(item(created_at=T0 - timedelta(days=31)))
    assert await repo.save(item(created_at=T0 - timedelta(days=31), version=2)) is True
    assert await repo.purge(T0 - timedelta(days=30)) == 1


async def test_the_maintenance_purge_applies_the_interview_retention_window(
    any_real_db: Database, make_settings: Callable[..., Settings]
) -> None:
    settings = make_settings()
    assert INTERVIEW_RETENTION == timedelta(days=InterviewSettings().retention_days) == timedelta(days=30)
    now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    repo = build_repositories(any_real_db).interview
    await repo.create(item("1" * 32, created_at=now - timedelta(days=31)))
    await repo.create(item("2" * 32, created_at=now - timedelta(days=29)))
    await repo.create(
        item("3" * 32, "p2", created_at=now - timedelta(days=90), status="done", debrief={"a": 1})
    )

    counts = await run_maintenance(any_real_db, settings, now)
    assert counts["interview"] == 2 and counts["errors"] == 0
    assert [s.id for s in await repo.list("p1", 10)] == ["2" * 32]
    assert await repo.list("p2", 10) == []


async def test_the_maintenance_window_follows_the_setting(
    any_real_db: Database, make_settings: Callable[..., Settings]
) -> None:
    settings = make_settings(interview=InterviewSettings(retention_days=7))
    now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    repo = build_repositories(any_real_db).interview
    await repo.create(item("1" * 32, created_at=now - timedelta(days=8)))
    await repo.create(item("2" * 32, created_at=now - timedelta(days=6)))
    counts = await run_maintenance(any_real_db, settings, now)
    assert counts["interview"] == 1
    assert [s.id for s in await repo.list("p1", 10)] == ["2" * 32]
