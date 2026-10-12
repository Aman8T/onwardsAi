"""Sign-ins: the cookie token, the stored session, and the cookie itself.

The cookie value is a random 32-byte token (urlsafe). Only its sha256 is stored (``auth_sessions.id``), so a copy
of the database cannot be replayed as a cookie. A sign-in lasts ``session_days`` and slides: each use pushes the
expiry out again, but the row is only rewritten when ``last_seen_at`` is over an hour old, so ordinary requests
stay reads. The cookie is ``HttpOnly``, ``SameSite=Lax`` (it travels with a top-level navigation, which the Google
callback and the emailed link are; not with a cross-site fetch), ``Path=/``, and ``Secure`` in prod.

`SessionService.resolve` turns a cookie value into a `SessionContext` (account, session, and whether the sliding
refresh just ran, so the caller can re-issue the cookie with a fresh lifetime), or None for anything that is not a
live sign-in: no cookie, an unknown or malformed token, an expired session (which is deleted on the spot).
"""

from __future__ import annotations

import dataclasses
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from starlette.responses import Response

from tayyari.core.clock import Clock
from tayyari.core.hashing import sha256_hex
from tayyari.core.logging import get_logger
from tayyari.core.settings import AuthSettings, Settings
from tayyari.storage.ports import StoredAuthSession, StoredUser, UserRepo

log = get_logger(__name__)

TOKEN_BYTES: Final = 32
TOKEN_MAX_CHARS: Final = 128
"""A cookie longer than this is not one of ours; it is not even hashed."""
TOUCH_INTERVAL: Final = timedelta(hours=1)
"""``last_seen_at`` (and the expiry) move forward at most this often."""
USER_AGENT_MAX_CHARS: Final = 200
"""What `auth_sessions.user_agent` can hold."""


def new_token() -> str:
    """A random token: 32 bytes, urlsafe base64 (43 characters)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """The sha256 hex of a token: what is stored instead of the token."""
    return sha256_hex(token)


def clip_user_agent(value: str | None) -> str | None:
    """The User-Agent kept on a session row: printable, at most `USER_AGENT_MAX_CHARS` characters."""
    if not value:
        return None
    cleaned = "".join(ch for ch in value if ch.isprintable())[:USER_AGENT_MAX_CHARS].strip()
    return cleaned or None


@dataclass(frozen=True, slots=True)
class SessionContext:
    """A live sign-in: who, which session, and whether this call slid its expiry forward."""

    user: StoredUser
    session: StoredAuthSession
    token: str = dataclasses.field(repr=False)
    refreshed: bool = False


class SessionService:
    def __init__(self, users: UserRepo, clock: Clock, settings: AuthSettings) -> None:
        self._users = users
        self._clock = clock
        self._settings = settings

    @property
    def lifetime(self) -> timedelta:
        return timedelta(days=self._settings.session_days)

    async def start(
        self, user_id: str, *, ip_hash: str | None, user_agent: str | None
    ) -> tuple[str, StoredAuthSession]:
        """Sign ``user_id`` in: store a session and return the cookie token (shown once, here) with its row."""
        token = new_token()
        now = self._clock.now()
        record = StoredAuthSession(
            id=hash_token(token),
            user_id=user_id,
            created_at=now,
            expires_at=now + self.lifetime,
            last_seen_at=now,
            ip_hash=ip_hash,
            user_agent=clip_user_agent(user_agent),
        )
        await self._users.create_session(record)
        return token, record

    async def resolve(self, token: str | None) -> SessionContext | None:
        """The live sign-in behind a cookie value, or None."""
        if not token or len(token) > TOKEN_MAX_CHARS:
            return None
        found = await self._users.get_session(hash_token(token))
        if found is None:
            return None
        session, user = found
        now = self._clock.now()
        if session.expires_at <= now:
            await self._users.delete_session(session.id)
            return None
        refreshed = False
        if now - session.last_seen_at >= TOUCH_INTERVAL:
            expires_at = now + self.lifetime
            try:
                await self._users.touch_session(session.id, last_seen_at=now, expires_at=expires_at)
            except Exception:
                # Not worth failing the request: the next one tries again.
                log.warning("session_touch_failed", exc_info=True)
            else:
                session = dataclasses.replace(session, last_seen_at=now, expires_at=expires_at)
                refreshed = True
        return SessionContext(user=user, session=session, token=token, refreshed=refreshed)

    async def end(self, token: str | None) -> bool:
        """Sign out: delete the session behind a cookie value. True when there was one."""
        if not token or len(token) > TOKEN_MAX_CHARS:
            return False
        return await self._users.delete_session(hash_token(token))


# ───────────────────────── the cookie ─────────────────────────


def cookies_are_secure(settings: Settings) -> bool:
    """``Secure`` is set in prod, and wherever the public URL is https (browsers accept it on http://localhost)."""
    return settings.is_prod or settings.auth.public_url.startswith("https://")


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    """Attach the sign-in cookie: HttpOnly, SameSite=Lax, Path=/, Max-Age = the session lifetime."""
    response.set_cookie(
        settings.auth.cookie_name,
        token,
        max_age=settings.auth.session_days * 86_400,
        path="/",
        httponly=True,
        samesite="lax",
        secure=cookies_are_secure(settings),
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Remove the sign-in cookie (same name, path and flags, so the browser matches it)."""
    response.delete_cookie(
        settings.auth.cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=cookies_are_secure(settings),
    )


__all__ = [
    "TOKEN_MAX_CHARS",
    "TOUCH_INTERVAL",
    "SessionContext",
    "SessionService",
    "clear_session_cookie",
    "clip_user_agent",
    "cookies_are_secure",
    "hash_token",
    "new_token",
    "set_session_cookie",
]
