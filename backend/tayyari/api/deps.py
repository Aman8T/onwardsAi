"""Who is calling, for logs and usage records: the request id, the client IP and its keyed hash.

`RequestIdMiddleware` (pure ASGI, so it never buffers or wraps a streaming body):
  - takes ``X-Request-ID`` from the request (sanitised, at most 64 characters) or makes a uuid4 hex id,
  - binds it into `core.context` for the whole request, so every log line carries it,
  - keeps it in ``scope["state"]["request_id"]`` and echoes it as an ``X-Request-ID`` response header.

`get_request_context(request)` is the FastAPI dependency routes use: a `RequestContext` with the request id,
the client IP (`guard.client_ip`, using the trusted-proxy settings), ``ip_hash = HMAC(key, ip)`` and the start
time. It also binds ``ip_hash`` into `core.context`. It reads these from ``app.state``, which the container
fills in:

    app.state.settings      a `core.settings.Settings` (``guard.trusted_proxy_hops``, ``guard.client_ip_header``);
                            `get_settings()` is used when absent
    app.state.ip_hash_key   the HMAC key as ``bytes``, or a zero-argument callable returning it (or None while it
                            is not ready); read once and then kept. Without a key ``ip_hash`` is None: an IP is
                            never hashed with a made-up key.
    app.state.clock         optional `core.clock.Clock` (default: the system clock), for ``started_at``

Only the hash of the IP is ever logged or stored. IPv6 addresses are hashed as their /64 network (see
`guard.client_ip.ip_subject`).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import HTTPConnection, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tayyari.contract.requests import RequestContext
from tayyari.core import context
from tayyari.core.clock import Clock
from tayyari.core.hashing import hmac_hex
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings, get_settings
from tayyari.guard.client_ip import client_ip, ip_subject

log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_MAX_CHARS = 64
_REQUEST_ID_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_CONTEXT_KEY = "request_context"
_KEY_CACHE_ATTR = "_ip_hash_key_resolved"
_WARNED_ATTR = "_ip_hash_key_warned"


def sanitize_request_id(raw: str | None) -> str | None:
    """A client-supplied id reduced to ``[A-Za-z0-9._-]`` and 64 characters, or None when nothing is left."""
    if not raw:
        return None
    cleaned = _REQUEST_ID_UNSAFE.sub("", raw)[:REQUEST_ID_MAX_CHARS]
    return cleaned or None


def new_request_id() -> str:
    """A fresh 32-character hex id."""
    return uuid.uuid4().hex


def _state(scope: Scope) -> dict[str, Any]:
    state = scope.get("state")
    if not isinstance(state, dict):
        state = {}
        scope["state"] = state
    return state


class RequestIdMiddleware:
    """Pure ASGI middleware: request id in, request id out, bound into the log context for the request.

    Add it with ``app.add_middleware(RequestIdMiddleware)``. It touches only the response *start* message, so
    streaming bodies pass through untouched.
    """

    def __init__(self, app: ASGIApp, *, header: str = REQUEST_ID_HEADER) -> None:
        self.app = app
        self.header = header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = sanitize_request_id(Headers(scope=scope).get(self.header)) or new_request_id()
        _state(scope)["request_id"] = rid
        header = self.header

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                MutableHeaders(scope=message)[header] = rid
            await send(message)

        # ip_hash is cleared here so that whatever get_request_context binds later is dropped with the request.
        with context.bind(request_id=rid, ip_hash=None):
            await self.app(scope, receive, send_with_id)


def _resolve_ip_hash_key(app_state: Any) -> bytes | None:
    """The HMAC key from ``app.state.ip_hash_key`` (bytes or a callable), cached after the first success."""
    cached = getattr(app_state, _KEY_CACHE_ATTR, None)
    if isinstance(cached, bytes):
        return cached
    raw = getattr(app_state, "ip_hash_key", None)
    if callable(raw):
        raw = raw()
    if isinstance(raw, bytes | bytearray) and raw:
        key = bytes(raw)
        setattr(app_state, _KEY_CACHE_ATTR, key)
        return key
    return None


def _settings(request: HTTPConnection) -> Settings:
    found = getattr(request.app.state, "settings", None)
    return found if isinstance(found, Settings) else get_settings()


def _now(request: HTTPConnection) -> datetime:
    clock = getattr(request.app.state, "clock", None)
    if isinstance(clock, Clock):
        return clock.now()
    return datetime.now(UTC)


def build_request_context(request: Request) -> RequestContext:
    """Compute the `RequestContext` for ``request`` (see the module docstring for where the inputs come from)."""
    state = _state(request.scope)
    rid = state.get("request_id")
    if not isinstance(rid, str) or not rid:
        rid = sanitize_request_id(request.headers.get(REQUEST_ID_HEADER)) or new_request_id()
        state["request_id"] = rid
    guard = _settings(request).guard
    ip = client_ip(request, trusted_hops=guard.trusted_proxy_hops, header=guard.client_ip_header)
    key = _resolve_ip_hash_key(request.app.state)
    ip_hash = hmac_hex(key, ip_subject(ip)) if ip is not None and key is not None else None
    if ip is not None and key is None and not getattr(request.app.state, _WARNED_ATTR, False):
        setattr(request.app.state, _WARNED_ATTR, True)
        log.warning("ip_hash_key_missing", effect="rate limits and usage records cannot tell clients apart")
    return RequestContext(request_id=rid, ip_hash=ip_hash, client_ip=ip, started_at=_now(request))


async def get_request_context(request: Request) -> RequestContext:
    """FastAPI dependency: the request's `RequestContext`, built once per request. Binds ``ip_hash`` into the
    log context (and the request id, if `RequestIdMiddleware` is not installed)."""
    cached = _state(request.scope).get(_CONTEXT_KEY)
    if isinstance(cached, RequestContext):
        return cached
    rc = build_request_context(request)
    _state(request.scope)[_CONTEXT_KEY] = rc
    context.request_id.set(rc.request_id)
    context.ip_hash.set(rc.ip_hash)
    return rc


__all__ = [
    "REQUEST_ID_HEADER",
    "REQUEST_ID_MAX_CHARS",
    "RequestIdMiddleware",
    "build_request_context",
    "get_request_context",
    "new_request_id",
    "sanitize_request_id",
]
