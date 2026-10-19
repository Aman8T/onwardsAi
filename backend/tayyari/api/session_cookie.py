"""The session cookie, set from wherever in a request the session was decided: a pure ASGI middleware.

Signing in from the Leafout site's cookie happens inside `api.deps.get_auth_context`, on whatever GET or POST first
needs to know who is calling, so no route owns the response. A route can also be a streaming one (analysis, chat), so
the cookie cannot be added to a response object either. Instead, `get_auth_context` leaves a note in
``scope["state"]`` and `SessionCookieMiddleware` acts on it when the response *starts*:

    `remember_session(scope, token)`   a session was just made (or swapped in): ``Set-Cookie: tayyari_session=<token>``
    `forget_session(scope)`            the session was just ended: clear the cookie in the browser too

It touches only the ``http.response.start`` message, so a streaming body passes through untouched. A route that set (or
cleared) the session cookie itself, such as the sign-in callbacks, logout and account deletion, wins: the middleware adds
nothing when the response already carries a ``tayyari_session`` cookie. The cookie's flags are the same as everywhere else
(`auth.sessions.set_session_cookie`): host-only, HttpOnly, SameSite=Lax, Secure in prod.
"""

from __future__ import annotations

from typing import Any, Final

from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tayyari.auth.sessions import clear_session_cookie, set_session_cookie
from tayyari.core.settings import Settings, get_settings

_NOTE_KEY: Final = "session_cookie_note"
_CLEAR: Final = "clear"


def _state(scope: Scope) -> dict[str, Any]:
    state = scope.get("state")
    if not isinstance(state, dict):
        state = {}
        scope["state"] = state
    return state


def remember_session(scope: Scope, token: str) -> None:
    """Ask for ``Set-Cookie: tayyari_session=<token>`` on this request's response."""
    _state(scope)[_NOTE_KEY] = token


def forget_session(scope: Scope) -> None:
    """Ask for the session cookie to be cleared on this request's response."""
    _state(scope)[_NOTE_KEY] = _CLEAR


def pending_note(scope: Scope) -> str | None:
    """The note left for the middleware: a token, ``"clear"``, or None (for tests)."""
    note = _state(scope).get(_NOTE_KEY)
    return note if isinstance(note, str) else None


def _cookie_headers(note: str, settings: Settings) -> list[bytes]:
    probe = Response()
    if note == _CLEAR:
        clear_session_cookie(probe, settings)
    else:
        set_session_cookie(probe, note, settings)
    return [value for name, value in probe.raw_headers if name == b"set-cookie"]


def _already_sets(message: Message, name: str) -> bool:
    prefix = f"{name}=".encode("latin-1")
    return any(
        key.lower() == b"set-cookie" and value.startswith(prefix) for key, value in message.get("headers", [])
    )


class SessionCookieMiddleware:
    """Pure ASGI middleware: see the module docstring. Add it with ``app.add_middleware`` (innermost)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # The same dict is shared with the middlewares above (`RequestIdMiddleware` creates it), so a note left deep in
        # the request is visible here.
        _state(scope)

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                note = pending_note(scope)
                if note is not None:
                    app = scope.get("app")
                    found = getattr(getattr(app, "state", None), "settings", None)
                    settings = found if isinstance(found, Settings) else get_settings()
                    if not _already_sets(message, settings.auth.cookie_name):
                        message.setdefault("headers", [])
                        headers = MutableHeaders(scope=message)
                        for value in _cookie_headers(note, settings):
                            headers.append("set-cookie", value.decode("latin-1"))
            await send(message)

        await self.app(scope, receive, send_with_cookie)


__all__ = [
    "SessionCookieMiddleware",
    "forget_session",
    "pending_note",
    "remember_session",
]
