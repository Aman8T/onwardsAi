"""`Database`: engine construction, SQLite pragmas, the transactional session helper."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from tayyari.core.dburl import DbConfig, normalise_database_url
from tayyari.storage.db import Database


def sqlite_config(path: Path) -> DbConfig:
    return normalise_database_url(f"sqlite:///{path.as_posix()}", data_dir=path.parent, env="test")


async def pragma(db: Database, name: str) -> object:
    async with db.session() as s:
        return (await s.execute(text(f"PRAGMA {name}"))).scalar_one()


async def test_file_database_gets_wal_foreign_keys_and_busy_timeout(tmp_path: Path) -> None:
    db = Database(sqlite_config(tmp_path / "a" / "b" / "tayyari.sqlite3"))
    try:
        assert (tmp_path / "a" / "b").is_dir()  # the parent folder is created
        assert str(await pragma(db, "journal_mode")).lower() == "wal"
        assert await pragma(db, "foreign_keys") == 1
        assert await pragma(db, "busy_timeout") == 5000
        assert await pragma(db, "synchronous") == 1  # NORMAL
        assert (db.dialect, db.kind, db.ephemeral) == ("sqlite", "sqlite", False)
    finally:
        await db.dispose()


async def test_memory_database_shares_one_connection_and_keeps_foreign_keys(tmp_path: Path) -> None:
    cfg = normalise_database_url("sqlite+aiosqlite://", data_dir=tmp_path, env="test")
    db = Database(cfg)
    try:
        assert isinstance(db.engine.pool, StaticPool)
        assert db.ephemeral is True
        assert await pragma(db, "foreign_keys") == 1
        async with db.session() as s:
            await s.execute(text("CREATE TABLE t (x INTEGER)"))
            await s.execute(text("INSERT INTO t VALUES (1)"))
        async with db.session() as s:  # the same connection: the table is still there
            assert (await s.execute(text("SELECT x FROM t"))).scalar_one() == 1
    finally:
        await db.dispose()


async def test_prod_sqlite_is_flagged_ephemeral(tmp_path: Path) -> None:
    cfg = normalise_database_url(None, data_dir=tmp_path / "data", env="prod")
    db = Database(cfg)
    try:
        assert db.ephemeral is True
        assert (tmp_path / "data").is_dir()
    finally:
        await db.dispose()


async def test_postgres_config_maps_to_the_postgresql_dialect() -> None:
    cfg = normalise_database_url(
        "postgresql://u:secret@ep-x-pooler.eu.aws.neon.tech/db?sslmode=require",
        data_dir=Path("."),
        env="prod",
    )
    db = Database(cfg)  # builds the engine without connecting
    try:
        assert (db.dialect, db.kind, db.ephemeral) == ("postgresql", "postgres", False)
        assert "secret" not in db.safe_url
    finally:
        await db.dispose()


async def test_session_commits_on_success_and_rolls_back_on_error(tmp_path: Path) -> None:
    db = Database(sqlite_config(tmp_path / "tayyari.sqlite3"))
    try:
        async with db.session() as s:
            await s.execute(text("CREATE TABLE t (x INTEGER)"))
        async with db.session() as s:
            await s.execute(text("INSERT INTO t VALUES (1)"))
        with pytest.raises(RuntimeError):
            async with db.session() as s:
                await s.execute(text("INSERT INTO t VALUES (2)"))
                raise RuntimeError("boom")
        async with db.session() as s:
            assert (await s.execute(text("SELECT x FROM t ORDER BY x"))).scalars().all() == [1]
    finally:
        await db.dispose()


async def test_memory_sessions_do_not_interleave(tmp_path: Path) -> None:
    cfg = normalise_database_url("sqlite+aiosqlite://", data_dir=tmp_path, env="test")
    db = Database(cfg)
    try:
        async with db.session() as s:
            await s.execute(text("CREATE TABLE t (x INTEGER)"))

        async def write(n: int, fail: bool) -> None:
            async with db.session() as s:
                await s.execute(text("INSERT INTO t VALUES (:n)"), {"n": n})
                if fail:
                    raise RuntimeError("rollback me")

        results = await asyncio.gather(
            write(1, False), write(2, True), write(3, False), return_exceptions=True
        )
        assert isinstance(results[1], RuntimeError)
        async with db.session() as s:
            # A failing transaction must not take its neighbours' rows with it.
            assert sorted((await s.execute(text("SELECT x FROM t"))).scalars().all()) == [1, 3]
    finally:
        await db.dispose()


async def test_ping_succeeds_and_reports_an_unreachable_database(tmp_path: Path) -> None:
    db = Database(sqlite_config(tmp_path / "ok.sqlite3"))
    try:
        await db.ping()
    finally:
        await db.dispose()

    unreachable = Database(
        DbConfig(
            url=f"sqlite+aiosqlite:///{(tmp_path / 'nope' / 'x.sqlite3').as_posix()}",
            kind="sqlite",
            ephemeral=False,
        )
    )
    try:
        # The constructor creates the parent folder; remove it again to make the file unopenable.
        (tmp_path / "nope").rmdir()
        (tmp_path / "nope").write_text("a file where the folder should be")
        with pytest.raises(OperationalError):
            await unreachable.ping()
    finally:
        await unreachable.dispose()
