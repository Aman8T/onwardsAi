"""Plans for roles, run against every backend (the fake, a SQLite file and memory, and Postgres when configured), so
`FakePrepareRepo` cannot drift from `SqlPrepareRepo`: a plan only its profile's, the newest plan found for a role and
requirement, the state's compare-and-swap, copies in and out, and (on a real database) the row going with its profile."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from tayyari.contract.stored import StoredContext
from tayyari.storage.db import Database
from tayyari.storage.ports import PrepareRepo, StoredPlan
from tayyari.storage.repos import build_repositories
from tayyari.storage.repos.prepare import SqlPrepareRepo
from tests.fakes.prepare import FakePrepareRepo
from tests.unit.storage.conftest import T0, open_db

PID = "a" * 32
OTHER = "b" * 32
P1, P2 = "1" * 32, "2" * 32
ROLE = "ai-product-engineer"


@pytest_asyncio.fixture
async def repo(backend: str, tmp_path: Path) -> AsyncIterator[PrepareRepo]:
    """The plan repository on each backend, with two profiles stored (a plan's row refers to its profile)."""
    if backend == "fake":
        yield FakePrepareRepo()
        return
    async with open_db(backend, tmp_path) as db:
        profiles = build_repositories(db).profiles
        for pid in (P1, P2):
            await profiles.save(
                pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=False
            )
        yield SqlPrepareRepo(db)


def item(
    plan_id: str = PID,
    profile_id: str = P1,
    *,
    role_id: str = ROLE,
    requirement: str | None = None,
    created_at: Any = T0,
    version: int = 1,
    content: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> StoredPlan:
    return StoredPlan(
        id=plan_id,
        profile_id=profile_id,
        role_id=role_id,
        requirement=requirement,
        content={"id": plan_id, "roleTitle": "AI Product Engineer", "gaps": [{"name": "LLM evals"}]}
        if content is None
        else content,
        state={"hoursPerWeek": 6, "moveIndex": 0, "done": [], "links": []} if state is None else state,
        version=version,
        created_at=created_at,
        updated_at=created_at,
    )


async def stored(repo: PrepareRepo, plan_id: str = PID, profile_id: str = P1) -> StoredPlan:
    found = await repo.get(profile_id, plan_id)
    assert found is not None
    return found


# ───────────────────────── create and get ─────────────────────────


async def test_a_created_plan_reads_back_as_it_was_written(repo: PrepareRepo) -> None:
    content = {
        "id": PID,
        "roleTitle": "AI Product Engineer “Q3”",
        "n": 3,
        "ratio": 0.5,
        "ok": True,
        "x": None,
    }
    await repo.create(item(content=content, requirement="LLM evals"))
    found = await stored(repo)
    assert (found.id, found.profile_id, found.role_id, found.requirement, found.version) == (
        PID,
        P1,
        ROLE,
        "LLM evals",
        1,
    )
    assert found.content == content
    assert found.state == {"hoursPerWeek": 6, "moveIndex": 0, "done": [], "links": []}
    assert found.created_at == T0 and found.updated_at == T0
    assert found.created_at.tzinfo is not None and found.created_at.utcoffset() == timedelta(0)


async def test_a_plan_is_visible_only_to_its_own_profile(repo: PrepareRepo) -> None:
    await repo.create(item())
    assert await repo.get(P2, PID) is None
    assert await repo.get(P1, OTHER) is None
    assert await repo.find(P2, ROLE, None) is None


async def test_what_comes_back_is_a_copy_and_what_was_handed_in_is_copied_too(repo: PrepareRepo) -> None:
    state: dict[str, Any] = {"done": []}
    await repo.create(item(state=state, content={"gaps": [{"name": "x"}]}))
    state["done"].append("after the write")
    first = await stored(repo)
    first.state["done"].append("changed")
    first.content["gaps"].append("changed")
    again = await stored(repo)
    assert again.state == {"done": []} and again.content == {"gaps": [{"name": "x"}]}


# ───────────────────────── find: the newest for a role and requirement ─────────────────────────


async def test_find_answers_the_newest_plan_for_the_role_and_the_requirement(repo: PrepareRepo) -> None:
    await repo.create(item("1" * 32, created_at=T0))
    await repo.create(item("2" * 32, created_at=T0 + timedelta(minutes=5)))
    await repo.create(item("3" * 32, requirement="LLM evals", created_at=T0 + timedelta(minutes=9)))
    await repo.create(item("4" * 32, role_id="design-engineer", created_at=T0 + timedelta(minutes=9)))
    await repo.create(item("5" * 32, P2, created_at=T0 + timedelta(hours=1)))
    whole = await repo.find(P1, ROLE, None)
    assert whole is not None and whole.id == "2" * 32
    one = await repo.find(P1, ROLE, "LLM evals")
    assert one is not None and one.id == "3" * 32
    assert await repo.find(P1, ROLE, "llm evals") is None  # the requirement's name as the plan stored it
    assert await repo.find(P1, "staff", None) is None
    theirs = await repo.find(P2, ROLE, None)
    assert theirs is not None and theirs.id == "5" * 32


# ───────────────────────── save_state: the version is a compare-and-swap ─────────────────────────


async def test_a_state_save_on_the_expected_version_lands_and_changes_only_the_state(
    repo: PrepareRepo,
) -> None:
    await repo.create(item())
    later = T0 + timedelta(minutes=5)
    new_state = {"hoursPerWeek": 10, "moveIndex": 1, "done": ["m1:0:0"], "links": []}
    assert await repo.save_state(P1, PID, state=new_state, expected_version=1, now=later) is True
    found = await stored(repo)
    assert (found.version, found.state, found.updated_at) == (2, new_state, later)
    assert found.content == item().content and found.created_at == T0 and found.requirement is None


async def test_a_save_over_a_stale_version_writes_nothing(repo: PrepareRepo) -> None:
    await repo.create(item())
    assert await repo.save_state(P1, PID, state={"who": "first tab"}, expected_version=1, now=T0) is True
    assert await repo.save_state(P1, PID, state={"who": "second tab"}, expected_version=1, now=T0) is False
    assert await repo.save_state(P1, PID, state={"who": "ahead"}, expected_version=3, now=T0) is False
    found = await stored(repo)
    assert (found.version, found.state) == (2, {"who": "first tab"})
    assert await repo.save_state(P1, PID, state={"who": "third"}, expected_version=2, now=T0) is True
    assert (await stored(repo)).version == 3


async def test_a_save_for_a_plan_that_is_not_there_or_not_this_profiles_is_refused(repo: PrepareRepo) -> None:
    assert await repo.save_state(P1, PID, state={}, expected_version=1, now=T0) is False
    assert await repo.get(P1, PID) is None
    await repo.create(item())
    assert await repo.save_state(P2, PID, state={"stolen": True}, expected_version=1, now=T0) is False
    found = await stored(repo)
    assert (found.version, found.state) == (1, item().state)


async def test_two_saves_of_the_same_version_at_once_cannot_both_land(repo: PrepareRepo) -> None:
    await repo.create(item())
    won_a, won_b = await asyncio.gather(
        repo.save_state(P1, PID, state={"who": "a"}, expected_version=1, now=T0),
        repo.save_state(P1, PID, state={"who": "b"}, expected_version=1, now=T0),
    )
    assert sorted([won_a, won_b]) == [False, True]
    found = await stored(repo)
    assert found.version == 2 and found.state == ({"who": "a"} if won_a else {"who": "b"})


async def test_a_duplicate_id_is_refused(repo: PrepareRepo) -> None:
    await repo.create(item())
    with pytest.raises(Exception):  # noqa: B017  (an IntegrityError on SQL, a ValueError on the fake)
        await repo.create(item())


# ───────────────────────── the row follows its profile ─────────────────────────


async def test_a_plan_goes_with_its_profile_on_a_real_database(any_real_db: Database) -> None:
    repos = build_repositories(any_real_db)
    for pid in (P1, P2):
        await repos.profiles.save(
            pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=False
        )
    await repos.prepare.create(item("1" * 32, P1))
    await repos.prepare.create(item("2" * 32, P2))
    assert await repos.profiles.purge_expired(T0 + timedelta(days=400)) == 2
    assert await repos.prepare.get(P1, "1" * 32) is None and await repos.prepare.get(P2, "2" * 32) is None


async def test_a_plan_needs_its_profile_row_on_a_real_database(any_real_db: Database) -> None:
    repos = build_repositories(any_real_db)
    with pytest.raises(Exception):  # noqa: B017  (the foreign key refuses it)
        await repos.prepare.create(item("9" * 32, "nobody"))
