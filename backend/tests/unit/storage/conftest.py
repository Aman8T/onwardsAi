"""Fixtures for the storage tests: real databases (migrated by Alembic), the fakes, and a manual clock.

Postgres variants run only when ``TEST_DATABASE_URL`` is set. Use a *direct* (non-pooler) URL of a throw-away
database or branch: each test gets its own schema (``search_path`` is set as a startup parameter, which
PgBouncer's transaction pooling does not forward) and the schema is dropped afterwards.
"""

from __future__ import annotations

import os
import uuid
import zlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text

from tayyari.contract.records import DecisionRecord, JevCallRecord, LlmCallRecord, StateBlob
from tayyari.core.dburl import DbConfig, normalise_database_url
from tayyari.core.hashing import canonical_json, sha256_hex
from tayyari.storage.db import Database
from tayyari.storage.migrate import migrate_to_head
from tayyari.storage.repos import Repositories, build_repositories
from tests.fakes.repos import FakeRepositories

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


class ManualClock:
    """A `core.clock.Clock` whose time only moves when the test says so."""

    def __init__(self, start: datetime = T0) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._now.timestamp()

    async def sleep(self, seconds: float) -> None:
        self.advance(timedelta(seconds=seconds))

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, moment: datetime) -> None:
        self._now = moment


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


def sqlite_config(path: Path) -> DbConfig:
    return normalise_database_url(f"sqlite:///{path.as_posix()}", data_dir=path.parent, env="test")


@dataclass
class Env:
    """What a shared behaviour test receives: the repositories and which backend they sit on."""

    kind: str
    repos: Repositories
    clock: ManualClock
    db: Database | None = None
    fakes: FakeRepositories | None = None


@asynccontextmanager
async def open_file_db(tmp_path: Path) -> AsyncIterator[Database]:
    """A migrated SQLite file database."""
    db = Database(sqlite_config(tmp_path / "db" / "tayyari.sqlite3"))
    try:
        await migrate_to_head(db)
        yield db
    finally:
        await db.dispose()


@asynccontextmanager
async def open_memory_db(tmp_path: Path) -> AsyncIterator[Database]:
    """A migrated in-memory SQLite database (one shared connection)."""
    db = Database(normalise_database_url("sqlite+aiosqlite://", data_dir=tmp_path, env="test"))
    try:
        await migrate_to_head(db)
        yield db
    finally:
        await db.dispose()


@asynccontextmanager
async def open_pg_db() -> AsyncIterator[Database]:
    """A migrated Postgres schema private to the test (skips without ``TEST_DATABASE_URL``)."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    base = normalise_database_url(TEST_DATABASE_URL, data_dir=Path.cwd(), env="test")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    admin = Database(base)
    try:
        async with admin.engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        args = dict(base.connect_args)
        args["server_settings"] = {**args.get("server_settings", {}), "search_path": schema}
        db = Database(replace(base, connect_args=args))
        try:
            await migrate_to_head(db)
            yield db
        finally:
            await db.dispose()
            async with admin.engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        await admin.dispose()


def open_db(kind: str, tmp_path: Path) -> AbstractAsyncContextManager[Database]:
    if kind == "sqlite_file":
        return open_file_db(tmp_path)
    if kind == "sqlite_memory":
        return open_memory_db(tmp_path)
    if kind == "postgres":
        return open_pg_db()
    raise ValueError(kind)


@pytest_asyncio.fixture
async def file_db(tmp_path: Path) -> AsyncIterator[Database]:
    async with open_file_db(tmp_path) as db:
        yield db


@pytest_asyncio.fixture
async def memory_db(tmp_path: Path) -> AsyncIterator[Database]:
    async with open_memory_db(tmp_path) as db:
        yield db


@pytest_asyncio.fixture
async def pg_db() -> AsyncIterator[Database]:
    async with open_pg_db() as db:
        yield db


@pytest.fixture(params=["sqlite_file", "postgres"])
def real_db_kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest_asyncio.fixture
async def any_real_db(real_db_kind: str, tmp_path: Path) -> AsyncIterator[Database]:
    """The same test on the SQLite file and (when configured) on Postgres."""
    async with open_db(real_db_kind, tmp_path) as db:
        yield db


@pytest.fixture(params=["fake", "sqlite_file", "sqlite_memory", "postgres"])
def backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest_asyncio.fixture
async def env(backend: str, clock: ManualClock, tmp_path: Path) -> AsyncIterator[Env]:
    """Repositories on every backend: the shared behaviour tests run unchanged against each."""
    if backend == "fake":
        fakes = FakeRepositories(clock)
        yield Env(kind=backend, repos=fakes.bundle, clock=clock, fakes=fakes)
        return
    async with open_db(backend, tmp_path) as db:
        yield Env(kind=backend, repos=build_repositories(db, clock), clock=clock, db=db)


# ───────────────────────────── record builders ─────────────────────────────


@pytest.fixture
def make_llm_call() -> Callable[..., LlmCallRecord]:
    def build(**overrides: Any) -> LlmCallRecord:
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "created_at": T0,
            "purpose": "chat",
            "model": "claude-sonnet-5-5",
            "status": "ok",
            "input_tokens": 1000,
            "output_tokens": 200,
            "cost_micro_usd": 4_000,
        }
        values.update(overrides)
        return LlmCallRecord(**values)

    return build


@pytest.fixture
def make_jev_call() -> Callable[..., JevCallRecord]:
    def build(**overrides: Any) -> JevCallRecord:
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "created_at": T0,
            "pack": "first_read",
            "model_requested": "jev-1.13.0",
            "state_hash": "a" * 64,
            "questions": {"career_family": {"type": "choice"}},
            "status": "ok",
            "model_served": "jev-1.13.0",
            "response": {"answers": {"career_family": {"choice": "design_ux"}}},
            "input_tokens": 3000,
            "cost_micro_usd": 126,
            "latency_ms": 900,
        }
        values.update(overrides)
        return JevCallRecord(**values)

    return build


@pytest.fixture
def make_decision() -> Callable[..., DecisionRecord]:
    def build(**overrides: Any) -> DecisionRecord:
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "created_at": T0,
            "spec_id": "first_read.career_family",
            "spec_version": "1",
            "pack": "first_read",
            "mode": "primary",
            "used": True,
            "source_used": "jev",
        }
        values.update(overrides)
        return DecisionRecord(**values)

    return build


@pytest.fixture
def make_state() -> Callable[..., StateBlob]:
    def build(state: Any = None, *, is_sample: bool = False) -> StateBlob:
        doc = {"resume": "Designed a checkout flow."} if state is None else state
        raw = canonical_json(doc)
        return StateBlob(
            hash=sha256_hex(raw), payload=zlib.compress(raw), raw_bytes=len(raw), is_sample=is_sample
        )

    return build
