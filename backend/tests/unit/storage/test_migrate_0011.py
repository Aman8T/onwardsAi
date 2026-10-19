"""Migration 0011 (Sign in with the Leafout account): new nullable columns, a unique index that tolerates NULLs, and
``users.email`` relaxed to NULL without a table rebuild (a rebuild would delete everything an account owns on SQLite)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tayyari.storage import migrate
from tayyari.storage.db import Database
from tayyari.storage.migrate import MIGRATIONS_DIR, head_revision, migrate_to_head
from tests.unit.storage.test_migrate import revision, run_sql, sqlite_config

OLD_ACCOUNT = (
    "INSERT INTO users (id, email, created_at, last_login_at) VALUES ('u1', 'ana@example.com', :ts, :ts)"
)
OLD_PROFILE = (
    "INSERT INTO profiles (id, created_at, last_accessed_at, is_sample, schema_version, profile, user_id) "
    "VALUES ('p1', :ts, :ts, 0, 1, '{}', 'u1')"
)
OLD_SESSION = (
    "INSERT INTO auth_sessions (id, user_id, created_at, expires_at, last_seen_at) "
    "VALUES ('s1', 'u1', :ts, :ts, :ts)"
)


async def columns(conn: Any, table: str) -> dict[str, Any]:
    return {r.name: r for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).all()}


async def test_0011_adds_the_columns_relaxes_the_email_and_keeps_every_row_and_what_hangs_on_it(
    tmp_path: Path,
) -> None:
    db = Database(sqlite_config(tmp_path / "keep.sqlite3"))
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(migrate._upgrade, "0010")
            await run_sql(conn, OLD_ACCOUNT, OLD_PROFILE, OLD_SESSION)
            assert (await columns(conn, "users"))["email"].notnull == 1
            assert "supabase_sub" not in await columns(conn, "users")
        async with db.engine.begin() as conn:
            await conn.run_sync(migrate._upgrade, "0011")

        async with db.engine.connect() as conn:
            users, sessions = await columns(conn, "users"), await columns(conn, "auth_sessions")
            assert users["email"].notnull == 0 and users["supabase_sub"].notnull == 0
            assert {"source", "supabase_session_id", "verified_at"} <= set(sessions)
            assert all(sessions[c].notnull == 0 for c in ("source", "supabase_session_id", "verified_at"))
            # nothing was rebuilt: the account, its profile and its session are all still there
            assert (await conn.execute(text("SELECT id, email, supabase_sub FROM users"))).all() == [
                ("u1", "ana@example.com", None)
            ]
            assert (await conn.execute(text("SELECT id FROM profiles"))).scalars().all() == ["p1"]
            assert (await conn.execute(text("SELECT id, source, verified_at FROM auth_sessions"))).all() == [
                ("s1", None, None)
            ]
            assert (await conn.execute(text("PRAGMA foreign_key_check"))).all() == []
            assert (await conn.execute(text("PRAGMA integrity_check"))).scalar_one() == "ok"
            keys = (await conn.execute(text("PRAGMA foreign_key_list(auth_sessions)"))).all()
            assert ("users", "user_id", "CASCADE") in {
                (k.table, k._mapping["from"], k.on_delete) for k in keys
            }
        assert await revision(db) == "0011"
    finally:
        await db.dispose()


async def test_after_0011_an_account_may_have_no_email_and_a_leafout_id_is_unique(tmp_path: Path) -> None:
    db = Database(sqlite_config(tmp_path / "rules.sqlite3"))
    try:
        assert await migrate_to_head(db) == head_revision()
        async with db.engine.begin() as conn:
            await run_sql(
                conn,
                "INSERT INTO users (id, email, supabase_sub, created_at, last_login_at) "
                "VALUES ('a', NULL, 'sub-a', :ts, :ts)",
                "INSERT INTO users (id, email, supabase_sub, created_at, last_login_at) "
                "VALUES ('b', NULL, NULL, :ts, :ts)",
                "INSERT INTO users (id, email, supabase_sub, created_at, last_login_at) "
                "VALUES ('c', NULL, NULL, :ts, :ts)",  # NULLs never collide, in email or in supabase_sub
            )
        with pytest.raises(IntegrityError):
            async with db.engine.begin() as conn:
                await run_sql(
                    conn,
                    "INSERT INTO users (id, email, supabase_sub, created_at, last_login_at) "
                    "VALUES ('d', 'd@example.com', 'sub-a', :ts, :ts)",
                )
        with pytest.raises(IntegrityError):  # the email stays unique when there is one
            async with db.engine.begin() as conn:
                await run_sql(
                    conn,
                    "INSERT INTO users (id, email, created_at, last_login_at) VALUES ('e', 'x@example.com', :ts, :ts)",
                    "INSERT INTO users (id, email, created_at, last_login_at) VALUES ('f', 'x@example.com', :ts, :ts)",
                )
    finally:
        await db.dispose()


async def test_0011_can_be_undone_on_a_development_database_and_goes_forward_again(tmp_path: Path) -> None:
    from alembic import command
    from sqlalchemy.engine import Connection

    def downgrade(connection: Connection) -> None:
        config = migrate.alembic_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "0010")

    db = Database(sqlite_config(tmp_path / "undo.sqlite3"))
    try:
        await migrate_to_head(db)
        async with db.engine.begin() as conn:
            await run_sql(conn, OLD_ACCOUNT, OLD_SESSION)
        async with db.engine.begin() as conn:
            await conn.run_sync(downgrade)
        async with db.engine.connect() as conn:
            assert "supabase_sub" not in await columns(conn, "users")
            assert "verified_at" not in await columns(conn, "auth_sessions")
            assert (await conn.execute(text("SELECT id FROM users"))).scalars().all() == ["u1"]
        assert await revision(db) == "0010"
        assert await migrate_to_head(db) == head_revision()
    finally:
        await db.dispose()


def test_0011_is_additive_apart_from_the_approved_relaxation() -> None:
    source = (MIGRATIONS_DIR / "versions" / "0011_leafout_identity.py").read_text(encoding="utf-8")
    assert "# contract-phase" not in source
    assert "DROP NOT NULL" in source and "relaxation" in source.lower()
