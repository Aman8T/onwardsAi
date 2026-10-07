from __future__ import annotations

import dataclasses
import ssl
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tayyari.core.dburl import DbConfig, default_sqlite_url, normalise_database_url

NEON = "ep-cool-darkness-123456.eu-central-1.aws.neon.tech"
NEON_POOLED = "ep-cool-darkness-123456-pooler.eu-central-1.aws.neon.tech"


def norm(url: str | None, tmp_path: Path, env: str = "local") -> DbConfig:
    return normalise_database_url(url, data_dir=tmp_path, env=env)


# ───────────────────────────── sqlite ─────────────────────────────


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_no_url_falls_back_to_a_sqlite_file_under_data_dir(tmp_path: Path, empty: str | None) -> None:
    cfg = norm(empty, tmp_path)
    assert cfg.kind == "sqlite"
    assert cfg.url.startswith("sqlite+aiosqlite:///")
    assert make_url(cfg.url).database == (tmp_path / "tayyari.sqlite3").as_posix()
    assert cfg.ephemeral is False
    assert cfg.connect_args == {"timeout": 15}
    assert cfg.engine_kwargs == {"hide_parameters": True}
    assert not cfg.in_memory


@pytest.mark.parametrize("empty", [None, ""])
def test_sqlite_fallback_is_ephemeral_in_prod(tmp_path: Path, empty: str | None) -> None:
    assert norm(empty, tmp_path, env="prod").ephemeral is True
    assert norm(empty, tmp_path, env="test").ephemeral is False


def test_default_sqlite_url_helper(tmp_path: Path) -> None:
    assert make_url(default_sqlite_url(tmp_path)).database == (tmp_path / "tayyari.sqlite3").as_posix()


@pytest.mark.parametrize(
    ("given", "expected_database"),
    [
        ("sqlite:///relative/app.db", "relative/app.db"),
        ("sqlite:////var/lib/app.db", "/var/lib/app.db"),
        ("sqlite+aiosqlite:///x.db", "x.db"),
        ("sqlite+pysqlite:///x.db", "x.db"),
    ],
)
def test_sqlite_urls_get_the_async_driver(tmp_path: Path, given: str, expected_database: str) -> None:
    cfg = norm(given, tmp_path)
    assert cfg.kind == "sqlite"
    parsed = make_url(cfg.url)
    assert parsed.drivername == "sqlite+aiosqlite"
    assert parsed.database == expected_database
    assert cfg.ephemeral is False
    assert norm(given, tmp_path, env="prod").ephemeral is True


@pytest.mark.parametrize("given", ["sqlite://", "sqlite:///:memory:", "sqlite+aiosqlite://"])
def test_in_memory_sqlite_is_ephemeral_and_flagged(tmp_path: Path, given: str) -> None:
    cfg = norm(given, tmp_path)
    assert cfg.in_memory
    assert cfg.ephemeral


# ───────────────────────────── postgres ─────────────────────────────


@pytest.mark.parametrize(
    "scheme",
    ["postgres", "postgresql", "postgresql+psycopg2", "postgresql+psycopg", "postgresql+asyncpg"],
)
def test_every_postgres_scheme_becomes_asyncpg(tmp_path: Path, scheme: str) -> None:
    cfg = norm(f"{scheme}://user:pw@{NEON}:5432/neondb", tmp_path)
    parsed = make_url(cfg.url)
    assert cfg.kind == "postgres"
    assert cfg.ephemeral is False
    assert parsed.drivername == "postgresql+asyncpg"
    assert (parsed.username, parsed.password) == ("user", "pw")
    assert (parsed.host, parsed.port, parsed.database) == (NEON, 5432, "neondb")


def test_neon_url_has_libpq_params_stripped_and_ssl_set(tmp_path: Path) -> None:
    cfg = norm(f"postgresql://user:pw@{NEON}/neondb?sslmode=require&channel_binding=require", tmp_path)
    parsed = make_url(cfg.url)
    assert "sslmode" not in parsed.query
    assert "channel_binding" not in parsed.query
    assert dict(parsed.query) == {}
    assert isinstance(cfg.connect_args["ssl"], ssl.SSLContext)
    assert cfg.connect_args["ssl"].verify_mode == ssl.CERT_REQUIRED


def test_connect_args_and_pool_settings(tmp_path: Path) -> None:
    cfg = norm(f"postgresql://user:pw@{NEON}/neondb?sslmode=require", tmp_path)
    assert cfg.connect_args["timeout"] == 10
    assert cfg.connect_args["command_timeout"] == 30
    assert cfg.connect_args["server_settings"] == {"application_name": "tayyari"}
    assert cfg.engine_kwargs == {
        "hide_parameters": True,
        "pool_size": 5,
        "max_overflow": 5,
        "pool_pre_ping": True,
        "pool_recycle": 240,
        "pool_timeout": 10,
    }


def test_direct_host_keeps_prepared_statements(tmp_path: Path) -> None:
    cfg = norm(f"postgresql://user:pw@{NEON}/neondb?sslmode=require", tmp_path)
    assert "statement_cache_size" not in cfg.connect_args
    assert "prepared_statement_name_func" not in cfg.connect_args
    assert "prepared_statement_cache_size" not in make_url(cfg.url).query


def test_pooler_host_disables_prepared_statements(tmp_path: Path) -> None:
    cfg = norm(f"postgresql://user:pw@{NEON_POOLED}/neondb?sslmode=require&channel_binding=require", tmp_path)
    assert make_url(cfg.url).query == {"prepared_statement_cache_size": "0"}
    assert cfg.connect_args["statement_cache_size"] == 0
    name_func = cfg.connect_args["prepared_statement_name_func"]
    first, second = name_func(), name_func()
    assert first != second
    assert first.startswith("__asyncpg_")
    assert "prepared_statement_cache_size" not in cfg.engine_kwargs  # it travels in the URL
    assert isinstance(cfg.connect_args["ssl"], ssl.SSLContext)


def test_pooler_url_reaches_the_asyncpg_dialect_as_int_zero(tmp_path: Path) -> None:
    cfg = norm(f"postgresql://user:pw@{NEON_POOLED}/neondb?sslmode=require", tmp_path)
    engine = create_async_engine(cfg.url, connect_args=cfg.connect_args, **cfg.engine_kwargs)
    _args, opts = engine.dialect.create_connect_args(engine.url)
    assert opts["prepared_statement_cache_size"] == 0
    assert opts["host"] == NEON_POOLED
    assert "sslmode" not in opts


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_local_postgres_needs_no_ssl(tmp_path: Path, host: str) -> None:
    cfg = norm(f"postgresql://user:pw@{host}:5432/app", tmp_path)
    assert "ssl" not in cfg.connect_args
    assert cfg.kind == "postgres"


def test_unset_sslmode_on_a_remote_host_still_gets_ssl(tmp_path: Path) -> None:
    cfg = norm("postgresql://user:pw@db.internal.example.com/app", tmp_path)
    assert isinstance(cfg.connect_args["ssl"], ssl.SSLContext)


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full", "prefer"])
def test_explicit_sslmode_gets_ssl_even_on_localhost(tmp_path: Path, mode: str) -> None:
    cfg = norm(f"postgresql://user:pw@localhost/app?sslmode={mode}", tmp_path)
    assert isinstance(cfg.connect_args["ssl"], ssl.SSLContext)
    assert "sslmode" not in make_url(cfg.url).query


def test_explicit_sslmode_disable_wins_even_on_a_remote_host(tmp_path: Path) -> None:
    cfg = norm("postgresql://user:pw@db.internal.example.com/app?sslmode=disable", tmp_path)
    assert "ssl" not in cfg.connect_args
    assert "sslmode" not in make_url(cfg.url).query


def test_asyncpg_style_ssl_parameter_is_understood(tmp_path: Path) -> None:
    cfg = norm("postgresql://user:pw@localhost/app?ssl=require", tmp_path)
    assert isinstance(cfg.connect_args["ssl"], ssl.SSLContext)
    assert "ssl" not in make_url(cfg.url).query
    off = norm("postgresql://user:pw@db.example.com/app?ssl=false", tmp_path)
    assert "ssl" not in off.connect_args


def test_other_libpq_only_parameters_are_dropped(tmp_path: Path) -> None:
    cfg = norm(
        f"postgresql://u:p@{NEON}/db?sslmode=require&connect_timeout=5&target_session_attrs=read-write"
        "&sslrootcert=/x/ca.pem&keepalives=1",
        tmp_path,
    )
    assert dict(make_url(cfg.url).query) == {}


def test_application_name_moves_to_server_settings(tmp_path: Path) -> None:
    cfg = norm(f"postgresql://u:p@{NEON}/db?application_name=reports", tmp_path)
    assert cfg.connect_args["server_settings"] == {"application_name": "reports"}
    assert "application_name" not in make_url(cfg.url).query


def test_libpq_options_become_server_settings_when_trivial(tmp_path: Path) -> None:
    cfg = norm(
        f"postgresql://u:p@{NEON}/db?options=-c%20statement_timeout%3D5000%20-csearch_path%3Dapp%20--lock_timeout%3D3s",
        tmp_path,
    )
    assert cfg.connect_args["server_settings"] == {
        "application_name": "tayyari",
        "statement_timeout": "5000",
        "search_path": "app",
        "lock_timeout": "3s",
    }
    assert "options" not in make_url(cfg.url).query


def test_untranslatable_options_are_dropped(tmp_path: Path) -> None:
    # Neon's routing hint for clients without SNI: asyncpg sends SNI, so it is simply dropped
    cfg = norm(f"postgresql://u:p@{NEON_POOLED}/db?options=endpoint%3Dep-cool-darkness-123456", tmp_path)
    assert cfg.connect_args["server_settings"] == {"application_name": "tayyari"}
    assert "options" not in make_url(cfg.url).query


def test_unknown_parameters_are_kept(tmp_path: Path) -> None:
    cfg = norm(f"postgresql://u:p@{NEON}/db?sslmode=require&server_settings_hint=x", tmp_path)
    assert dict(make_url(cfg.url).query) == {"server_settings_hint": "x"}


def test_special_characters_in_the_password_survive(tmp_path: Path) -> None:
    cfg = norm(f"postgresql://user:p%40ss%2Fw%3Ard@{NEON}/db?sslmode=require", tmp_path)
    assert make_url(cfg.url).password == "p@ss/w:rd"


# ───────────────────────────── safety ─────────────────────────────


def test_safe_url_masks_the_password(tmp_path: Path) -> None:
    cfg = norm(f"postgresql://user:hunter2@{NEON_POOLED}/neondb?sslmode=require", tmp_path)
    assert "hunter2" not in cfg.safe_url
    assert "***" in cfg.safe_url
    assert "hunter2" not in repr(cfg)
    assert "hunter2" not in str(cfg)
    assert cfg.safe_url.startswith("postgresql+asyncpg://user:***@")
    assert "hunter2" in cfg.url  # the real URL is still there for the engine


def test_safe_url_of_a_sqlite_config_is_just_the_path(tmp_path: Path) -> None:
    cfg = norm(None, tmp_path)
    assert cfg.safe_url.startswith("sqlite+aiosqlite:///")
    assert "tayyari.sqlite3" in cfg.safe_url


def test_dbconfig_is_frozen(tmp_path: Path) -> None:
    cfg = norm(None, tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.url = "x"  # type: ignore[misc]


@pytest.mark.parametrize(
    "url", ["mysql://u:topsecret@h/db", "mongodb://u:topsecret@h/db", "oracle://u:topsecret@h/x"]
)
def test_unsupported_schemes_raise_without_echoing_the_url(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError, match="Unsupported DATABASE_URL scheme") as excinfo:
        norm(url, tmp_path)
    assert "topsecret" not in str(excinfo.value)


def test_unparseable_url_raises_without_echoing_it(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="could not be parsed") as excinfo:
        norm("this is not a url with password hunter2", tmp_path)
    assert "hunter2" not in str(excinfo.value)


# ───────────────────────────── the config really works with SQLAlchemy ─────────────────────────────


async def test_default_sqlite_config_opens_a_real_connection(tmp_path: Path) -> None:
    cfg = norm(None, tmp_path)
    engine = create_async_engine(cfg.url, connect_args=cfg.connect_args, **cfg.engine_kwargs)
    try:
        async with engine.connect() as conn:
            assert (await conn.execute(text("select 1"))).scalar_one() == 1
    finally:
        await engine.dispose()
    assert (tmp_path / "tayyari.sqlite3").exists()


def test_postgres_config_builds_an_engine_without_connecting(tmp_path: Path) -> None:
    cfg = norm(f"postgres://user:pw@{NEON_POOLED}/neondb?sslmode=require&channel_binding=require", tmp_path)
    engine = create_async_engine(cfg.url, connect_args=cfg.connect_args, **cfg.engine_kwargs)
    assert engine.dialect.name == "postgresql"
    assert engine.pool.size() == 5  # type: ignore[attr-defined]
