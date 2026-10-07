"""Alembic migrations, run from code at startup (or by the CLI).

There is no ``alembic.ini``: `alembic_config` builds the configuration in code and `migrations/env.py` uses
the connection handed to it through ``config.attributes["connection"]``.

`migrate_to_head` is built for FastAPI Cloud with a Neon database behind PgBouncer, several replicas
starting at once, and zero-downtime rollouts:

1. The first connection is retried with backoff (a suspended Neon compute needs a few seconds to wake).
2. Fast path: one cheap query compares ``alembic_version`` with the script head. A warm start stops here.
3. Otherwise the upgrade runs in one transaction that first takes a *transaction-scoped* advisory lock
   (``pg_advisory_xact_lock``: unlike a session lock it works through PgBouncer's transaction pooling and
   is released by the commit), with ``lock_timeout`` so a stuck peer cannot hold startup forever. The head
   is checked again under the lock, so the replicas that lost the race find nothing to do.
4. Postgres DDL is transactional: a failing migration rolls back whole, startup fails, and the platform
   keeps the previous version serving. Migrations are therefore additive only (see the lint test).
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from tayyari.core.dburl import DbConfig
from tayyari.core.logging import get_logger

from .db import Database

if TYPE_CHECKING:
    from alembic.config import Config

log = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
ADVISORY_LOCK_KEY = 0x5441595941524931
"""``"TAYYARI1"`` as a positive bigint: the Postgres advisory lock that serialises migrations."""
LOCK_TIMEOUT = "10s"

_BACKOFF_START_S = 0.5
_BACKOFF_MAX_S = 5.0
_PERMANENT_SQLSTATES = frozenset(
    {
        "28P01",  # invalid_password
        "28000",  # invalid_authorization_specification
        "3D000",  # invalid_catalog_name: the database does not exist
    }
)


def alembic_config() -> Config:
    """The Alembic configuration for this package's migrations (no ini file)."""
    from alembic.config import Config

    config = Config()
    # configparser interpolates "%", so a literal one in the path must be doubled.
    config.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    return config


def head_revision() -> str:
    """The newest revision in ``migrations/versions``."""
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise RuntimeError("no migrations found")
    return head


def _current_revision(connection: Connection) -> str | None:
    """The revision recorded in ``alembic_version`` (None for a database that was never migrated)."""
    if not inspect(connection).has_table("alembic_version"):
        return None
    value = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return None if value is None else str(value)


def _upgrade(connection: Connection, revision: str) -> None:
    """Run ``alembic upgrade`` on an open connection (``env.py`` finds it in ``config.attributes``)."""
    from alembic import command

    config = alembic_config()
    config.attributes["connection"] = connection
    command.upgrade(config, revision)


def _is_permanent(exc: BaseException) -> bool:
    """True for failures that waiting cannot fix (wrong password, no such database)."""
    orig = getattr(exc, "orig", None)
    state = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return state in _PERMANENT_SQLSTATES


async def _connect_with_retry(
    ping: Callable[[], Awaitable[None]],
    timeout_s: float,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    """Call ``ping`` until it succeeds, backing off, for up to ``timeout_s``; returns the attempts used."""
    deadline = monotonic() + timeout_s
    delay = _BACKOFF_START_S
    attempt = 0
    while True:
        attempt += 1
        try:
            await ping()
            return attempt
        except (DBAPIError, OSError, TimeoutError) as exc:
            remaining = deadline - monotonic()
            if _is_permanent(exc) or remaining <= 0:
                raise
            wait = min(delay * random.uniform(0.8, 1.2), remaining)
            log.warning(
                "db_connect_retry", attempt=attempt, retry_in_s=round(wait, 2), error=type(exc).__name__
            )
            await sleep(wait)
            delay = min(delay * 2, _BACKOFF_MAX_S)


async def migrate_to_head(db: Database, *, connect_timeout_s: float = 45.0) -> str:
    """Bring the database schema to the newest revision and return it. Safe to call from every replica."""
    head = head_revision()
    # A local SQLite file answers at once or never: only a remote database is worth waiting for.
    wait_s = connect_timeout_s if db.kind == "postgres" else 0.0
    attempts = await _connect_with_retry(db.ping, wait_s)

    async with db.engine.connect() as conn:
        current = await conn.run_sync(_current_revision)
    if current == head:
        log.info("migrations_current", revision=head, connect_attempts=attempts)
        return head

    log.info("migrations_start", current=current, head=head, dialect=db.dialect)
    async with db.engine.begin() as conn:
        if db.dialect == "postgresql":
            await conn.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
            await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADVISORY_LOCK_KEY})
        current = await conn.run_sync(_current_revision)  # another replica may have finished while we waited
        if current == head:
            log.info("migrations_applied_by_peer", revision=head)
            return head
        await conn.run_sync(_upgrade, head)
    log.info("migrations_done", previous=current, revision=head)
    return head


def migrate_to_head_sync(cfg: DbConfig, *, connect_timeout_s: float = 45.0) -> str:
    """`migrate_to_head` for the CLI: builds a database from ``cfg``, migrates, and disposes it."""

    async def run() -> str:
        db = Database(cfg)
        try:
            return await migrate_to_head(db, connect_timeout_s=connect_timeout_s)
        finally:
            await db.dispose()

    return asyncio.run(run())


__all__ = [
    "ADVISORY_LOCK_KEY",
    "MIGRATIONS_DIR",
    "alembic_config",
    "head_revision",
    "migrate_to_head",
    "migrate_to_head_sync",
]
