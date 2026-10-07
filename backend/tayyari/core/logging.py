"""Logging: structlog on top of the stdlib, so our logs and uvicorn / httpx / anthropic share one pipeline.

- JSON lines in prod, a readable console renderer everywhere else.
- Every line carries the ids bound with `core.context.bind` (request, analysis, profile, session, ip hash)
  and an ISO-8601 UTC timestamp.
- `core.redact.redact_event_dict` runs last: resume text, notes, prompts and emails never reach a log.
  Log structure (counts, kinds, durations, usage), not content.
- Noisy libraries are held at WARNING (uvicorn's access log stays on locally).
- Logfire is optional: used only when ``LOGFIRE_TOKEN`` is set and ``import logfire`` works.

`configure_logging` is idempotent: calling it again replaces the handler it installed earlier and leaves
any other handler (pytest's caplog, for example) alone.
"""

from __future__ import annotations

import importlib
import logging
import sys
from collections.abc import MutableMapping, Sequence
from typing import TYPE_CHECKING, Any, cast

import structlog
from structlog.typing import Processor

from . import context
from .redact import redact_event_dict

if TYPE_CHECKING:
    from .settings import Settings

_HANDLER_MARK = "_tayyari_handler"

_QUIET_LOGGERS: dict[str, int] = {
    "uvicorn": logging.WARNING,
    "uvicorn.error": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "anthropic": logging.WARNING,
    "asyncio": logging.WARNING,
    "aiosqlite": logging.WARNING,
    "multipart": logging.WARNING,
    "python_multipart": logging.WARNING,
    "sqlalchemy.engine": logging.WARNING,
    "alembic": logging.INFO,
}
_UVICORN_ACCESS = "uvicorn.access"


def reconfigure_streams() -> None:
    """Make stdout and stderr escape what they cannot encode instead of raising (idempotent).

    Logs carry model and page text. A legacy console (Windows cp1252) cannot encode all of it, and a failed
    write inside an error handler would hide the real error.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):  # replaced by a test harness or already closed
            pass


def add_request_context(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: add the `core.context` variables that are set (explicit event keys win)."""
    for key, value in context.current().items():
        event_dict.setdefault(key, value)
    return event_dict


def _logfire_processors(settings: Settings) -> list[Processor]:
    """Logfire's structlog processor, only when a token is configured and logfire is installed."""
    token = settings.logfire_token
    if token is None:
        return []
    try:
        logfire = importlib.import_module("logfire")
    except ImportError:
        return []
    try:
        logfire.configure(
            token=token.get_secret_value(),
            service_name="tayyari",
            environment=settings.env,
            console=False,
        )
        processor_cls = getattr(logfire, "StructlogProcessor", None)
        return [processor_cls()] if processor_cls is not None else []
    except Exception as exc:  # observability must never stop the app from starting
        logging.getLogger(__name__).warning(
            "Logfire is configured but could not start: %s", type(exc).__name__
        )
        return []


def _shared_processors(settings: Settings) -> list[Processor]:
    processors: list[Processor] = [
        add_request_context,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    if settings.env == "prod":
        processors.append(structlog.processors.format_exc_info)  # so tracebacks pass through redaction
    processors.append(redact_event_dict)
    processors.extend(_logfire_processors(settings))
    return processors


def configure_logging(settings: Settings) -> None:
    """Install the structlog pipeline and a stdout handler on the root logger."""
    reconfigure_streams()
    shared = _shared_processors(settings)
    level = logging.getLevelNamesMapping()[settings.log_level]
    prod = settings.env == "prod"

    renderer: Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if prod
        else structlog.dev.ConsoleRenderer(colors=bool(getattr(sys.stdout, "isatty", lambda: False)()))
    )

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=prod,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=cast(Sequence[Processor], shared),
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    setattr(handler, _HANDLER_MARK, True)

    root = logging.getLogger()
    for existing in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    for name, quiet_level in _QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(quiet_level)
    access = logging.getLogger(_UVICORN_ACCESS)
    access.setLevel(logging.WARNING if prod else logging.INFO)
    # uvicorn installs its own handlers and stops propagation; send everything through ours instead.
    for name in ("uvicorn", "uvicorn.error", _UVICORN_ACCESS):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """A structlog logger (``log.info("analysis_done", skills=12)``); pass ``__name__`` for the logger name."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


__all__ = ["add_request_context", "configure_logging", "get_logger", "reconfigure_streams"]
