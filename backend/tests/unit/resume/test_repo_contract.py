"""Resumes in storage, run against every backend (the fake, a SQLite file and memory, and Postgres when configured), so
`FakeResumeRepo` cannot drift from `SqlResumeRepo`: one resume per ``(profile, kind, role)``, the draft's
compare-and-swap, the one default that only a finalised resume can be, copies in and out, the migration's table, and
(on a real database) the rows going with their profile."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import inspect

from tayyari.contract.stored import StoredContext
from tayyari.storage.db import Database
from tayyari.storage.migrate import head_revision
from tayyari.storage.ports import ResumeRepo, StoredResume
from tayyari.storage.repos import build_repositories
from tayyari.storage.repos.resumes import SqlResumeRepo
from tests.fakes.repos import FakeRepositories
from tests.fakes.resumes import FakeResumeRepo
from tests.unit.storage.conftest import T0, open_db

P1, P2 = "1" * 32, "2" * 32
UPLOAD, PATH, OTHER = "a" * 32, "b" * 32, "c" * 32
ROLE = "senior-growth"
DRAFT: dict[str, Any] = {"header": {"name": "Meera Nair", "contact": []}, "experience": []}
FINAL: dict[str, Any] = {**DRAFT, "lint": {"items": []}}
BOOK: dict[str, Any] = {"name": "Meera Nair", "facts": [], "entries": []}


@pytest_asyncio.fixture
async def repo(backend: str, tmp_path: Path) -> AsyncIterator[ResumeRepo]:
    """The resume repository on each backend, with two profiles stored (a resume's row refers to its profile)."""
    if backend == "fake":
        yield FakeResumeRepo()
        return
    async with open_db(backend, tmp_path) as db:
        profiles = build_repositories(db).profiles
        for pid in (P1, P2):
            await profiles.save(
                pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=False
            )
        yield SqlResumeRepo(db)


def item(
    resume_id: str = UPLOAD,
    profile_id: str = P1,
    *,
    kind: Any = "upload",
    role_key: str = "",
    final: dict[str, Any] | None = None,
    is_default: bool = False,
    created_at: Any = T0,
) -> StoredResume:
    return StoredResume(
        id=resume_id,
        user_id=None,
        profile_id=profile_id,
        kind=kind,
        role_key=role_key,
        title="Resume" if kind == "upload" else "Senior Growth Marketing Manager",
        draft=dict(DRAFT),
        final=final,
        facts=dict(BOOK),
        is_default=is_default,
        version=1,
        created_at=created_at,
        updated_at=created_at,
        finalised_at=created_at if final is not None else None,
    )


async def stored(repo: ResumeRepo, resume_id: str) -> StoredResume:
    found = await repo.get(resume_id)
    assert found is not None
    return found


async def test_a_resume_is_stored_and_read_back_as_it_was(repo: ResumeRepo) -> None:
    made = await repo.create(item(final=dict(FINAL), is_default=True))
    assert made == item(final=dict(FINAL), is_default=True)
    assert await repo.get(UPLOAD) == made
    assert await repo.find(P1, "upload", "") == made
    assert await repo.get(OTHER) is None and await repo.find(P2, "upload", "") is None
    assert made.final == FINAL and made.finalised_at == T0


async def test_a_resume_with_no_finalised_version_reads_back_with_none(repo: ResumeRepo) -> None:
    made = await repo.create(item(PATH, kind="path", role_key=ROLE))
    assert made.final is None and made.finalised_at is None and not made.is_default


async def test_a_profile_has_one_resume_of_each_kind_and_role(repo: ResumeRepo) -> None:
    first = await repo.create(item(UPLOAD))
    second = await repo.create(item(OTHER))  # a second first request, with its own id

    assert second == first  # the one already stored is handed back, nothing is written
    assert await repo.get(OTHER) is None
    await repo.create(item(PATH, kind="path", role_key=ROLE))
    again = await repo.create(item(OTHER, kind="path", role_key=ROLE))
    assert again.id == PATH
    # another role, and another profile, are other resumes
    await repo.create(item("d" * 32, kind="path", role_key="head-of-growth"))
    await repo.create(item("e" * 32, P2))
    assert [r.id for r in await repo.list_for_profile(P1)] == [UPLOAD, PATH, "d" * 32]


async def test_a_profiles_resumes_are_listed_oldest_first(repo: ResumeRepo) -> None:
    await repo.create(item(PATH, kind="path", role_key=ROLE, created_at=T0 + timedelta(minutes=5)))
    await repo.create(item(UPLOAD, created_at=T0))
    await repo.create(item(OTHER, P2))
    assert [r.id for r in await repo.list_for_profile(P1)] == [UPLOAD, PATH]
    assert await repo.list_for_profile("9" * 32) == []


async def test_a_save_lands_only_on_the_version_it_read(repo: ResumeRepo) -> None:
    made = await repo.create(item())
    later = T0 + timedelta(minutes=1)
    edited = replace(
        made,
        draft={**DRAFT, "experience": [{"entryFactId": "exp-1"}]},
        facts={**BOOK, "facts": [{"id": "user-1"}]},
        final=dict(FINAL),
        finalised_at=later,
        updated_at=later,
        version=2,
    )

    assert await repo.save(edited, expected_version=1) is True
    assert (
        await repo.save(replace(edited, draft=dict(DRAFT), version=2), expected_version=1) is False
    )  # stale

    now = await stored(repo, UPLOAD)
    assert now == edited and now.version == 2
    assert await repo.save(replace(edited, id=OTHER), expected_version=2) is False  # no such resume


async def test_a_save_never_changes_the_owner_the_title_or_the_default(repo: ResumeRepo) -> None:
    made = await repo.create(item(final=dict(FINAL), is_default=True))
    sneaky = replace(
        made, user_id="someone", title="Renamed", is_default=False, kind="path", role_key=ROLE, version=2
    )
    assert await repo.save(sneaky, expected_version=1) is True
    now = await stored(repo, UPLOAD)
    assert (now.user_id, now.title, now.is_default, now.kind, now.role_key) == (
        None,
        "Resume",
        True,
        "upload",
        "",
    )
    assert now.version == 2


async def test_the_default_moves_and_only_onto_a_finalised_resume(repo: ResumeRepo) -> None:
    await repo.create(item(UPLOAD, final=dict(FINAL), is_default=True))
    await repo.create(item(PATH, kind="path", role_key=ROLE))
    await repo.create(item(OTHER, P2, final=dict(FINAL), is_default=True))

    assert await repo.set_default(P1, PATH) is False  # it has no finalised version
    assert [r.is_default for r in await repo.list_for_profile(P1)] == [True, False]

    path = await stored(repo, PATH)
    assert await repo.save(replace(path, final=dict(FINAL), finalised_at=T0, version=2), expected_version=1)
    assert await repo.set_default(P1, PATH) is True
    assert [(r.id, r.is_default) for r in await repo.list_for_profile(P1)] == [(UPLOAD, False), (PATH, True)]
    assert (await stored(repo, OTHER)).is_default is True  # another profile's default is its own

    assert await repo.set_default(P2, PATH) is False  # not that profile's resume
    assert await repo.set_default(P1, "f" * 32) is False
    assert [(r.id, r.is_default) for r in await repo.list_for_profile(P1)] == [(UPLOAD, False), (PATH, True)]
    assert await repo.set_default(P1, UPLOAD) is True
    assert [(r.id, r.is_default) for r in await repo.list_for_profile(P1)] == [(UPLOAD, True), (PATH, False)]


async def test_a_claimed_profiles_resumes_are_adopted_once(repo: ResumeRepo) -> None:
    await repo.create(item(UPLOAD))
    await repo.create(item(PATH, kind="path", role_key=ROLE))
    await repo.create(item(OTHER, P2))
    if isinstance(repo, SqlResumeRepo):
        pytest.skip("adopting needs a users row on a real database: covered by the integration tests")
    assert await repo.adopt(P1, "u1") == 2
    assert await repo.adopt(P1, "u2") == 0  # they have an account now
    assert {r.user_id for r in await repo.list_for_profile(P1)} == {"u1"}
    assert (await stored(repo, OTHER)).user_id is None


async def test_what_is_read_is_a_copy(repo: ResumeRepo) -> None:
    await repo.create(item())
    first = await stored(repo, UPLOAD)
    first.draft["header"]["name"] = "Changed in the test"
    first.facts["facts"].append({"id": "x"})
    again = await stored(repo, UPLOAD)
    assert again.draft == DRAFT and again.facts == BOOK


def test_the_fake_bundle_carries_the_resume_repo() -> None:
    fakes = FakeRepositories()
    assert fakes.bundle.resumes is fakes.resumes and isinstance(fakes.resumes, FakeResumeRepo)


# ───────────────────────── on a real database ─────────────────────────


@pytest_asyncio.fixture(params=["sqlite_file", "postgres"])
async def db(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Database]:
    async with open_db(str(request.param), tmp_path) as database:
        yield database


async def test_the_migration_makes_the_table_the_spec_describes(db: Database) -> None:
    def describe(conn: Any) -> tuple[dict[str, bool], list[Any], list[Any], set[tuple[str, str | None]]]:
        found = inspect(conn)
        columns = {c["name"]: bool(c["nullable"]) for c in found.get_columns("resumes")}
        uniques = [tuple(u["column_names"]) for u in found.get_unique_constraints("resumes")]
        indexes = [(i["name"], tuple(i["column_names"])) for i in found.get_indexes("resumes")]
        keys = {
            (fk["referred_table"], (fk.get("options") or {}).get("ondelete"))
            for fk in found.get_foreign_keys("resumes")
        }
        return columns, uniques, indexes, keys

    async with db.engine.connect() as conn:
        columns, uniques, indexes, keys = await conn.run_sync(describe)

    assert head_revision() >= "0018"
    assert columns == {
        "id": False,
        "user_id": True,
        "profile_id": False,
        "kind": False,
        "role_key": False,
        "title": False,
        "draft": False,
        "final": True,
        "facts": False,
        "is_default": False,
        "version": False,
        "created_at": False,
        "updated_at": False,
        "finalised_at": True,
    }
    assert ("profile_id", "kind", "role_key") in uniques
    assert ("ix_resumes_profile_id", ("profile_id",)) in indexes
    assert keys == {("users", "CASCADE"), ("profiles", "CASCADE")}


async def test_a_resume_goes_with_its_profile_and_with_its_account(db: Database) -> None:
    repos = build_repositories(db)
    for pid in (P1, P2):
        await repos.profiles.save(
            pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=False
        )
    user = await repos.users.sign_in_email(email="ana@example.com", now=T0)
    await repos.users.claim_profile(user.id, P1)
    await repos.resumes.create(item(UPLOAD))
    await repos.resumes.create(item(OTHER, P2))

    assert await repos.resumes.adopt(P1, user.id) == 1
    assert (await stored(repos.resumes, UPLOAD)).user_id == user.id
    assert await repos.users.delete(user.id) is True

    assert await repos.resumes.get(UPLOAD) is None  # gone with the account and its profile
    assert await repos.resumes.get(OTHER) is not None
