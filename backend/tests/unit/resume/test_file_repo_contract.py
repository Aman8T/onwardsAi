"""Resume files in storage, run against every backend (the fake, a SQLite file and memory, and Postgres when configured) so
`FakeResumeFileRepo` cannot drift from `SqlResumeFileRepo`: the bytes kept apart from the rows, listing oldest first, a
second add of one id changing nothing, copies in and out, claiming a profile, the migration's table and (on a real
database) the rows going with their profile."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import inspect, text

from tayyari.contract.stored import StoredContext
from tayyari.storage.db import Database
from tayyari.storage.ports import ResumeFileRepo, StoredResumeFile
from tayyari.storage.repos import build_repositories
from tayyari.storage.repos.resume_files import SqlResumeFileRepo
from tests.fakes.resume_files import FakeResumeFileRepo
from tests.unit.storage.conftest import T0, open_db

P1, P2 = "1" * 32, "2" * 32
A, B, C = "a" * 32, "b" * 32, "c" * 32
BOOK: dict[str, Any] = {"name": "Meera Nair", "facts": [], "entries": []}
BYTES = bytes(range(256)) * 40


@pytest_asyncio.fixture
async def repo(backend: str, tmp_path: Path) -> AsyncIterator[ResumeFileRepo]:
    if backend == "fake":
        yield FakeResumeFileRepo()
        return
    async with open_db(backend, tmp_path) as db:
        profiles = build_repositories(db).profiles
        for pid in (P1, P2):
            await profiles.save(
                pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=False
            )
        yield SqlResumeFileRepo(db)


def item(file_id: str = A, profile_id: str = P1, *, origin: Any = "upload", age: int = 0) -> StoredResumeFile:
    return StoredResumeFile(
        id=file_id,
        user_id=None,
        profile_id=profile_id,
        origin=origin,
        filename="meera.pdf",
        mime="application/pdf",
        size=len(BYTES),
        sha256="9" * 64,
        text="Meera Nair\nGrowth Marketing Manager",
        facts=dict(BOOK),
        merge=None if origin == "analysis" else {"fileId": file_id, "filename": "meera.pdf"},
        merged_book=None if origin == "analysis" else dict(BOOK),
        created_at=T0 + timedelta(minutes=age),
    )


async def test_a_file_round_trips_and_its_bytes_are_read_apart(repo: ResumeFileRepo) -> None:
    stored = await repo.add(item(), BYTES)
    assert stored == item() or stored.created_at.replace(tzinfo=None) == item().created_at.replace(
        tzinfo=None
    )
    got = await repo.get(A)
    assert got is not None
    assert (got.filename, got.mime, got.size, got.sha256, got.origin, got.text) == (
        "meera.pdf",
        "application/pdf",
        len(BYTES),
        "9" * 64,
        "upload",
        "Meera Nair\nGrowth Marketing Manager",
    )
    assert (
        got.facts == BOOK and got.merge == {"fileId": A, "filename": "meera.pdf"} and got.merged_book == BOOK
    )
    assert await repo.get_data(A) == BYTES
    assert await repo.get("x" * 32) is None and await repo.get_data("x" * 32) is None


async def test_the_analysis_file_has_no_merge(repo: ResumeFileRepo) -> None:
    await repo.add(item(origin="analysis"), BYTES)
    got = await repo.get(A)
    assert got is not None and got.origin == "analysis" and got.merge is None and got.merged_book is None


async def test_a_second_add_of_one_id_changes_nothing(repo: ResumeFileRepo) -> None:
    await repo.add(item(), BYTES)
    again = await repo.add(replace(item(), filename="other.pdf"), b"other")
    assert again.filename == "meera.pdf"
    assert await repo.get_data(A) == BYTES


async def test_files_list_oldest_first_for_their_own_profile(repo: ResumeFileRepo) -> None:
    await repo.add(item(C, age=5), b"c")
    await repo.add(item(A, age=1), b"a")
    await repo.add(item(B, P2, age=2), b"b")
    assert [f.id for f in await repo.list_for_profile(P1)] == [A, C]
    assert [f.id for f in await repo.list_for_profile(P2)] == [B]
    assert await repo.list_for_profile("9" * 32) == []


async def test_a_claimed_profile_hands_its_files_to_the_account(repo: ResumeFileRepo, backend: str) -> None:
    await repo.add(item(), b"a")
    if backend == "fake":
        assert await repo.adopt(P1, "u1") == 1
        got = await repo.get(A)
        assert got is not None and got.user_id == "u1"
        assert await repo.adopt(P1, "u2") == 0  # only files with no account are handed over
        return
    # a real database enforces the account's foreign key: the account must exist
    assert await repo.adopt(P2, "nobody") == 0


async def test_what_comes_out_is_a_copy(repo: ResumeFileRepo) -> None:
    await repo.add(item(), BYTES)
    got = await repo.get(A)
    assert got is not None
    got.facts["facts"].append("tampered")
    again = await repo.get(A)
    assert again is not None and again.facts == BOOK


async def test_the_table_is_what_the_migration_made(backend: str, tmp_path: Path) -> None:
    if backend == "fake":
        pytest.skip("storage only")
    async with open_db(backend, tmp_path) as db:
        names = await _table_columns(db)
        assert names == {
            "id", "user_id", "profile_id", "origin", "filename", "mime", "size", "sha256", "data", "text",
            "facts", "merge", "merged_book", "created_at",
        }  # fmt: skip


async def test_files_go_with_their_profile(backend: str, tmp_path: Path) -> None:
    if backend == "fake":
        pytest.skip("no foreign keys in the fake")
    async with open_db(backend, tmp_path) as db:
        repos = build_repositories(db)
        await repos.profiles.save(
            P1, {"id": P1}, StoredContext(), goal=None, owner_hash=None, is_sample=False
        )
        await repos.resume_files.add(item(), b"a")
        async with db.session() as s:
            await s.execute(text("DELETE FROM profiles WHERE id = :p"), {"p": P1})
        assert await repos.resume_files.get(A) is None


async def _table_columns(db: Database) -> set[str]:
    async with db.engine.connect() as conn:
        return set(
            await conn.run_sync(lambda c: {col["name"] for col in inspect(c).get_columns("resume_files")})
        )
