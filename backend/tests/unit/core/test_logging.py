from __future__ import annotations

import io
import json
import logging
import sys
import types
from collections.abc import Callable, Iterator, MutableMapping
from datetime import datetime
from typing import Any

import pytest
import structlog

from tayyari.core.context import bind
from tayyari.core.logging import (
    _logfire_processors,
    add_request_context,
    configure_logging,
    get_logger,
    reconfigure_streams,
)
from tayyari.core.settings import Settings

MakeSettings = Callable[..., Settings]
HANDLER_MARK = "_tayyari_handler"
TOUCHED = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore", "anthropic", "alembic")


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    saved = {
        name: (
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
            list(logging.getLogger(name).handlers),
        )
        for name in TOUCHED
    }
    yield
    for name, (level, propagate, handlers) in saved.items():
        target = logging.getLogger(name)
        target.setLevel(level)
        target.propagate = propagate
        target.handlers[:] = handlers
    structlog.reset_defaults()


def json_lines(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


# ----------------------------------------------------------------------------- prod: JSON


def test_prod_writes_json_lines_with_context_timestamp_and_redaction(
    capsys: pytest.CaptureFixture[str], make_settings: MakeSettings
) -> None:
    configure_logging(make_settings(env="prod"))
    log = get_logger("tayyari.test")
    resume = "Jane Doe, 5 years of React"
    with bind(request_id="req-1", profile_id="prof-1", ip_hash="abcd1234abcd1234"):
        log.info("analysis_started", sources=3, resume=resume, reason="mail jane@example.com")
    (rec,) = json_lines(capsys.readouterr().out)
    assert rec["event"] == "analysis_started"
    assert rec["level"] == "info"
    assert rec["logger"] == "tayyari.test"
    assert rec["request_id"] == "req-1"
    assert rec["profile_id"] == "prof-1"
    assert rec["ip_hash"] == "abcd1234abcd1234"
    assert rec["sources"] == 3
    assert rec["resume"] == f"<redacted {len(resume)} chars>"
    assert rec["reason"] == "mail [email]"
    stamp = datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00"))
    assert stamp.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert rec["timestamp"].endswith("Z")


def test_explicit_event_fields_beat_bound_context(
    capsys: pytest.CaptureFixture[str], make_settings: MakeSettings
) -> None:
    configure_logging(make_settings(env="prod"))
    with bind(request_id="bound"):
        get_logger("t").info("e", request_id="explicit")
    (rec,) = json_lines(capsys.readouterr().out)
    assert rec["request_id"] == "explicit"


def test_prod_exceptions_are_formatted_and_redacted(
    capsys: pytest.CaptureFixture[str], make_settings: MakeSettings
) -> None:
    configure_logging(make_settings(env="prod"))
    log = get_logger("tayyari.test")
    try:
        raise ValueError("bad input from jane@example.com")
    except ValueError:
        log.exception("boom")
    (rec,) = json_lines(capsys.readouterr().out)
    assert rec["level"] == "error"
    assert "ValueError" in rec["exception"]
    assert "jane@example.com" not in rec["exception"]
    assert "[email]" in rec["exception"]


def test_stdlib_loggers_flow_through_the_same_pipeline(
    capsys: pytest.CaptureFixture[str], make_settings: MakeSettings
) -> None:
    configure_logging(make_settings(env="prod"))
    with bind(request_id="req-9"):
        logging.getLogger("anthropic").warning("rate limited, contact ops@example.com")
    (rec,) = json_lines(capsys.readouterr().out)
    assert rec["logger"] == "anthropic"
    assert rec["level"] == "warning"
    assert rec["event"] == "rate limited, contact [email]"
    assert rec["request_id"] == "req-9"


def test_chatty_libraries_are_held_at_warning(
    capsys: pytest.CaptureFixture[str], make_settings: MakeSettings
) -> None:
    configure_logging(make_settings(env="prod"))
    logging.getLogger("httpx").info("HTTP Request: POST https://api.anthropic.com/v1/messages 200 OK")
    logging.getLogger("anthropic").info("request details")
    logging.getLogger("uvicorn.error").info("Started server process")
    logging.getLogger("httpx").warning("timeout talking to a server")
    events = [r["event"] for r in json_lines(capsys.readouterr().out)]
    assert events == ["timeout talking to a server"]


def test_alembic_migration_steps_stay_visible(
    capsys: pytest.CaptureFixture[str], make_settings: MakeSettings
) -> None:
    configure_logging(make_settings(env="prod"))
    logging.getLogger("alembic.runtime.migration").info("Running upgrade  -> 0001")
    assert [r["event"] for r in json_lines(capsys.readouterr().out)] == ["Running upgrade  -> 0001"]


def test_uvicorn_access_is_on_locally_and_off_in_prod(
    capsys: pytest.CaptureFixture[str], make_settings: MakeSettings
) -> None:
    configure_logging(make_settings(env="local"))
    logging.getLogger("uvicorn.access").info("GET /api/health 200")
    assert "GET /api/health 200" in capsys.readouterr().out
    configure_logging(make_settings(env="prod"))
    logging.getLogger("uvicorn.access").info("GET /api/health 200")
    assert capsys.readouterr().out == ""


def test_uvicorn_handlers_are_replaced_by_ours(make_settings: MakeSettings) -> None:
    own = logging.StreamHandler(io.StringIO())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.addHandler(own)
        lg.propagate = False
    configure_logging(make_settings(env="prod"))
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        assert lg.handlers == []
        assert lg.propagate is True


def test_log_level_setting_is_honoured(
    capsys: pytest.CaptureFixture[str], make_settings: MakeSettings
) -> None:
    configure_logging(make_settings(env="prod", log_level="WARNING"))
    assert logging.getLogger().level == logging.WARNING
    log = get_logger("t")
    log.info("hidden")
    log.warning("shown")
    assert [r["event"] for r in json_lines(capsys.readouterr().out)] == ["shown"]


# ----------------------------------------------------------------------------- local: console


def test_local_uses_the_console_renderer(
    capsys: pytest.CaptureFixture[str], make_settings: MakeSettings
) -> None:
    configure_logging(make_settings(env="local"))
    get_logger("tayyari.test").info("hello_console", count=2)
    out = capsys.readouterr().out
    assert "hello_console" in out
    assert "count" in out
    assert not out.lstrip().startswith("{")


def test_local_console_still_redacts(capsys: pytest.CaptureFixture[str], make_settings: MakeSettings) -> None:
    configure_logging(make_settings(env="test"))
    get_logger("t").info("e", resume="very secret resume text", who="jane@example.com")
    out = capsys.readouterr().out
    assert "very secret" not in out
    assert "jane@example.com" not in out
    assert "<redacted 23 chars>" in out


# ----------------------------------------------------------------------------- idempotence


def test_configuring_twice_installs_one_handler_and_keeps_foreign_ones(make_settings: MakeSettings) -> None:
    root = logging.getLogger()
    foreign = logging.NullHandler()
    root.addHandler(foreign)
    try:
        configure_logging(make_settings(env="prod"))
        configure_logging(make_settings(env="local"))
        ours = [h for h in root.handlers if getattr(h, HANDLER_MARK, False)]
        assert len(ours) == 1
        assert foreign in root.handlers
    finally:
        root.removeHandler(foreign)
        for h in [h for h in root.handlers if getattr(h, HANDLER_MARK, False)]:
            root.removeHandler(h)


def test_reconfiguring_switches_the_renderer(
    capsys: pytest.CaptureFixture[str], make_settings: MakeSettings
) -> None:
    configure_logging(make_settings(env="prod"))
    get_logger("a").info("first")
    assert json_lines(capsys.readouterr().out)
    configure_logging(make_settings(env="local"))
    get_logger("a").info("second")
    assert not capsys.readouterr().out.lstrip().startswith("{")


def test_get_logger_returns_a_bindable_logger(
    capsys: pytest.CaptureFixture[str], make_settings: MakeSettings
) -> None:
    configure_logging(make_settings(env="prod"))
    bound = get_logger("tayyari.bound").bind(stage="synth")
    bound.info("tick")
    (rec,) = json_lines(capsys.readouterr().out)
    assert rec["stage"] == "synth"
    assert rec["logger"] == "tayyari.bound"


def test_add_request_context_processor_adds_only_what_is_bound() -> None:
    event: MutableMapping[str, Any] = {"event": "e"}
    assert add_request_context(None, "info", event) == {"event": "e"}
    with bind(session_id="s1"):
        assert add_request_context(None, "info", {"event": "e"}) == {"event": "e", "session_id": "s1"}


# ----------------------------------------------------------------------------- stdout/stderr escaping


def test_reconfigure_streams_escapes_unencodable_text_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = io.BytesIO()
    wrapper = io.TextIOWrapper(raw, encoding="ascii", errors="strict")
    monkeypatch.setattr(sys, "stdout", wrapper)
    with pytest.raises(UnicodeEncodeError):
        wrapper.write("café")
        wrapper.flush()
    raw.seek(0)
    raw.truncate()
    reconfigure_streams()
    reconfigure_streams()
    wrapper.write("café")
    wrapper.flush()
    assert raw.getvalue() == b"caf\\xe9"


def test_reconfigure_streams_tolerates_streams_that_cannot_be_reconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdout", io.StringIO())  # no .reconfigure at all
    closed = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    closed.close()
    monkeypatch.setattr(sys, "stderr", closed)  # reconfigure raises ValueError
    reconfigure_streams()


# ----------------------------------------------------------------------------- optional logfire


def fake_logfire(configure: Callable[..., None], processor: Callable[[], Any] | None) -> types.ModuleType:
    module = types.ModuleType("logfire")
    module.configure = configure  # type: ignore[attr-defined]
    if processor is not None:
        module.StructlogProcessor = processor  # type: ignore[attr-defined]
    return module


def test_logfire_is_never_touched_without_a_token(
    monkeypatch: pytest.MonkeyPatch, make_settings: MakeSettings
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setitem(sys.modules, "logfire", fake_logfire(lambda **kw: calls.append(kw), lambda: object()))
    assert _logfire_processors(make_settings()) == []
    assert calls == []


def test_logfire_missing_package_is_fine(
    monkeypatch: pytest.MonkeyPatch, make_settings: MakeSettings
) -> None:
    monkeypatch.setitem(sys.modules, "logfire", None)  # makes `import logfire` raise ImportError
    assert _logfire_processors(make_settings(logfire_token="tok")) == []


def test_logfire_is_configured_when_token_and_package_exist(
    monkeypatch: pytest.MonkeyPatch, make_settings: MakeSettings
) -> None:
    calls: list[dict[str, Any]] = []
    marker = lambda _l, _m, event: event  # noqa: E731
    monkeypatch.setitem(sys.modules, "logfire", fake_logfire(lambda **kw: calls.append(kw), lambda: marker))
    assert _logfire_processors(make_settings(logfire_token="tok")) == [marker]
    assert calls == [{"token": "tok", "service_name": "tayyari", "environment": "test", "console": False}]


def test_logfire_failure_never_stops_logging(
    monkeypatch: pytest.MonkeyPatch, make_settings: MakeSettings
) -> None:
    def explode(**_: Any) -> None:
        raise RuntimeError("no network")

    monkeypatch.setitem(sys.modules, "logfire", fake_logfire(explode, lambda: object()))
    assert _logfire_processors(make_settings(logfire_token="tok")) == []


def test_logfire_sees_events_only_after_redaction(
    monkeypatch: pytest.MonkeyPatch, make_settings: MakeSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[dict[str, Any]] = []

    def processor(_logger: Any, _method: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        seen.append(dict(event))
        return event

    monkeypatch.setitem(sys.modules, "logfire", fake_logfire(lambda **_: None, lambda: processor))
    configure_logging(make_settings(env="prod", logfire_token="tok"))
    get_logger("t").info("e", resume="the whole resume", who="jane@example.com")
    capsys.readouterr()
    assert len(seen) == 1
    assert seen[0]["resume"] == "<redacted 16 chars>"
    assert seen[0]["who"] == "[email]"
