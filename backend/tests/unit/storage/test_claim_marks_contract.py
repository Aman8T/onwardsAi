"""Claim marks (I2), run against every backend (the fake, a SQLite file and memory, and Postgres when configured), so
`FakeClaimMarkRepo` cannot drift from `SqlClaimMarkRepo`: a mark only its profile's, a session's marks written once, the
orders, where a Fix step is and whether it is ticked, deleting one session's marks, copies in and out, and (on a real
database) the row going with its profile."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from tayyari.contract.stored import StoredContext
from tayyari.storage.db import Database
from tayyari.storage.ports import ClaimMarkRepo, StoredClaimMark
from tayyari.storage.repos import build_repositories
from tayyari.storage.repos.claim_marks import SqlClaimMarkRepo
from tests.fakes.claim_marks import FakeClaimMarkRepo
from tests.unit.storage.conftest import T0, open_db

P1, P2 = "1" * 32, "2" * 32
S1, S2 = "a" * 32, "b" * 32
PLAN, OTHER_PLAN = "c" * 32, "d" * 32
LCP = "LCP on the payment step dropped from 4.1s to 1.9s"


@pytest_asyncio.fixture
async def repo(backend: str, tmp_path: Path) -> AsyncIterator[ClaimMarkRepo]:
    """The mark repository on each backend, with two profiles stored (a mark's row refers to its profile)."""
    if backend == "fake":
        yield FakeClaimMarkRepo()
        return
    async with open_db(backend, tmp_path) as db:
        profiles = build_repositories(db).profiles
        for pid in (P1, P2):
            await profiles.save(
                pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=False
            )
        yield SqlClaimMarkRepo(db)


def mark(
    mark_id: str,
    profile_id: str = P1,
    session_id: str = S1,
    slot_id: str = "s1",
    *,
    text: str = LCP,
    verdict: str = "shaky",
    on: date = date(2026, 10, 16),
    created_at: Any = T0,
    fix_text: str | None = "Find the pull request behind the LCP change.",
    story: dict[str, Any] | None = None,
) -> StoredClaimMark:
    return StoredClaimMark(
        id=mark_id,
        profile_id=profile_id,
        claim_key=text.lower(),
        claim_text=text,
        source="Resume · line 15",
        session_id=session_id,
        slot_id=slot_id,
        session_kind="resume",
        slot_kind="claim",
        verdict=verdict,
        rung="push",
        question="What moved LCP the most, and how do you know?",
        marked_on=on,
        created_at=created_at,
        quote="loading the payment provider's script only when the pay step came into view",
        story=story,
        fix_text=fix_text,
        fix_minutes=None if fix_text is None else 20,
    )


async def got(repo: ClaimMarkRepo, mark_id: str, profile_id: str = P1) -> StoredClaimMark:
    found = await repo.get(profile_id, mark_id)
    assert found is not None
    return found


# ───────────────────────── add: written once ─────────────────────────


async def test_an_added_mark_reads_back_as_it_was_written(repo: ClaimMarkRepo) -> None:
    item = mark("m1", story={"situation": True, "action": True, "result": False, "reflection": False})
    assert await repo.add([item]) == 1
    found = await got(repo, "m1")
    assert found == item
    assert found.created_at.tzinfo is not None and found.created_at.utcoffset() == timedelta(0)
    assert found.fix_kept is False and found.fix_plan_id is None and found.fix_added_at is None


async def test_a_sessions_marks_are_written_once_and_a_second_write_changes_nothing(
    repo: ClaimMarkRepo,
) -> None:
    assert await repo.add([mark("m1"), mark("m2", slot_id="s2")]) == 2
    assert await repo.place_fix(P1, "m1", plan_id=PLAN, now=T0)
    assert await repo.set_fix_done(P1, "m1", T0 + timedelta(days=1))
    again = [
        mark("x1", verdict="defended", fix_text=None),
        mark("x2", slot_id="s2"),
        mark("m3", slot_id="s3"),
    ]
    assert await repo.add(again) == 1
    first = await got(repo, "m1")
    assert (first.verdict, first.fix_plan_id, first.fix_done_at) == ("shaky", PLAN, T0 + timedelta(days=1))
    assert await repo.get(P1, "x1") is None and await repo.get(P1, "x2") is None
    assert await repo.get(P1, "m3") is not None


async def test_the_same_slot_of_the_same_session_may_be_marked_for_another_profile(
    repo: ClaimMarkRepo,
) -> None:
    assert await repo.add([mark("m1"), mark("m2", P2)]) == 2


# ───────────────────────── reads, scoped to the profile ─────────────────────────


async def test_a_mark_is_visible_only_to_its_own_profile(repo: ClaimMarkRepo) -> None:
    await repo.add([mark("m1")])
    assert await repo.get(P2, "m1") is None
    assert await repo.find(P2, S1, "s1") is None
    assert await repo.list_for_profile(P2, 10) == []
    assert await repo.session_ids(P2) == set()
    found = await repo.find(P1, S1, "s1")
    assert found is not None and found.id == "m1"
    assert await repo.find(P1, S1, "s9") is None


async def test_a_profiles_marks_come_oldest_first(repo: ClaimMarkRepo) -> None:
    await repo.add(
        [
            mark("m3", session_id=S2, on=date(2026, 10, 16), created_at=T0 + timedelta(days=9)),
            mark("m1", on=date(2026, 10, 12), created_at=T0 + timedelta(days=5)),
            mark("m2", slot_id="s2", on=date(2026, 10, 12), created_at=T0 + timedelta(days=5)),
            mark("mx", P2, on=date(2026, 10, 1)),
        ]
    )
    assert [m.id for m in await repo.list_for_profile(P1, 10)] == ["m1", "m2", "m3"]
    assert [m.id for m in await repo.list_for_profile(P1, 2)] == ["m1", "m2"]
    assert await repo.session_ids(P1) == {S1, S2}


async def test_what_comes_back_is_a_copy(repo: ClaimMarkRepo) -> None:
    story: dict[str, Any] = {"situation": True}
    await repo.add([mark("m1", story=story)])
    story["action"] = True
    first = await got(repo, "m1")
    assert first.story is not None
    first.story["result"] = True
    assert (await got(repo, "m1")).story == {"situation": True}


# ───────────────────────── where the Fix step is ─────────────────────────


async def test_a_fix_step_goes_into_a_plan_then_moves_to_practice_and_keeps_its_tick(
    repo: ClaimMarkRepo,
) -> None:
    await repo.add([mark("m1")])
    assert await repo.place_fix(P1, "m1", plan_id=PLAN, now=T0)
    assert [m.id for m in await repo.fixes_for_plan(P1, PLAN)] == ["m1"]
    assert await repo.kept_fixes(P1) == []
    assert await repo.set_fix_done(P1, "m1", T0 + timedelta(hours=2))
    assert await repo.place_fix(P1, "m1", plan_id=None, now=T0 + timedelta(days=1))
    moved = await got(repo, "m1")
    assert (moved.fix_plan_id, moved.fix_kept) == (None, True)
    assert moved.fix_added_at == T0 + timedelta(days=1) and moved.fix_done_at == T0 + timedelta(hours=2)
    assert await repo.fixes_for_plan(P1, PLAN) == []
    assert [m.id for m in await repo.kept_fixes(P1)] == ["m1"]


async def test_a_mark_without_a_fix_step_cannot_be_placed(repo: ClaimMarkRepo) -> None:
    await repo.add([mark("m1", verdict="defended", fix_text=None)])
    assert not await repo.place_fix(P1, "m1", plan_id=PLAN, now=T0)
    assert (await got(repo, "m1")).fix_plan_id is None


async def test_only_a_placed_fix_step_can_be_ticked_or_cleared(repo: ClaimMarkRepo) -> None:
    await repo.add([mark("m1")])
    assert not await repo.set_fix_done(P1, "m1", T0)
    assert not await repo.clear_fix(P1, "m1")
    assert await repo.place_fix(P1, "m1", plan_id=None, now=T0)
    assert await repo.set_fix_done(P1, "m1", T0)
    assert await repo.set_fix_done(P1, "m1", None)
    assert (await got(repo, "m1")).fix_done_at is None
    assert await repo.set_fix_done(P1, "m1", T0)
    assert await repo.clear_fix(P1, "m1")
    cleared = await got(repo, "m1")
    assert (cleared.fix_plan_id, cleared.fix_kept, cleared.fix_added_at, cleared.fix_done_at) == (
        None,
        False,
        None,
        None,
    )
    assert cleared.fix_text == "Find the pull request behind the LCP change."


async def test_another_profile_cannot_move_tick_or_clear_a_fix_step(repo: ClaimMarkRepo) -> None:
    await repo.add([mark("m1")])
    assert not await repo.place_fix(P2, "m1", plan_id=PLAN, now=T0)
    assert await repo.place_fix(P1, "m1", plan_id=PLAN, now=T0)
    assert not await repo.set_fix_done(P2, "m1", T0)
    assert not await repo.clear_fix(P2, "m1")
    assert await repo.fixes_for_plan(P2, PLAN) == []
    assert (await got(repo, "m1")).fix_plan_id == PLAN


async def test_fix_steps_come_in_the_order_they_were_placed(repo: ClaimMarkRepo) -> None:
    await repo.add([mark("m1"), mark("m2", slot_id="s2"), mark("m3", slot_id="s3"), mark("m4", slot_id="s4")])
    await repo.place_fix(P1, "m2", plan_id=PLAN, now=T0)
    await repo.place_fix(P1, "m1", plan_id=PLAN, now=T0 + timedelta(minutes=5))
    await repo.place_fix(P1, "m3", plan_id=OTHER_PLAN, now=T0 + timedelta(minutes=1))
    await repo.place_fix(P1, "m4", plan_id=None, now=T0 + timedelta(minutes=2))
    assert [m.id for m in await repo.fixes_for_plan(P1, PLAN)] == ["m2", "m1"]
    assert [m.id for m in await repo.fixes_for_plan(P1, OTHER_PLAN)] == ["m3"]
    assert [m.id for m in await repo.kept_fixes(P1)] == ["m4"]


# ───────────────────────── deleting a session's marks ─────────────────────────


async def test_deleting_a_sessions_marks_takes_only_that_session_of_that_profile(repo: ClaimMarkRepo) -> None:
    await repo.add([mark("m1"), mark("m2", slot_id="s2"), mark("m3", session_id=S2), mark("m4", P2)])
    assert await repo.delete_for_session(P2, S2) == 0
    assert await repo.delete_for_session(P1, S1) == 2
    assert [m.id for m in await repo.list_for_profile(P1, 10)] == ["m3"]
    assert [m.id for m in await repo.list_for_profile(P2, 10)] == ["m4"]


# ───────────────────────── the row follows its profile ─────────────────────────


async def test_a_mark_goes_with_its_profile_on_a_real_database(any_real_db: Database) -> None:
    repos = build_repositories(any_real_db)
    for pid in (P1, P2):
        await repos.profiles.save(
            pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=False
        )
    await repos.claim_marks.add([mark("m1"), mark("m2", P2)])
    assert await repos.profiles.purge_expired(T0 + timedelta(days=400)) == 2
    assert await repos.claim_marks.get(P1, "m1") is None and await repos.claim_marks.get(P2, "m2") is None


async def test_a_mark_needs_its_profile_row_on_a_real_database(any_real_db: Database) -> None:
    repos = build_repositories(any_real_db)
    with pytest.raises(Exception):  # noqa: B017  (the foreign key refuses it)
        await repos.claim_marks.add([replace(mark("m9"), profile_id="nobody")])
