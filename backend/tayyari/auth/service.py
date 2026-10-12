"""`AuthService`: the one object the auth routes and the dependencies talk to.

It composes the parts (`google.GoogleOAuth`, `magic.MagicLinks`, `sessions.SessionService`) and keeps the rules
that cross them in one place:

- both sign-in paths end the same way: find or create the account, start a session, hand back the cookie token
  and the validated place to send the browser (`SignedIn`);
- `providers` says which paths this server offers, so the web app hides what cannot work;
- nothing here logs an email address, a token or a cookie value; the log context carries the account id only.

The routes own HTTP (redirects, cookies, status codes); this class owns the decisions.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

import httpx2

from tayyari.core import context
from tayyari.core.clock import Clock
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings
from tayyari.storage.ports import LimitRepo, StoredUser, UserRepo

from .email import EmailSender
from .google import (
    CALLBACK_PATH,
    GoogleError,
    GoogleOAuth,
    new_state,
    read_state,
    safe_next,
    sign_state,
)
from .magic import MagicLinks, MagicOutcome
from .sessions import SessionService

log = get_logger(__name__)

LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True, slots=True)
class SignedIn:
    """A completed sign-in: set ``token`` as the session cookie and send the browser to ``next_path``."""

    user: StoredUser
    token: str = field(repr=False)
    next_path: str = "/"


@dataclass(frozen=True, slots=True)
class GoogleStart:
    """Redirect the browser to ``url`` and set ``cookie_value`` as the (short-lived) OAuth state cookie."""

    url: str
    cookie_value: str = field(repr=False)


class AuthService:
    def __init__(
        self,
        *,
        settings: Settings,
        users: UserRepo,
        limits: LimitRepo,
        clock: Clock,
        state_key: bytes,
        mailer: EmailSender | None = None,
        http: httpx2.AsyncClient | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._users = users
        self._clock = clock
        self._state_key = state_key
        self._owns_http = http is None
        self._http = (
            http
            if http is not None
            else httpx2.AsyncClient(timeout=httpx2.Timeout(10.0), transport=transport)
        )
        self.sessions = SessionService(users, clock, settings.auth)
        self.magic = MagicLinks(
            settings=settings, users=users, limits=limits, clock=clock, key=state_key, mailer=mailer
        )
        if settings.is_prod and urlsplit(settings.auth.public_url).hostname in LOCAL_HOSTS:
            # FastAPI Cloud sets FASTAPI_PUBLIC_URL; if it did not arrive, emailed links and Google's callback
            # would point at a developer's machine.
            log.warning(
                "auth_public_url_is_local",
                fix="set TAYYARI_AUTH__PUBLIC_URL to the app's public https URL",
            )
        self.google: GoogleOAuth | None = None
        client_id, client_secret = settings.google_client_id, settings.google_client_secret
        if client_id is not None and client_secret is not None:
            self.google = GoogleOAuth(
                client_id=client_id.get_secret_value(),
                client_secret=client_secret.get_secret_value(),
                redirect_uri=f"{settings.auth.public_url}{CALLBACK_PATH}",
                http=self._http,
                clock=clock,
                transport=transport,
            )

    @property
    def providers(self) -> tuple[bool, bool]:
        """``(google, magic)``: which ways of signing in work on this server."""
        return self.google is not None, self.magic.enabled

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ───────────────────────── google ─────────────────────────

    def google_start(self, next_path: str | None) -> GoogleStart:
        """Begin a Google sign-in. Raises `GoogleError` when Google is not configured."""
        if self.google is None:
            raise GoogleError("not_configured")
        state = new_state(safe_next(next_path), now_ts=int(self._clock.now().timestamp()))
        return GoogleStart(
            url=self.google.authorization_url(state), cookie_value=sign_state(self._state_key, state)
        )

    async def google_finish(
        self,
        *,
        code: str | None,
        state: str | None,
        cookie_value: str | None,
        ip_hash: str | None,
        user_agent: str | None,
    ) -> SignedIn:
        """Complete a Google sign-in from the callback's ``code`` and ``state`` and the state cookie.

        Raises `GoogleError` (any reason) when the flow cannot be trusted or completed.
        """
        if self.google is None:
            raise GoogleError("not_configured")
        saved = read_state(self._state_key, cookie_value, now_ts=int(self._clock.now().timestamp()))
        if saved is None:
            raise GoogleError("state_cookie_missing_or_invalid")
        if not state or not _same(state, saved.state):
            raise GoogleError("state_mismatch")
        if not code:
            raise GoogleError("no_code")
        identity = await self.google.identity(code, saved)
        user = await self._users.sign_in_google(
            sub=identity.sub,
            email=identity.email,
            name=identity.name,
            picture_url=identity.picture,
            locale=identity.locale,
            now=self._clock.now(),
        )
        return await self._signed_in(user, saved.next_path, ip_hash=ip_hash, user_agent=user_agent)

    # ───────────────────────── email link ─────────────────────────

    async def request_magic_link(self, email: str, ip_hash: str | None) -> MagicOutcome:
        return await self.magic.request(email, ip_hash)

    async def verify_magic_link(
        self, token: str, *, ip_hash: str | None, user_agent: str | None
    ) -> SignedIn | None:
        """Spend a link and sign its owner in; None when the link is unknown, used or expired."""
        user = await self.magic.verify(token)
        if user is None:
            return None
        return await self._signed_in(user, "/", ip_hash=ip_hash, user_agent=user_agent)

    # ───────────────────────── shared ─────────────────────────

    async def _signed_in(
        self, user: StoredUser, next_path: str, *, ip_hash: str | None, user_agent: str | None
    ) -> SignedIn:
        token, _session = await self.sessions.start(user.id, ip_hash=ip_hash, user_agent=user_agent)
        with context.bind(user_id=user.id):
            log.info("signed_in")
        return SignedIn(user=user, token=token, next_path=next_path)


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


__all__ = ["AuthService", "GoogleStart", "SignedIn"]
