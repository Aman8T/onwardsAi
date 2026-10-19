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

Who is signed in, for routes that need an account:

    get_auth_context(request)   the live sign-in behind the ``tayyari_session`` cookie (`auth.SessionContext`), or
                                None. Looked up once per request; binds ``user_id`` (never the email) into the log
                                context. A route that wants the sliding cookie refreshed reads ``.refreshed``. It is also
                                where the Leafout site's cookie (``leafout-access``) is looked at, on ``/api/`` paths
                                that are not webhooks and when Leafout sign-in is configured:
                                  - no session and a cookie: the bridge (`AuthService.leafout_bridge`) starts one; the
                                    cookie reaches the browser through `api.session_cookie.SessionCookieMiddleware`;
                                  - a session made that way and the cookie gone, unusable or someone else's, or its
                                    24-hour recheck due: `AuthService.review_leafout_session` decides (keep, slide, end,
                                    reauth or swap), see its docstring and the amendments in docs/roadmap.md, section L1;
                                  - a refused bridge is an anonymous request, never a 401 of its own.
    suppress_bridge(request)    for sign-out: this request never bridges the Leafout cookie into a session
    leafout_flags(request)      ``(reauth, sign_in_issue)`` for `GET /api/me` after `get_auth_context` ran: the app just ended
                                a Leafout-made session whose site sign-in must be refreshed, and/or a bridge was refused
                                because the verified email belongs to an app account linked to another Leafout sign-in.
    get_current_user(request)   the signed-in account (`StoredUser`), or None for an anonymous visitor
    require_user(request)       the signed-in account, or a 401 `Unauthorized` that the web app turns into the
                                sign-in sheet
    user_or_anonymous(request, anonymous_allowed=...)
                                the signed-in account, or None for a visitor with no sign-in when the route may
                                be used without one (`Container.match_anonymous`), else the same 401

Analysis and chat stay open to anonymous visitors and do not call these; routes that persist for a person do.

Who has no limits, for every route that rate-limits or charges:

    unlimited(request, container)
                                True when the signed-in account's email is on the unlimited list
                                (``TAYYARI_GUARD__UNLIMITED_EMAILS``, `GuardSettings.is_unlimited`). A route resolves it
                                first and passes it as ``exempt=`` to `RateLimiter.shed` / `check` (and skips the
                                entitlement charge). With no list, or no session cookie, it answers False without any lookup,
                                so anonymous traffic costs what it did before; otherwise it reuses `get_auth_context`'s
                                once-per-request session lookup, and the answer is kept on the request. A lookup that fails
                                counts as "not unlimited" (the limits apply), never as an error of its own.

Whose profile it is, for every route that takes a profile id:

    require_profile_access(request, profile_id, not_found=...)
                                a profile nobody has claimed (``profiles.user_id`` NULL) is served to whoever holds its
                                id, as before; so is a sample run. A claimed profile is served only to the session of the
                                account that claimed it, and to anyone else (anonymous, or another account) the answer
                                is the 404 the route gives an unknown id: same status, same body, never a 403. An id
                                that no stored profile has (the bundled sample persona before it is seeded, or a
                                profile that does not exist) passes through to the route's own handling of it.
    owner_required(access)      the rule itself, without the request: the one account id allowed in, or None for anyone
"""

from __future__ import annotations

import hmac
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import HTTPConnection, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tayyari.api.lifespan import get_container
from tayyari.api.security import subnet_hash
from tayyari.api.session_cookie import forget_session, remember_session
from tayyari.auth.sessions import BRIDGE_HOLD_COOKIE, SessionContext, bridge_hold_value
from tayyari.contract.requests import RequestContext
from tayyari.core import context
from tayyari.core.clock import Clock
from tayyari.core.errors import NotFound, RateLimited, Unauthorized
from tayyari.core.hashing import hmac_hex
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings, get_settings
from tayyari.guard.client_ip import client_ip, ip_subject
from tayyari.storage.ports import ProfileAccess
from tayyari.storage.ports import StoredUser as User

if TYPE_CHECKING:
    from tayyari.container import Container

log = get_logger(__name__)

PROFILE_NOT_FOUND: Final = "Profile not found"
"""The sentence of the 404 for a profile the caller may not open, unless the route passes the one it uses."""
REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_MAX_CHARS = 64
_REQUEST_ID_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_CONTEXT_KEY = "request_context"
_AUTH_KEY = "auth_context"
_UNLIMITED_KEY = "unlimited"
_REAUTH_KEY = "leafout_reauth"
_ISSUE_KEY = "leafout_sign_in_issue"
_NO_BRIDGE_KEY = "leafout_no_bridge"
_WEBHOOK_PREFIX = "/api/billing/webhooks/"
_UNSET: Any = object()
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
        with context.bind(request_id=rid, ip_hash=None, user_id=None):
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


def _held(request: Request, cookie: str) -> bool:
    """True when this browser just signed out or deleted its account and still sends that same old site token."""
    hold = request.cookies.get(BRIDGE_HOLD_COOKIE)
    return hold is not None and hmac.compare_digest(hold, bridge_hold_value(cookie))


def _bridge_applies(request: Request, container: Container) -> bool:
    """Whether this request may look at the Leafout cookie: configured, an ``/api/`` path, not a payment webhook."""
    path = request.url.path
    return (
        container.auth.leafout is not None
        and path.startswith("/api/")
        and not path.startswith(_WEBHOOK_PREFIX)
    )


async def _bridge(
    request: Request, container: Container, cookie: str, state: dict[str, Any]
) -> SessionContext | None:
    """Turn the Leafout cookie into a session for this request. None when it cannot be (the request is anonymous)."""
    rc = await get_request_context(request)

    async def gate() -> bool:
        try:
            await container.rate_limiter.check(
                "auth_bridge", rc.ip_hash, subnet_hash(container.ip_hash_key, rc.client_ip)
            )
        except RateLimited:
            return False
        return True

    result = await container.auth.leafout_bridge(
        cookie, ip_hash=rc.ip_hash, user_agent=request.headers.get("user-agent"), gate=gate
    )
    if result.issue is not None:
        state[_ISSUE_KEY] = result.issue
    signed = result.signed_in
    if signed is None or signed.session is None:
        return None
    remember_session(request.scope, signed.token)
    return SessionContext(user=signed.user, session=signed.session, token=signed.token, refreshed=False)


async def _resolve_auth(
    request: Request, container: Container, state: dict[str, Any]
) -> SessionContext | None:
    auth = container.auth
    settings = container.settings
    found = await auth.sessions.resolve(request.cookies.get(settings.auth.cookie_name))
    if state.get(_NO_BRIDGE_KEY) is True or not _bridge_applies(request, container):
        return found
    cookie = request.cookies.get(settings.auth.supabase_cookie)
    if found is None:
        if not cookie or _held(request, cookie):
            return None
        return await _bridge(request, container, cookie, state)
    review = await auth.review_leafout_session(found, cookie)
    if review.action == "keep":
        return found
    if review.action == "slide":
        return SessionContext(
            user=found.user,
            session=await auth.slide_verified(found.session),
            token=found.token,
            refreshed=found.refreshed,
        )
    # end, reauth and swap all end this session; the browser's copy of the cookie goes with it.
    await auth.end_session(found.token)
    forget_session(request.scope)
    if review.action == "swap" and cookie:
        return await _bridge(request, container, cookie, state)
    if review.action == "reauth":
        state[_REAUTH_KEY] = True
    return None


async def get_auth_context(request: Request) -> SessionContext | None:
    """The live sign-in behind the session cookie, or None (no cookie, unknown, expired). Resolved once per request.

    With Leafout sign-in configured this is also where the site's cookie is bridged and reviewed (see the module docstring).
    """
    state = _state(request.scope)
    cached = state.get(_AUTH_KEY, _UNSET)
    if cached is not _UNSET:
        found: SessionContext | None = cached
        return found
    container = get_container(request)
    resolved = await _resolve_auth(request, container, state)
    state[_AUTH_KEY] = resolved
    if resolved is not None:
        context.user_id.set(resolved.user.id)
    return resolved


def suppress_bridge(request: Request) -> None:
    """Never turn the Leafout cookie into a session on this request (sign-out must not sign anyone in on its way)."""
    _state(request.scope)[_NO_BRIDGE_KEY] = True


def leafout_flags(request: Request) -> tuple[bool, str | None]:
    """``(reauth, sign_in_issue)`` noted while this request's session was decided (see the module docstring)."""
    state = _state(request.scope)
    issue = state.get(_ISSUE_KEY)
    return state.get(_REAUTH_KEY) is True, issue if isinstance(issue, str) else None


async def get_current_user(request: Request) -> User | None:
    """The signed-in account, or None for an anonymous visitor."""
    resolved = await get_auth_context(request)
    return None if resolved is None else resolved.user


async def unlimited(request: Request, container: Container) -> bool:
    """Whether the signed-in account is on the unlimited list: no rate limits, and nothing charged (see the module docstring).

    Answers False with no lookup when the list is empty or the request carries no session cookie. Otherwise the session is
    resolved through `get_auth_context` (cached for the request, so a route that looks the user up later pays nothing more),
    and the answer is cached on the request too, so asking twice is free.
    """
    state = _state(request.scope)
    cached = state.get(_UNLIMITED_KEY)
    if isinstance(cached, bool):
        return cached
    settings = container.settings
    answer = False
    has_cookie = request.cookies.get(settings.auth.cookie_name) or (
        settings.auth.leafout_enabled and request.cookies.get(settings.auth.supabase_cookie)
    )
    if settings.guard.has_unlimited and has_cookie:
        try:
            user = await get_current_user(request)
        except Exception:  # not worth failing the request: the limits apply to anyone we cannot name
            log.warning("unlimited_lookup_failed", exc_info=True)
        else:
            answer = user is not None and settings.guard.is_unlimited(user.email)
    state[_UNLIMITED_KEY] = answer
    return answer


async def require_user(request: Request) -> User:
    """The signed-in account; a 401 (`Unauthorized`) when there is none."""
    user = await get_current_user(request)
    if user is None:
        raise Unauthorized()
    return user


async def user_or_anonymous(request: Request, *, anonymous_allowed: bool) -> User | None:
    """The signed-in account; None for a visitor with no sign-in when ``anonymous_allowed``, else a 401 like
    `require_user`. A cookie that names no live sign-in (garbage, expired) is a visitor with no sign-in."""
    user = await get_current_user(request)
    if user is None and not anonymous_allowed:
        raise Unauthorized()
    return user


def owner_required(access: ProfileAccess | None) -> str | None:
    """The id of the only account that may open a profile with this ``access``, or None when anyone holding the
    profile id may: a profile nobody has claimed, a sample run, or one the database does not have."""
    if access is None or access.is_sample:
        return None
    return access.user_id


async def require_profile_access(
    request: Request, profile_id: str, *, not_found: str = PROFILE_NOT_FOUND
) -> None:
    """Let the request through when it may open ``profile_id``; otherwise raise `NotFound` (``not_found`` is the
    sentence the route gives an unknown id, so the two cannot be told apart). See `owner_required` for the rule.

    The session is looked up only for a claimed profile, so anonymous use of the open ones costs one indexed
    lookup of two columns and nothing more. Call it right after the id shape is checked, before the body is read
    and before any rate-limit counter or spend reservation is touched.
    """
    access = await get_container(request).repos.profiles.get_access(profile_id)
    required = owner_required(access)
    if required is None:
        return
    user = await get_current_user(request)
    if user is None or user.id != required:
        raise NotFound(not_found)


__all__ = [
    "PROFILE_NOT_FOUND",
    "REQUEST_ID_HEADER",
    "REQUEST_ID_MAX_CHARS",
    "RequestIdMiddleware",
    "User",
    "build_request_context",
    "get_auth_context",
    "get_current_user",
    "get_request_context",
    "leafout_flags",
    "new_request_id",
    "owner_required",
    "require_profile_access",
    "require_user",
    "sanitize_request_id",
    "suppress_bridge",
    "unlimited",
    "user_or_anonymous",
]
