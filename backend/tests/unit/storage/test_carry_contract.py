"""`ProfileCarryRepo` on every backend (the fake, SQLite file and memory, Postgres when configured), so `FakeProfileCarryRepo`
cannot drift from `SqlProfileCarryRepo`: resumes, resume files, own-words lines and dashboard notes move from the old
profile to the new one in one transaction; the default flag is carried; the new profile's clashing rows are replaced by the
moved ones; only the account that may open the old profile may carry, and then it owns the new one; a failure leaves both
profiles as they were."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from tayyari.contract.stored import StoredContext
from tayyari.match.facts import user_fact
from tayyari.storage.ports import StoredDeskNote, StoredResume, StoredResumeFile
from tests.unit.storage.conftest import T0, Env

OLD, NEW, OTHER = "1" * 32, "2" * 32, "3" * 32
BOOK: dict[str, Any] = {"name": "Meera Nair", "facts": [], "entries": []}
LINE = "Ran the paid-search budget alone for two quarters"


async def profile(env: Env, pid: str, *, user_id: str | None = None, sample: bool = False) -> None:
    await env.repos.profiles.save(
        pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=sample
    )
    if user_id is not None:
        await env.repos.users.claim_profile(user_id, pid)


async def account(env: Env, email: str = "ana@example.com") -> str:
    return (await env.repos.users.sign_in_email(email=email, now=T0)).id


def resume(
    rid: str, pid: str, *, kind: Any = "upload", role: str = "", default: bool = False
) -> StoredResume:
    return StoredResume(
        id=rid,
        user_id=None,
        profile_id=pid,
        kind=kind,
        role_key=role,
        title="Resume",
        draft={"header": {"name": "Meera", "contact": []}, "experience": []},
        final=None,
        facts=dict(BOOK),
        is_default=default,
        version=1,
        created_at=T0,
        updated_at=T0,
        finalised_at=None,
    )


def file(fid: str, pid: str, age: int = 0) -> StoredResumeFile:
    return StoredResumeFile(
        id=fid,
        user_id=None,
        profile_id=pid,
        origin="upload",
        filename=f"{fid[:3]}.txt",
        mime="text/plain",
        size=3,
        sha256=fid[0] * 64,
        text="Meera Nair",
        facts={},
        merge=None,
        merged_book=None,
        created_at=T0 + timedelta(minutes=age),
    )


async def said(env: Env, pid: str, text: str = LINE) -> None:
    fact = user_fact(text, key="resume:abc/b-exp-1.1")
    await env.repos.user_facts.add(
        None, pid, fact, question="A line of your resume, in your own words", answer=text, match_id=None
    )


async def stock(env: Env, pid: str, *, ids: str, default: bool = True) -> None:
    await env.repos.resumes.create(resume(ids * 32, pid, default=default))
    await env.repos.resumes.create(resume(ids.upper() * 32, pid, kind="path", role="growth"))
    await env.repos.resume_files.add(file((ids + "f") * 16, pid), b"abc")
    await said(env, pid)


async def test_everything_moves_and_the_old_profile_is_left_empty(env: Env) -> None:
    user = await account(env)
    await profile(env, OLD, user_id=user)
    await profile(env, NEW)
    await stock(env, OLD, ids="a")
    await env.repos.desk_notes.replace(
        user, OLD, [StoredDeskNote(id="n1", kind="note", text="call Sam", at=5)], T0
    )

    moved = await env.repos.carry.carry(OLD, NEW, user)

    assert (moved.resumes, moved.resume_files, moved.user_facts, moved.desk_notes) == (2, 1, 1, 1)
    mine = await env.repos.resumes.list_for_profile(NEW)
    assert sorted((r.kind, r.role_key) for r in mine) == [("path", "growth"), ("upload", "")]
    assert [r.is_default for r in mine if r.kind == "upload"] == [True]  # the default flag is carried
    assert {r.user_id for r in mine} == {user}
    assert [f.filename for f in await env.repos.resume_files.list_for_profile(NEW)] == ["afa.txt"]
    assert [f.text for f in await env.repos.user_facts.list_for_profile(NEW)] == [LINE]
    assert [n.text for n in await env.repos.desk_notes.list(user, NEW)] == ["call Sam"]
    for repo_rows in (
        await env.repos.resumes.list_for_profile(OLD),
        await env.repos.resume_files.list_for_profile(OLD),
        await env.repos.user_facts.list_for_profile(OLD),
        await env.repos.desk_notes.list(user, OLD),
    ):
        assert repo_rows == []
    # the signed-in caller owns the new profile now
    assert (await env.repos.profiles.get_access(NEW)).user_id == user  # type: ignore[union-attr]


async def test_a_clash_keeps_the_moved_row_and_one_default(env: Env) -> None:
    user = await account(env)
    await profile(env, OLD, user_id=user)
    await profile(env, NEW, user_id=user)
    await stock(env, OLD, ids="a")
    # the new profile already made its own uploaded resume (the default), another path resume, and the same line
    await env.repos.resumes.create(resume("d" * 32, NEW, default=True))
    await env.repos.resumes.create(resume("e" * 32, NEW, kind="path", role="other"))
    await said(env, NEW)

    await env.repos.carry.carry(OLD, NEW, user)

    mine = {r.id: r for r in await env.repos.resumes.list_for_profile(NEW)}
    assert set(mine) == {"a" * 32, "A" * 32, "e" * 32}  # the clashing upload "d" is replaced by the moved "a"
    assert [r.id for r in mine.values() if r.is_default] == ["a" * 32]
    assert [f.text for f in await env.repos.user_facts.list_for_profile(NEW)] == [LINE]  # one row, not two


async def test_a_profile_nobody_claimed_is_carried_without_an_account(env: Env) -> None:
    await profile(env, OLD)
    await profile(env, NEW)
    await stock(env, OLD, ids="a")
    moved = await env.repos.carry.carry(OLD, NEW, None)
    assert (moved.resumes, moved.resume_files, moved.user_facts, moved.desk_notes) == (2, 1, 1, 0)
    assert len(await env.repos.resumes.list_for_profile(NEW)) == 2


async def test_another_accounts_profile_is_refused_and_nothing_moves(env: Env) -> None:
    ana, bo = await account(env), await account(env, "bo@example.com")
    await profile(env, OLD, user_id=ana)
    await profile(env, NEW)
    await stock(env, OLD, ids="a")
    with pytest.raises(PermissionError):
        await env.repos.carry.carry(OLD, NEW, bo)
    assert len(await env.repos.resumes.list_for_profile(OLD)) == 2
    assert await env.repos.resumes.list_for_profile(NEW) == []
    assert (await env.repos.profiles.get_access(NEW)).user_id is None  # type: ignore[union-attr]


async def test_a_new_profile_of_another_account_is_refused(env: Env) -> None:
    ana, bo = await account(env), await account(env, "bo@example.com")
    await profile(env, OLD, user_id=ana)
    await profile(env, NEW, user_id=bo)
    await stock(env, OLD, ids="a")
    with pytest.raises(PermissionError):
        await env.repos.carry.carry(OLD, NEW, ana)
    assert len(await env.repos.resumes.list_for_profile(OLD)) == 2


async def test_a_sample_run_and_an_unknown_profile_are_refused(env: Env) -> None:
    await profile(env, OLD, sample=True)
    await profile(env, NEW)
    with pytest.raises(PermissionError):
        await env.repos.carry.carry(OLD, NEW, None)
    with pytest.raises(PermissionError):
        await env.repos.carry.carry(OTHER, NEW, None)
    with pytest.raises(PermissionError):
        await env.repos.carry.carry(NEW, NEW, None)


async def test_a_failure_part_way_leaves_both_profiles_as_they_were(env: Env) -> None:
    if env.fakes is None:
        pytest.skip("a failure is injected into the fake; the SQL repo is one transaction by construction")
    user = await account(env)
    await profile(env, OLD, user_id=user)
    await profile(env, NEW)
    await stock(env, OLD, ids="a")
    env.fakes.carry.fail_at = "facts"

    with pytest.raises(RuntimeError):
        await env.repos.carry.carry(OLD, NEW, user)

    assert len(await env.repos.resumes.list_for_profile(OLD)) == 2
    assert len(await env.repos.resume_files.list_for_profile(OLD)) == 1
    assert len(await env.repos.user_facts.list_for_profile(OLD)) == 1
    assert await env.repos.resumes.list_for_profile(NEW) == []
    assert await env.repos.resume_files.list_for_profile(NEW) == []
    assert (await env.repos.profiles.get_access(NEW)).user_id is None  # type: ignore[union-attr]
