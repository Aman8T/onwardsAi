"""The async engine and session factory, built from `core.dburl.DbConfig`.

`Database` is the only object that owns a connection pool. Repositories take one and open short
transactions through `Database.session()`; nothing else in the package touches the engine.

SQLite notes (local development, tests and the ephemeral prod fallback):

- Every connection gets ``foreign_keys=ON`` and a 5 s ``busy_timeout``. File databases also get WAL and
  ``synchronous=NORMAL`` so readers and a writer do not block each other.
- An in-memory database lives in exactly one connection (``StaticPool``), so sessions are serialised with
  a lock: two interleaved transactions on one connection would commit or roll back each other's work.
- Writers should start a transaction with their write, not with a read (a read-then-write upgrade can fail
  at once with "database is locked" when another connection committed in between). The repositories are
  written that way.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy import event, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import CursorResult, Result, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tayyari.core.dburl import DbConfig
from tayyari.core.logging import get_logger

log = get_logger(__name__)

Dialect = Literal["postgresql", "sqlite"]


def _is_file_uri(database: str) -> bool:
    return database.startswith("file:")


class Database:
    """An async SQLAlchemy engine plus a transactional session helper."""

    def __init__(self, cfg: DbConfig) -> None:
        self.config = cfg
        kwargs: dict[str, Any] = dict(cfg.engine_kwargs)
        if cfg.kind == "sqlite":
            if cfg.in_memory:
                kwargs["poolclass"] = StaticPool
            else:
                self._ensure_parent_dir(cfg)
        self.engine: AsyncEngine = create_async_engine(cfg.url, connect_args=dict(cfg.connect_args), **kwargs)
        if cfg.kind == "sqlite":
            self._install_sqlite_pragmas(file_db=not cfg.in_memory)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self._memory_lock: asyncio.Lock | None = asyncio.Lock() if cfg.in_memory else None

    # ───────────────────────────── properties ─────────────────────────────

    @property
    def dialect(self) -> Dialect:
        """``"postgresql"`` or ``"sqlite"`` (what dialect-specific SQL branches on)."""
        return "postgresql" if self.config.kind == "postgres" else "sqlite"

    @property
    def kind(self) -> Literal["postgres", "sqlite"]:
        return self.config.kind

    @property
    def ephemeral(self) -> bool:
        """True when data will not survive a restart (in-memory SQLite, or SQLite in prod)."""
        return self.config.ephemeral

    @property
    def safe_url(self) -> str:
        """The URL with the password masked, for logs."""
        return self.config.safe_url

    # ───────────────────────────── usage ─────────────────────────────

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A session inside one transaction: committed on a clean exit, rolled back on any exception."""
        guard: AbstractAsyncContextManager[Any] = self._memory_lock or nullcontext()
        async with guard, self._sessions() as session, session.begin():
            yield session

    async def ping(self) -> None:
        """``SELECT 1`` (raises when the database cannot be reached): the deep health check."""
        async with self.session() as s:
            await s.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        """Close every pooled connection (call on shutdown)."""
        await self.engine.dispose()

    # ───────────────────────────── setup ─────────────────────────────

    @staticmethod
    def _ensure_parent_dir(cfg: DbConfig) -> None:
        database = make_url(cfg.url).database
        if database and not _is_file_uri(database):
            Path(database).parent.mkdir(parents=True, exist_ok=True)

    def _install_sqlite_pragmas(self, *, file_db: bool) -> None:
        def on_connect(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA busy_timeout=5000")
                if file_db:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

        event.listen(self.engine.sync_engine, "connect", on_connect)


# ───────────────────────────── statement helpers ─────────────────────────────


def dialect_insert(dialect: str, table: Any) -> postgresql.Insert | sqlite.Insert:
    """The dialect's ``INSERT`` construct (both support ``on_conflict_do_update`` / ``..._do_nothing``)."""
    return postgresql.insert(table) if dialect == "postgresql" else sqlite.insert(table)


def rowcount(result: Result[Any]) -> int:
    """Rows matched by an UPDATE or DELETE (SQLAlchemy types ``execute`` as a plain ``Result``)."""
    return max(cast("CursorResult[Any]", result).rowcount, 0)


__all__ = ["Database", "Dialect", "dialect_insert", "rowcount"]
