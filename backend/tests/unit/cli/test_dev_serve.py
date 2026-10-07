"""``dev`` and ``serve`` call uvicorn with the right arguments (uvicorn itself is replaced)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import uvicorn

import tayyari
from tayyari.cli import dev, serve
from tayyari.core.settings import Settings, get_settings

Call = tuple[tuple[Any, ...], dict[str, Any]]
ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - an address the command is told to bind, never one a test binds


@pytest.fixture
def uvicorn_calls(monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    calls: list[Call] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    return calls


@pytest.fixture
def use_settings(
    monkeypatch: pytest.MonkeyPatch, make_settings: Callable[..., Settings]
) -> Callable[..., Settings]:
    """Make both commands read these settings instead of the process-wide ones."""

    def apply(**overrides: Any) -> Settings:
        settings = make_settings(**overrides)
        monkeypatch.setattr(dev, "get_settings", lambda: settings)
        monkeypatch.setattr(serve, "get_settings", lambda: settings)
        return settings

    return apply


# ───────────────────────── dev ─────────────────────────


def test_dev_runs_the_app_factory_with_reload(
    uvicorn_calls: list[Call], use_settings: Callable[..., Settings]
) -> None:
    use_settings(api_port=9123, log_level="DEBUG")
    assert dev.run() == 0
    assert len(uvicorn_calls) == 1
    args, kwargs = uvicorn_calls[0]
    assert args == ("tayyari.api.app:create_app",)
    assert kwargs == {
        "factory": True,
        "reload": True,
        "reload_dirs": [str(Path(tayyari.__file__).resolve().parent)],
        "host": "127.0.0.1",
        "port": 9123,
        "log_level": "debug",
    }


def test_dev_watches_the_tayyari_package_folder(
    uvicorn_calls: list[Call], use_settings: Callable[..., Settings]
) -> None:
    use_settings()
    dev.run()
    (reload_dir,) = uvicorn_calls[0][1]["reload_dirs"]
    assert (Path(reload_dir) / "api" / "app.py").is_file()


def test_dev_port_follows_the_environment(uvicorn_calls: list[Call], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_API_PORT", "9456")
    get_settings.cache_clear()
    dev.run()
    assert uvicorn_calls[0][1]["port"] == 9456


def test_dev_defaults_to_the_port_the_vite_proxy_targets(uvicorn_calls: list[Call]) -> None:
    dev.run()
    assert uvicorn_calls[0][1]["port"] == 8787


@pytest.mark.parametrize(
    ("level", "expected"), [("DEBUG", "debug"), ("INFO", "info"), ("WARNING", "warning"), ("ERROR", "error")]
)
def test_log_level_comes_from_the_settings(
    level: str, expected: str, uvicorn_calls: list[Call], use_settings: Callable[..., Settings]
) -> None:
    use_settings(log_level=level)
    dev.run()
    serve.run()
    assert [call[1]["log_level"] for call in uvicorn_calls] == [expected, expected]


def test_the_app_target_is_importable() -> None:
    """The string uvicorn gets must name a real factory."""
    module_name, _, attribute = dev.APP.partition(":")
    module = __import__(module_name, fromlist=[attribute])
    assert callable(getattr(module, attribute))


# ───────────────────────── serve ─────────────────────────


def test_serve_has_no_reload(uvicorn_calls: list[Call], use_settings: Callable[..., Settings]) -> None:
    use_settings(api_port=9000)
    assert serve.run() == 0
    args, kwargs = uvicorn_calls[0]
    assert args == ("tayyari.api.app:create_app",)
    assert kwargs == {
        "factory": True,
        "host": "127.0.0.1",
        "port": 9000,
        "log_level": "info",
    }
    assert "reload" not in kwargs
    assert "reload_dirs" not in kwargs


def test_serve_host_and_port_options_win(
    uvicorn_calls: list[Call], use_settings: Callable[..., Settings]
) -> None:
    use_settings(api_port=9000)
    serve.run(ALL_INTERFACES, 8123)
    kwargs = uvicorn_calls[0][1]
    assert (kwargs["host"], kwargs["port"]) == (ALL_INTERFACES, 8123)


# ───────────────────────── the start-up line ─────────────────────────


def test_the_banner_says_where_it_listens_and_never_shows_a_secret(
    uvicorn_calls: list[Call],
    use_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_settings(
        anthropic_api_key="sk-ant-very-secret",
        jev_api_key="jev-very-secret",
        github_token=None,
        database_url="postgresql://user:db-very-secret@db.example.com/app",
    )
    dev.run()
    out = capsys.readouterr().out
    assert "http://127.0.0.1:8787" in out
    assert "reload on" in out
    assert "anthropic=yes" in out
    assert "jev=yes" in out
    assert "github=no" in out
    for secret in ("sk-ant-very-secret", "jev-very-secret", "db-very-secret"):
        assert secret not in out


def test_the_serve_banner_says_reload_off(
    uvicorn_calls: list[Call], use_settings: Callable[..., Settings], capsys: pytest.CaptureFixture[str]
) -> None:
    use_settings()
    serve.run(ALL_INTERFACES, 8123)
    out = capsys.readouterr().out
    assert f"http://{ALL_INTERFACES}:8123" in out
    assert "reload off" in out
