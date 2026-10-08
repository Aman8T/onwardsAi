"""Migrations: upgrade to head, the fast path, connection retry, and schema parity with the models."""

from __future__ import annotations

import ast
import asyncio
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from tayyari.core.dburl import DbConfig, normalise_database_url
from tayyari.storage import migrate
from tayyari.storage.db import Database
from tayyari.storage.migrate import MIGRATIONS_DIR, head_revision, migrate_to_head, migrate_to_head_sync
from tayyari.storage.tables import metadata

EXPECTED_TABLES = {
    "profiles",
    "profile_contexts",
    "chat_sessions",
    "chat_messages",
    "llm_calls",
    "jev_calls",
    "decision_states",
    "decision_log",
    "rate_limit_hits",
    "spend_daily",
    "kv",
    "portfolios",
}


def sqlite_config(path: Path) -> DbConfig:
    return normalise_database_url(f"sqlite:///{path.as_posix()}", data_dir=path.parent, env="test")


async def table_names(db: Database) -> set[str]:
    async with db.engine.connect() as conn:
        return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))


async def revision(db: Database) -> str | None:
    async with db.engine.connect() as conn:
        return await conn.run_sync(migrate._current_revision)


# ───────────────────────────── upgrade ─────────────────────────────


async def test_an_empty_database_is_migrated_to_head(tmp_path: Path) -> None:
    db = Database(sqlite_config(tmp_path / "empty.sqlite3"))
    try:
        assert await table_names(db) == set()
        assert await migrate_to_head(db) == head_revision()
        assert await table_names(db) == EXPECTED_TABLES | {"alembic_version"}
        assert await revision(db) == head_revision()
    finally:
        await db.dispose()


async def test_the_second_call_takes_the_fast_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = Database(sqlite_config(tmp_path / "fast.sqlite3"))
    try:
        await migrate_to_head(db)

        def explode(*_args: Any) -> None:
            raise AssertionError("the upgrade must not run when the database is already at head")

        monkeypatch.setattr(migrate, "_upgrade", explode)
        assert await migrate_to_head(db) == head_revision()
    finally:
        await db.dispose()


async def test_a_replica_that_loses_the_race_finds_nothing_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The head is checked again once the lock is held: a peer may have migrated while this one waited."""
    db = Database(sqlite_config(tmp_path / "race.sqlite3"))
    try:
        answers = iter([None, head_revision()])  # fast-path check: nothing yet; under the lock: peer is done
        monkeypatch.setattr(migrate, "_current_revision", lambda _conn: next(answers))

        def explode(*_args: Any) -> None:
            raise AssertionError("the peer already migrated")

        monkeypatch.setattr(migrate, "_upgrade", explode)
        assert await migrate_to_head(db) == head_revision()
        assert await table_names(db) == set()  # nothing was created by this replica
    finally:
        await db.dispose()


# ───────────────────────────── a database newer than the code (rollback) ─────────────────────────────


class RecordingLog:
    """Stands in for the module's structlog logger."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def _add(self, level: str, event: str, **fields: Any) -> None:
        self.events.append((level, event, fields))

    def info(self, event: str, **fields: Any) -> None:
        self._add("info", event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._add("warning", event, **fields)

    def named(self, event: str) -> list[tuple[str, dict[str, Any]]]:
        return [(level, fields) for level, name, fields in self.events if name == event]


@pytest.fixture
def recorded_log(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    stub = RecordingLog()
    monkeypatch.setattr(migrate, "log", stub)
    return stub


async def stamp(db: Database, version: str) -> None:
    async with db.engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": version})


async def test_a_database_at_a_newer_revision_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded_log: RecordingLog
) -> None:
    """#16: old code after a rollback must start on the newer schema, not crash-loop on it."""
    db = Database(sqlite_config(tmp_path / "newer.sqlite3"))
    try:
        await migrate_to_head(db)
        await stamp(db, "0002_future")
        tables_before = await table_names(db)
        recorded_log.events.clear()  # only what the restarts of the older code log

        def explode(*_args: Any) -> None:
            raise AssertionError("nothing may be upgraded or downgraded")

        monkeypatch.setattr(migrate, "_upgrade", explode)

        assert await migrate_to_head(db) == "0002_future"  # the database's own revision
        assert await migrate_to_head(db) == "0002_future"  # and again on the next start
        assert await revision(db) == "0002_future"  # untouched
        assert await table_names(db) == tables_before

        [(level, warning), _second] = recorded_log.named("migrations_schema_newer_than_code")
        assert level == "warning"
        assert warning["db_revision"] == "0002_future" and warning["code_head"] == head_revision()
        assert recorded_log.named("migrations_start") == [] and recorded_log.named("migrations_done") == []
    finally:
        await db.dispose()


async def test_the_repositories_still_work_on_a_newer_schema(tmp_path: Path) -> None:
    from tayyari.storage.repos import build_repositories

    db = Database(sqlite_config(tmp_path / "newer-use.sqlite3"))
    try:
        await migrate_to_head(db)
        await stamp(db, "0002_future")
        await migrate_to_head(db)
        repos = build_repositories(db)
        await repos.kv.set("probe", "ok")
        assert await repos.kv.get("probe") == "ok"
    finally:
        await db.dispose()


async def test_a_newer_revision_that_appears_while_waiting_for_the_lock_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded_log: RecordingLog
) -> None:
    """A peer running the newer release may finish between the fast-path check and the lock."""
    db = Database(sqlite_config(tmp_path / "race-newer.sqlite3"))
    try:
        answers = iter([None, "0002_future"])
        monkeypatch.setattr(migrate, "_current_revision", lambda _conn: next(answers))

        def explode(*_args: Any) -> None:
            raise AssertionError("the newer release already migrated")

        monkeypatch.setattr(migrate, "_upgrade", explode)
        assert await migrate_to_head(db) == "0002_future"
        assert await table_names(db) == set()  # nothing was created by this replica
        assert len(recorded_log.named("migrations_schema_newer_than_code")) == 1
    finally:
        await db.dispose()


def test_only_a_revision_the_code_has_never_heard_of_counts_as_newer() -> None:
    assert migrate._is_newer_than_code("0002_future") is True
    assert migrate._is_newer_than_code("deadbeef") is True
    assert migrate._is_newer_than_code(head_revision()) is False
    assert migrate._is_newer_than_code(None) is False  # never migrated: it needs the upgrade
    assert migrate._is_newer_than_code("") is False  # a broken stamp is not "newer"
    assert head_revision() in migrate._known_revisions()


async def test_an_unmigrated_database_is_still_upgraded(tmp_path: Path, recorded_log: RecordingLog) -> None:
    db = Database(sqlite_config(tmp_path / "fresh.sqlite3"))
    try:
        assert await migrate_to_head(db) == head_revision()
        assert recorded_log.named("migrations_schema_newer_than_code") == []
        assert len(recorded_log.named("migrations_done")) == 1
    finally:
        await db.dispose()


async def test_a_broken_alembic_state_still_raises(tmp_path: Path) -> None:
    """Only "newer than this code" is forgiven: a half-migrated or unreadable database must stop the start."""
    # The tables exist but the version row is gone: the upgrade tries to create them again.
    emptied = Database(sqlite_config(tmp_path / "emptied.sqlite3"))
    try:
        await migrate_to_head(emptied)
        async with emptied.engine.begin() as conn:
            await conn.execute(text("DELETE FROM alembic_version"))
        with pytest.raises(OperationalError, match="already exists"):
            await migrate_to_head(emptied)
    finally:
        await emptied.dispose()

    # A version table that is not Alembic's.
    wrong = Database(sqlite_config(tmp_path / "wrong.sqlite3"))
    try:
        async with wrong.engine.begin() as conn:
            await conn.execute(text("CREATE TABLE alembic_version (other TEXT)"))
        with pytest.raises(OperationalError, match="version_num"):
            await migrate_to_head(wrong)
    finally:
        await wrong.dispose()

    # An empty stamp is a broken state, not a newer schema.
    blank = Database(sqlite_config(tmp_path / "blank.sqlite3"))
    try:
        await migrate_to_head(blank)
        await stamp(blank, "")
        with pytest.raises(Exception):  # noqa: B017 - alembic's own error for an unknown revision
            await migrate_to_head(blank)
    finally:
        await blank.dispose()


async def test_missing_migrations_still_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "no-migrations"
    (empty / "versions").mkdir(parents=True)
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", empty)
    db = Database(sqlite_config(tmp_path / "nomig.sqlite3"))
    try:
        with pytest.raises(RuntimeError, match="no migrations found"):
            await migrate_to_head(db)
    finally:
        await db.dispose()


async def test_an_in_memory_database_can_be_migrated(tmp_path: Path) -> None:
    db = Database(normalise_database_url("sqlite+aiosqlite://", data_dir=tmp_path, env="test"))
    try:
        assert await migrate_to_head(db) == head_revision()
        assert await table_names(db) == EXPECTED_TABLES | {"alembic_version"}
    finally:
        await db.dispose()


def test_the_sync_wrapper_migrates_and_disposes(tmp_path: Path) -> None:
    cfg = sqlite_config(tmp_path / "sync.sqlite3")
    assert migrate_to_head_sync(cfg) == head_revision()
    assert migrate_to_head_sync(cfg) == head_revision()  # idempotent


def test_revisions_form_one_linear_chain() -> None:
    revisions: dict[str, str | None] = {}
    for path in (MIGRATIONS_DIR / "versions").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assigned = {
            node.target.id: ast.literal_eval(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        }
        revisions[assigned["revision"]] = assigned["down_revision"]
    roots = [r for r, parent in revisions.items() if parent is None]
    heads = set(revisions) - {p for p in revisions.values() if p is not None}
    assert len(roots) == 1 and heads == {head_revision()}
    assert all(parent in revisions for parent in revisions.values() if parent is not None)


# ───────────────────────────── schema parity ─────────────────────────────


def autogenerate_diff(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return list(compare_metadata(context, metadata))


async def test_migrations_and_models_agree(any_real_db: Database) -> None:
    """Autogenerate against the migrated database finds nothing to add, drop or alter."""
    async with any_real_db.engine.connect() as conn:
        assert await conn.run_sync(autogenerate_diff) == []


async def test_every_model_table_is_created_by_the_migration(any_real_db: Database) -> None:
    assert set(metadata.tables) == EXPECTED_TABLES
    assert await table_names(any_real_db) == EXPECTED_TABLES | {"alembic_version"}


async def test_schema_diff_catches_a_missing_column(any_real_db: Database) -> None:
    """The parity check is only worth something if it can fail."""
    async with any_real_db.engine.begin() as conn:
        await conn.execute(text("ALTER TABLE kv ADD COLUMN stray TEXT"))
    async with any_real_db.engine.connect() as conn:
        diff = await conn.run_sync(autogenerate_diff)
    assert any(item[0] == "remove_column" and item[3].name == "stray" for item in diff)


# ───────────────────────────── connection retry ─────────────────────────────


class FakeTime:
    """A monotonic clock and a sleep that advances it, for the retry loop."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def failing_ping(times: int, error: Callable[[], BaseException]) -> tuple[Callable[[], Any], SimpleNamespace]:
    counter = SimpleNamespace(calls=0)

    async def ping() -> None:
        counter.calls += 1
        if counter.calls <= times:
            raise error()

    return ping, counter


def operational_error(sqlstate: str | None = None) -> OperationalError:
    return OperationalError("SELECT 1", {}, SimpleNamespace(sqlstate=sqlstate))  # type: ignore[arg-type]


async def test_the_first_connection_is_retried_with_growing_backoff() -> None:
    clock = FakeTime()
    ping, counter = failing_ping(4, lambda: ConnectionRefusedError("waking up"))
    attempts = await migrate._connect_with_retry(ping, 45.0, sleep=clock.sleep, monotonic=clock.monotonic)
    assert attempts == 5 and counter.calls == 5
    assert len(clock.slept) == 4
    assert clock.slept == sorted(clock.slept)  # backing off
    assert all(0.3 < wait <= 6.0 for wait in clock.slept)


async def test_retry_gives_up_after_the_timeout_with_the_original_error() -> None:
    clock = FakeTime()
    ping, counter = failing_ping(10_000, lambda: TimeoutError("still asleep"))
    with pytest.raises(TimeoutError, match="still asleep"):
        await migrate._connect_with_retry(ping, 20.0, sleep=clock.sleep, monotonic=clock.monotonic)
    assert 4 <= counter.calls < 100
    assert sum(clock.slept) <= 20.0 + 1e-6  # never sleeps past the deadline


async def test_a_zero_timeout_means_a_single_attempt() -> None:
    clock = FakeTime()
    ping, counter = failing_ping(10, lambda: operational_error())
    with pytest.raises(OperationalError):
        await migrate._connect_with_retry(ping, 0.0, sleep=clock.sleep, monotonic=clock.monotonic)
    assert counter.calls == 1 and clock.slept == []


@pytest.mark.parametrize("sqlstate", ["28P01", "28000", "3D000"])
async def test_a_wrong_password_or_missing_database_is_not_retried(sqlstate: str) -> None:
    clock = FakeTime()
    ping, counter = failing_ping(10, lambda: operational_error(sqlstate))
    with pytest.raises(OperationalError):
        await migrate._connect_with_retry(ping, 45.0, sleep=clock.sleep, monotonic=clock.monotonic)
    assert counter.calls == 1


async def test_other_errors_are_not_swallowed() -> None:
    clock = FakeTime()
    ping, counter = failing_ping(1, lambda: ValueError("a bug, not an outage"))
    with pytest.raises(ValueError):
        await migrate._connect_with_retry(ping, 45.0, sleep=clock.sleep, monotonic=clock.monotonic)
    assert counter.calls == 1


async def test_sqlite_does_not_wait_for_a_connection(tmp_path: Path) -> None:
    unopenable = tmp_path / "x.sqlite3"
    unopenable.mkdir()  # a folder where the database file should be
    db = Database(sqlite_config(unopenable))
    try:
        # A Postgres database would be retried for the whole timeout; a local file fails at once.
        with pytest.raises(OperationalError):
            await asyncio.wait_for(migrate_to_head(db, connect_timeout_s=60.0), timeout=10.0)
    finally:
        await db.dispose()


def test_head_revision_and_script_location_exist() -> None:
    assert re.fullmatch(r"\d{4}", head_revision())
    assert (MIGRATIONS_DIR / "env.py").is_file()
    assert (MIGRATIONS_DIR / "script.py.mako").is_file()


# ───────────────────────────── revision template and downgrade ─────────────────────────────


def test_the_revision_template_renders_a_valid_additive_migration(tmp_path: Path) -> None:
    """``script.py.mako`` is what the next person's ``alembic revision`` uses: it must produce valid Python."""
    scripts = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    config = migrate.alembic_config()
    config.set_main_option("script_location", str(scripts))
    command.revision(config, message="add a column", rev_id="0003")

    (generated,) = scripts.glob("versions/0003_*.py")
    tree = ast.parse(generated.read_text(encoding="utf-8"))
    assigned = {
        n.target.id: ast.literal_eval(n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None
    }
    assert assigned["revision"] == "0003"
    assert assigned["down_revision"] == "0002"
    assert {n.name for n in tree.body if isinstance(n, ast.FunctionDef)} == {"upgrade", "downgrade"}


async def test_the_initial_migration_can_be_undone(tmp_path: Path) -> None:
    def downgrade(connection: Connection) -> None:
        config = migrate.alembic_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "base")

    db = Database(sqlite_config(tmp_path / "down.sqlite3"))
    try:
        await migrate_to_head(db)
        async with db.engine.begin() as conn:
            await conn.run_sync(downgrade)
        assert await table_names(db) <= {
            "alembic_version"
        }  # every table is gone (the version table is empty)
        assert await migrate_to_head(db) == head_revision()  # and it can be rebuilt
        assert await table_names(db) == EXPECTED_TABLES | {"alembic_version"}
    finally:
        await db.dispose()
