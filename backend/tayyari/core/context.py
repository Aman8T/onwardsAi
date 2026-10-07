"""Per-request context variables, merged into every log line by `core.logging`.

Use `bind` around a unit of work (a request, an analysis, a chat turn); the previous values are restored
on exit, so nested and concurrent tasks never see each other's ids::

    with bind(request_id=rid, ip_hash=ip):
        ...
        with bind(profile_id=pid):
            log.info("saved")          # carries request_id, ip_hash and profile_id
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

request_id: ContextVar[str | None] = ContextVar("tayyari_request_id", default=None)
analysis_id: ContextVar[str | None] = ContextVar("tayyari_analysis_id", default=None)
profile_id: ContextVar[str | None] = ContextVar("tayyari_profile_id", default=None)
session_id: ContextVar[str | None] = ContextVar("tayyari_session_id", default=None)
ip_hash: ContextVar[str | None] = ContextVar("tayyari_ip_hash", default=None)

_VARS: dict[str, ContextVar[str | None]] = {
    "request_id": request_id,
    "analysis_id": analysis_id,
    "profile_id": profile_id,
    "session_id": session_id,
    "ip_hash": ip_hash,
}

KEYS: tuple[str, ...] = tuple(_VARS)
"""The bindable names, in log order."""


@contextmanager
def bind(**values: str | uuid.UUID | None) -> Iterator[None]:
    """Set the given context variables for the duration of the ``with`` block, then restore them.

    ``None`` clears a variable inside the block. A ``UUID`` is stored as its string form. Unknown names
    raise ``TypeError`` (a typo must not silently drop a correlation id).
    """
    unknown = sorted(set(values) - set(_VARS))
    if unknown:
        raise TypeError(f"unknown context variable(s): {', '.join(unknown)}")
    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = []
    try:
        for name, value in values.items():
            var = _VARS[name]
            tokens.append((var, var.set(None if value is None else str(value))))
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def current() -> dict[str, str]:
    """The variables that are set right now (non-None), in `KEYS` order."""
    out: dict[str, str] = {}
    for name, var in _VARS.items():
        value = var.get()
        if value is not None:
            out[name] = value
    return out


__all__ = [
    "KEYS",
    "analysis_id",
    "bind",
    "current",
    "ip_hash",
    "profile_id",
    "request_id",
    "session_id",
]
