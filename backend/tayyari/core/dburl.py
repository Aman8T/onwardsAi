"""DATABASE_URL to an async SQLAlchemy configuration.

The only module that knows which drivers we use (asyncpg for Postgres, aiosqlite for SQLite), so moving
to psycopg later is a change in this file alone.

Neon (injected by FastAPI Cloud) hands out libpq-style URLs such as
``postgresql://user:pw@ep-x-pooler.eu-central-1.aws.neon.tech/db?sslmode=require&channel_binding=require``.
asyncpg rejects ``sslmode`` and ``channel_binding`` as connect arguments, so they are stripped from the
query and replaced by an SSL context; a ``-pooler`` host (PgBouncer in transaction mode) additionally
gets prepared statements switched off. Passwords never leave this module in logs: use `DbConfig.safe_url`.

With no URL there is a SQLite file. Pragmas (WAL, foreign keys, busy timeout) are not set here: the
storage unit applies them from a SQLAlchemy ``connect`` event listener, for file databases only.
"""

from __future__ import annotations

import ssl
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

SQLITE_FILE_NAME = "tayyari.sqlite3"

_LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})
_POSTGRES_DRIVERS = frozenset(
    {"postgres", "postgresql", "postgresql+psycopg2", "postgresql+psycopg", "postgresql+asyncpg"}
)
_SQLITE_DRIVERS = frozenset({"sqlite", "sqlite+pysqlite", "sqlite+aiosqlite"})

# libpq-only query parameters asyncpg's connect() does not know. They are dropped; the ones with an
# asyncpg equivalent are translated below (application_name, options).
_LIBPQ_ONLY = frozenset(
    {
        "sslmode",
        "channel_binding",
        "sslcert",
        "sslkey",
        "sslrootcert",
        "sslcrl",
        "sslcompression",
        "sslsni",
        "sslnegotiation",
        "gssencmode",
        "krbsrvname",
        "target_session_attrs",
        "connect_timeout",
        "keepalives",
        "keepalives_idle",
        "keepalives_interval",
        "keepalives_count",
        "tcp_user_timeout",
        "fallback_application_name",
        "client_encoding",
        "requirepeer",
        "service",
        "passfile",
    }
)
_PREPARED_CACHE_PARAM = "prepared_statement_cache_size"


@dataclass(frozen=True)
class DbConfig:
    """Everything the storage layer needs to build an async engine."""

    url: str
    """SQLAlchemy URL (``postgresql+asyncpg://`` or ``sqlite+aiosqlite://``), password included."""
    kind: Literal["postgres", "sqlite"]
    ephemeral: bool
    """True when data will not survive a restart (SQLite in prod, or in memory)."""
    connect_args: dict[str, Any] = field(default_factory=dict)
    """Passed to the driver (``create_async_engine(connect_args=...)``)."""
    engine_kwargs: dict[str, Any] = field(default_factory=dict)
    """Passed to ``create_async_engine`` (pool settings for Postgres)."""

    @property
    def safe_url(self) -> str:
        """The URL with the password masked, for logs and diagnostics."""
        try:
            return make_url(self.url).render_as_string(hide_password=True)
        except ArgumentError:
            return f"<{self.kind} database>"

    @property
    def in_memory(self) -> bool:
        """A SQLite ``:memory:`` database (needs a single shared connection, e.g. ``StaticPool``)."""
        if self.kind != "sqlite":
            return False
        return _is_memory(make_url(self.url).database)

    def __repr__(self) -> str:
        return f"DbConfig(url={self.safe_url!r}, kind={self.kind!r}, ephemeral={self.ephemeral})"


def default_sqlite_url(data_dir: Path) -> str:
    """``sqlite+aiosqlite:///<data_dir>/tayyari.sqlite3`` (forward slashes, so it also works on Windows)."""
    return URL.create("sqlite+aiosqlite", database=(data_dir / SQLITE_FILE_NAME).as_posix()).render_as_string(
        hide_password=False
    )


def normalise_database_url(url: str | None, *, data_dir: Path, env: str) -> DbConfig:
    """Turn whatever ``DATABASE_URL`` holds (or nothing) into a `DbConfig`.

    - ``None`` / empty: SQLite under ``data_dir``; ephemeral when ``env == "prod"``.
    - ``sqlite://...``: ``sqlite+aiosqlite://...``; ephemeral in prod or in memory.
    - ``postgres://``, ``postgresql://``, ``postgresql+psycopg2://``, ``postgresql+psycopg://``:
      ``postgresql+asyncpg://`` with libpq-only parameters stripped (see module docstring).

    Raises ``ValueError`` (without echoing the URL) for anything else.
    """
    raw = (url or "").strip()
    if not raw:
        return _sqlite_config(make_url(default_sqlite_url(data_dir)), env, force_file=True)
    try:
        parsed = make_url(raw)
    except ArgumentError:
        raise ValueError("DATABASE_URL could not be parsed") from None
    driver = parsed.drivername
    if driver in _SQLITE_DRIVERS:
        return _sqlite_config(parsed, env)
    if driver in _POSTGRES_DRIVERS:
        return _postgres_config(parsed)
    raise ValueError(
        f"Unsupported DATABASE_URL scheme {driver!r}: expected postgresql://, postgres:// or sqlite://"
    )


# ───────────────────────────── sqlite ─────────────────────────────


def _is_memory(database: str | None) -> bool:
    return not database or database == ":memory:" or "mode=memory" in database


def _sqlite_config(parsed: URL, env: str, *, force_file: bool = False) -> DbConfig:
    target = parsed.set(drivername="sqlite+aiosqlite")
    in_memory = not force_file and _is_memory(target.database)
    return DbConfig(
        url=target.render_as_string(hide_password=False),
        kind="sqlite",
        ephemeral=env == "prod" or in_memory,
        connect_args={"timeout": 15},
        engine_kwargs={},
    )


# ───────────────────────────── postgres ─────────────────────────────


def _first(value: str | tuple[str, ...]) -> str:
    return value if isinstance(value, str) else (value[0] if value else "")


def _parse_libpq_options(options: str) -> dict[str, str]:
    """``-c key=value`` / ``-ckey=value`` / ``--key=value`` pairs as server settings; anything else is ignored."""
    settings: dict[str, str] = {}
    parts = options.split()
    i = 0
    while i < len(parts):
        part = parts[i]
        pair: str | None = None
        if part == "-c" and i + 1 < len(parts):
            pair = parts[i + 1]
            i += 1
        elif (part.startswith("-c") and len(part) > 2) or (part.startswith("--") and "=" in part):
            pair = part[2:]
        if pair and "=" in pair:
            key, _, value = pair.partition("=")
            if key:
                settings[key] = value
        i += 1
    return settings


def _ssl_mode(query: Mapping[str, str | tuple[str, ...]]) -> str | None:
    """The requested SSL mode, from libpq's ``sslmode`` or asyncpg's ``ssl`` query parameter (lower-cased)."""
    for name in ("sslmode", "ssl"):
        if name in query:
            value = _first(query[name]).strip().lower()
            return "disable" if value in {"false", "0", "off", "no"} else (value or None)
    return None


def _postgres_config(parsed: URL) -> DbConfig:
    host = (parsed.host or "").lower()
    query: Mapping[str, str | tuple[str, ...]] = parsed.query
    sslmode = _ssl_mode(query)

    server_settings: dict[str, str] = {"application_name": "tayyari"}
    if "application_name" in query:
        server_settings["application_name"] = _first(query["application_name"]) or "tayyari"
    if "options" in query:
        # `options` carries libpq startup options. `-c k=v` pairs become server settings; anything else
        # (Neon's `options=endpoint=ep-x` routing hint, for example) is dropped: asyncpg sends SNI instead.
        server_settings.update(_parse_libpq_options(_first(query["options"])))

    kept: dict[str, str | tuple[str, ...]] = {
        k: v
        for k, v in query.items()
        if k not in _LIBPQ_ONLY and k not in {"application_name", "options", "ssl", _PREPARED_CACHE_PARAM}
    }

    pooled = "-pooler" in host
    if pooled:
        kept[_PREPARED_CACHE_PARAM] = "0"
    elif _PREPARED_CACHE_PARAM in query:
        kept[_PREPARED_CACHE_PARAM] = _first(query[_PREPARED_CACHE_PARAM])

    connect_args: dict[str, Any] = {
        "timeout": 10,
        "command_timeout": 30,
        "server_settings": server_settings,
    }
    # An explicit sslmode=disable wins; otherwise SSL is on for every non-local host and for any other
    # explicit sslmode (require, verify-ca, prefer ...).
    wants_ssl = sslmode != "disable" and (sslmode is not None or host not in _LOCAL_HOSTS)
    if wants_ssl:
        connect_args["ssl"] = ssl.create_default_context()
    if pooled:
        # PgBouncer in transaction mode hands the same statement name to different server connections.
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_name_func"] = _unique_statement_name

    target = parsed.set(drivername="postgresql+asyncpg", query=kept)
    return DbConfig(
        url=target.render_as_string(hide_password=False),
        kind="postgres",
        ephemeral=False,
        connect_args=connect_args,
        engine_kwargs={
            "pool_size": 5,
            "max_overflow": 5,
            "pool_pre_ping": True,
            "pool_recycle": 240,
            "pool_timeout": 10,
        },
    )


def _unique_statement_name() -> str:
    return f"__asyncpg_{uuid.uuid4().hex}__"


__all__ = ["SQLITE_FILE_NAME", "DbConfig", "default_sqlite_url", "normalise_database_url"]
