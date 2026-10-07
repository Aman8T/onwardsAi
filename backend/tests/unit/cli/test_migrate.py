"""``migrate`` on a temporary SQLite file, and its failure paths."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tayyari.cli import migrate
from tayyari.core.settings import Settings
from tayyari.storage.migrate import head_revision


@pytest.fixture
def use_settings(
    monkeypatch: pytest.MonkeyPatch, make_settings: Callable[..., Settings]
) -> Callable[..., Settings]:
    def apply(**overrides: Any) -> Settings:
        settings = make_settings(**overrides)
        monkeypatch.setattr(migrate, "get_settings", lambda: settings)
        return settings

    return apply


def database_file(settings: Settings) -> Path:
    return settings.data_dir / "tayyari.sqlite3"


def test_migrates_a_fresh_sqlite_file_to_head(
    use_settings: Callable[..., Settings], capsys: pytest.CaptureFixture[str]
) -> None:
    settings = use_settings()
    assert not database_file(settings).exists()

    assert migrate.run() == 0

    out = capsys.readouterr().out
    assert f"revision {head_revision()}" in out
    assert settings.db.safe_url in out
    with sqlite3.connect(database_file(settings)) as conn:
        (version,) = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert version == head_revision()
    assert {"profiles", "chat_sessions", "llm_calls", "spend_daily"} <= tables


def test_running_it_twice_is_harmless(
    use_settings: Callable[..., Settings], capsys: pytest.CaptureFixture[str]
) -> None:
    use_settings()
    assert migrate.run() == 0
    assert migrate.run() == 0
    assert capsys.readouterr().out.count(f"revision {head_revision()}") == 2


def test_an_explicit_sqlite_url_is_used(use_settings: Callable[..., Settings], tmp_path: Path) -> None:
    target = tmp_path / "elsewhere" / "custom.sqlite3"
    use_settings(database_url=f"sqlite:///{target.as_posix()}")
    assert migrate.run() == 0
    assert target.is_file()


def test_a_failure_returns_1_and_hides_the_password(
    use_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_settings(database_url="postgresql://app:hunter2-secret@db.example.com/tayyari")

    def refuse(cfg: object) -> str:
        raise ConnectionError("could not connect to postgresql://app:hunter2-secret@db.example.com/tayyari")

    monkeypatch.setattr(migrate, "migrate_to_head_sync", refuse)
    assert migrate.run() == 1

    captured = capsys.readouterr()
    assert "Migration failed: ConnectionError" in captured.err
    assert "***" in captured.err
    assert "hunter2-secret" not in captured.out + captured.err


def test_an_unusable_database_url_returns_1(
    use_settings: Callable[..., Settings], capsys: pytest.CaptureFixture[str]
) -> None:
    use_settings(database_url="mysql://user:pw@host/db")
    assert migrate.run() == 1
    err = capsys.readouterr().err
    assert "Invalid database configuration" in err
    assert "pw@host" not in err


def test_an_unreachable_sqlite_path_returns_1(
    use_settings: Callable[..., Settings], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    use_settings(database_url=f"sqlite:///{(blocker / 'db.sqlite3').as_posix()}")
    assert migrate.run() == 1
    assert "Migration failed" in capsys.readouterr().err


def test_the_password_scrubber_leaves_other_text_alone(make_settings: Callable[..., Settings]) -> None:
    cfg = make_settings(database_url="postgresql://u:pw123@h.example.com/db").db
    assert migrate._scrub("failed for pw123 twice: pw123", cfg) == "failed for *** twice: ***"
    sqlite_cfg = make_settings().db
    assert migrate._scrub("nothing to hide", sqlite_cfg) == "nothing to hide"
