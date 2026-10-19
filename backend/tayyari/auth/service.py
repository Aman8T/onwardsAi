"""`AuthService`: the one object the auth routes and the dependencies talk to.

It composes the parts (`google.GoogleOAuth`, `magic.MagicLinks`, `sessions.SessionService`) and keeps the rules
that cross them in one place:

- every sign-in path ends the same way: find or create the account, start a session, hand back the cookie token
  and the validated place to send the browser (`SignedIn`);
- `providers` says which paths this server offers (`Providers`: Google and the emailed link, which are the app's own and
  off unless ``TAYYARI_AUTH__OWN_SIGN_IN`` is set, and the Leafout account), so the web app hides what cannot work;
- `leafout_sign_in` turns the Leafout site's cookie into an app session (`auth.supabase` checks the token), and
  `review_leafout_session` decides what a session started that way does when the cookie is gone, changed or due a recheck;
- nothing here logs an email address, a token or a cookie value; the log context carries the account id only.

The routes own HTTP (redirects, cookies, status codes); this class owns the decisions.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Literal
from urllib.parse import quote, urlsplit

import httpx2

from tayyari.core import context
from tayyari.core.clock import Clock
from tayyari.core.hashing import hmac_hex
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings
from tayyari.storage.ports import EmailTaken, LimitRepo, StoredAuthSession, StoredUser, UserRepo

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
from .sessions import SessionContext, SessionService
from .supabase import SupabaseError, SupabaseVerifier

log = get_logger(__name__)

LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})
SUPABASE_SOURCE: Final = "supabase"
SUB_WINDOW_S: Final = 86_400

BridgeGate = Callable[[], Awaitable[bool]]
"""Asked once the token has passed every local check and Supabase is about to be called: False means "over the limit"."""
SignInIssue = Literal["email_taken"]
ReviewAction = Literal["keep", "slide", "end", "reauth", "swap"]


@dataclass(frozen=True, slots=True)
class Providers:
    """Which ways of signing in work on this server. ``google`` and ``magic`` are the app's own; ``leafout`` is the Leafout
    site's account (a cookie bridged by `AuthService.leafout_sign_in`)."""

    google: bool
    magic: bool
    leafout: bool


@dataclass(frozen=True, slots=True)
class SignedIn:
    """A completed sign-in: set ``token`` as the session cookie and send the browser to ``next_path``."""

    user: StoredUser
    token: str = field(repr=False)
    next_path: str = "/"
    session: StoredAuthSession | None = None


@dataclass(frozen=True, slots=True)
class BridgeResult:
    """What turning a Leafout cookie into a session did: the sign-in, or why not (``reason`` is for the log; ``issue`` is
    what `/api/me` may tell the person)."""

    signed_in: SignedIn | None = None
    issue: SignInIssue | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Review:
    """What to do with a live Leafout-made session, given the cookie that came with the request.

    ``keep``: nothing. ``slide``: the cookie vouched again, move ``verified_at``. ``end``: the cookie is gone or unusable, end
    the session (the request is anonymous). ``reauth``: the same, and the web app must send the person back through the
    site. ``swap``: someone else is signed in on the site in this browser; end this session and bridge the cookie."""

    action: ReviewAction
    reason: str | None = None


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
        self._limits = limits
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
        self.leafout: SupabaseVerifier | None = None
        auth = settings.auth
        if auth.leafout_enabled and auth.supabase_url and auth.supabase_publishable_key:
            self.leafout = SupabaseVerifier(
                url=auth.supabase_url,
                publishable_key=auth.supabase_publishable_key,
                mode=auth.supabase_verify,
                max_token_age_s=auth.supabase_max_token_age_s,
                http=self._http,
                clock=clock,
            )
            log.info("leafout_sign_in", enabled=True, verify=auth.supabase_verify)
            if not _shares_parent_domain(auth.public_url, auth.supabase_sign_in_url):
                log.warning(
                    "leafout_cookie_domain_mismatch",
                    fix="the app's public URL and the sign-in URL need a common parent domain for the cookie to reach the app",
                )
        self.google: GoogleOAuth | None = None
        client_id, client_secret = settings.google_client_id, settings.google_client_secret
        if auth.own_sign_in and client_id is not None and client_secret is not None:
            self.google = GoogleOAuth(
                client_id=client_id.get_secret_value(),
                client_secret=client_secret.get_secret_value(),
                redirect_uri=f"{settings.auth.public_url}{CALLBACK_PATH}",
                http=self._http,
                clock=clock,
                transport=transport,
            )

    @property
    def providers(self) -> Providers:
        """Which ways of signing in work on this server."""
        return Providers(
            google=self.google is not None, magic=self.magic.enabled, leafout=self.leafout is not None
        )

    @property
    def own_sign_in(self) -> bool:
        """Whether the app's own Google and emailed-link sign-in is offered (``TAYYARI_AUTH__OWN_SIGN_IN``)."""
        return self.settings.auth.own_sign_in

    def sign_in_url(self, next_path: str | None) -> str | None:
        """The Leafout site's sign-in page that returns to ``next_path`` on this app, or None when it is not configured.

        ``next_path`` goes through `safe_next` (a path on this site and nothing else), so the link can never carry the
        person to another host.
        """
        base = self.settings.auth.supabase_sign_in_url
        if self.leafout is None or not base:
            return None
        target = f"{self.settings.auth.public_url}{safe_next(next_path)}"
        return f"{base}?next={quote(target, safe='')}"

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

    # ───────────────────────── the Leafout account ─────────────────────────

    async def leafout_sign_in(
        self, token: str, *, ip_hash: str | None, user_agent: str | None, gate: BridgeGate | None = None
    ) -> SignedIn | None:
        """Turn the Leafout site's access token into an app session; None when it cannot be (see `leafout_bridge`)."""
        return (await self.leafout_bridge(token, ip_hash=ip_hash, user_agent=user_agent, gate=gate)).signed_in

    async def leafout_bridge(
        self, token: str, *, ip_hash: str | None, user_agent: str | None, gate: BridgeGate | None = None
    ) -> BridgeResult:
        """Check the token, ask Supabase whether its session is alive, find or create the account and start a session.

        ``gate`` is asked once the local checks pass and just before Supabase is called (the route counts the
        ``auth_bridge`` window there); a per-account daily counter bounds the sessions minted for one person. A refusal never
        raises: the caller treats the request as anonymous. The log records the outcome and a reason code, never the token,
        the cookie, the ``sub``, the email or the phone.
        """
        verifier = self.leafout
        if verifier is None:
            return BridgeResult(reason="not_configured")
        try:
            claims = await verifier.claims(token)
        except SupabaseError as err:
            return self._refused(err.reason)
        if gate is not None and not await gate():
            return self._refused("rate_limited")
        if not await self._within_sub_limit(claims.sub):
            return self._refused("sub_limit")
        try:
            identity = await verifier.confirm(token, claims)
        except SupabaseError as err:
            return self._refused(err.reason)
        now = self._clock.now()
        try:
            user = await self._users.sign_in_supabase(
                sub=identity.sub,
                email=identity.email,
                email_verified=identity.email_verified,
                name=identity.name,
                now=now,
            )
        except EmailTaken as taken:
            with context.bind(user_id=taken.user_id):
                log.warning("leafout_bridge", outcome="refused", reason="email_taken")
            return BridgeResult(issue="email_taken", reason="email_taken")
        signed = await self._signed_in(
            user,
            "/",
            ip_hash=ip_hash,
            user_agent=user_agent,
            source=SUPABASE_SOURCE,
            supabase_session_id=identity.session_id,
            verified_at=now,
        )
        with context.bind(user_id=user.id):
            log.info("leafout_bridge", outcome="signed_in")
        return BridgeResult(signed_in=signed)

    @staticmethod
    def _refused(reason: str) -> BridgeResult:
        log.info("leafout_bridge", outcome="refused", reason=reason)
        return BridgeResult(reason=reason)

    async def _within_sub_limit(self, sub: str) -> bool:
        limit = self.settings.guard.auth_bridge_per_sub_per_day
        if limit <= 0 or not self.settings.guard.enabled:
            return True
        key = hmac_hex(self._state_key, f"leafout-sub:{sub}", 32)
        try:
            count = await self._limits.hit(f"u:sb:{key}", SUB_WINDOW_S, self._clock.now())
        except Exception:  # the limiter failing must not lock people out
            log.warning("leafout_sub_limit_unavailable", exc_info=True)
            return True
        return count <= limit

    async def review_leafout_session(self, ctx: SessionContext, cookie: str | None) -> Review:
        """What a live session does given the request's ``leafout-access`` cookie (None when it has none).

        Only a session made from the site's cookie (``source == "supabase"``) is reviewed; the app's own sign-ins are left
        alone until they expire.

        - cookie absent: ``end`` (the site deletes it only on sign-out or account deletion, so this makes a site sign-out
          immediate);
        - malformed, or its signature or claims refused: also ``end`` (it counts as absent);
        - a genuine token for a different account (someone else signed in on the site): ``swap`` when the token is still
          current, else ``reauth``;
        - the same account and the recheck is due (``verified_at`` older than ``supabase_recheck_hours``): ``slide`` when the
          token is current, else ``reauth``; not due: ``keep``.

        Signature validity is checked even when the token has expired; only ``exp`` and the age limit are set aside for the
        identity comparison. When the key set cannot be reached the answer is ``keep``: an outage must not sign people out.
        """
        verifier = self.leafout
        if ctx.session.source != SUPABASE_SOURCE or verifier is None:
            return Review("keep")
        if not cookie:
            return self._reviewed("end", "absent")
        try:
            found = await verifier.inspect(cookie)
        except SupabaseError as err:
            if err.reason == "upstream":
                return Review("keep", "upstream")
            return self._reviewed("end", err.reason)
        current = found.stale is None
        if ctx.user.supabase_sub is None or found.claims.sub != ctx.user.supabase_sub:
            if current:
                log.info("supabase_sub_switched")
                return Review("swap")
            return self._reviewed("reauth", found.stale)
        verified_at = ctx.session.verified_at
        window = timedelta(hours=self.settings.auth.supabase_recheck_hours)
        if verified_at is not None and self._clock.now() - verified_at < window:
            return Review("keep")
        if current:
            log.info("leafout_recheck", outcome="slid")
            return Review("slide")
        return self._reviewed("reauth", found.stale)

    @staticmethod
    def _reviewed(action: ReviewAction, reason: str | None) -> Review:
        log.info("leafout_recheck", outcome=action, reason=reason)
        return Review(action, reason)

    async def end_session(self, token: str | None) -> None:
        """End the session behind a cookie value (see `SessionService.end`)."""
        await self.sessions.end(token)

    async def slide_verified(self, session: StoredAuthSession) -> StoredAuthSession:
        """Record that the site's cookie vouched for ``session`` again."""
        return await self.sessions.mark_verified(session)

    # ───────────────────────── shared ─────────────────────────

    async def _signed_in(
        self,
        user: StoredUser,
        next_path: str,
        *,
        ip_hash: str | None,
        user_agent: str | None,
        source: str | None = None,
        supabase_session_id: str | None = None,
        verified_at: datetime | None = None,
    ) -> SignedIn:
        token, session = await self.sessions.start(
            user.id,
            ip_hash=ip_hash,
            user_agent=user_agent,
            source=source,
            supabase_session_id=supabase_session_id,
            verified_at=verified_at,
        )
        with context.bind(user_id=user.id):
            log.info("signed_in")
        return SignedIn(user=user, token=token, next_path=next_path, session=session)


def _shares_parent_domain(public_url: str, sign_in_url: str | None) -> bool:
    """Whether the app and the site's sign-in page can share a parent-domain cookie: the same last two host labels."""
    if not sign_in_url:
        return True
    hosts = [urlsplit(url).hostname or "" for url in (public_url, sign_in_url)]
    if any("." not in h for h in hosts):
        return False
    first, second = (".".join(h.split(".")[-2:]) for h in hosts)
    return first == second


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


__all__ = [
    "AuthService",
    "BridgeGate",
    "BridgeResult",
    "GoogleStart",
    "Providers",
    "Review",
    "SignedIn",
]
