"""The sign-in gate: with it on, nothing under ``/api/`` works without a signed-in account (a pure ASGI middleware).

`SignInGate` answers ``401 {"error": "Sign in to continue."}`` (the body `core.errors.Unauthorized` makes everywhere else) to
every ``/api/*`` request that has no signed-in user, before the route runs. It is default-deny: a route added tomorrow is
covered without anyone remembering to protect it, and the one place that says what is public is `PUBLIC_API` below.

Whether it is on is `Settings.sign_in_required` (``TAYYARI_AUTH__REQUIRE_SIGN_IN``; unset means on in production and off
everywhere else), decided once by `create_app`. Off, the middleware is a pass-through and the app behaves as it always did.

Public on purpose, signed out (everything else under ``/api/`` is a 401):

    GET  /api/health, /api/health/deep      liveness probes (HEAD as well as GET on the first)
    any  /api/auth/...                      the sign-in and sign-out routes themselves
    GET  /api/me                            how the web app finds out it must show sign-in (it answers visitors)
    POST /api/billing/webhooks/razorpay, /stripe   server-to-server, signed; they never carry a cookie
    GET  /api/billing/entitlements          read-only price list for the public pricing page
    GET  /api/p/{public_id}                 a portfolio its owner chose to publish
    GET, POST /api/weekly/unsubscribe       the email link carries its own token
    OPTIONS (any /api path)                 a CORS preflight carries no credentials

Paths outside ``/api/`` are not this middleware's business: the static pages, the web app's shell, robots and sitemap, and the
public card routes (``/u/<slug>``, ``/r/<slug>.svg``, ``/og/...``) are served as before.

How it decides. For a gated request it calls `api.deps.get_auth_context`, the same lookup the routes use, so the answer is
cached on the request (the route does not look the session up a second time) and the Leafout cookie bridge still runs, which is
how a person arriving from the site gets a session before their first call. Anything but a live sign-in is a 401. The middleware
sits inside `SessionCookieMiddleware` so a session the lookup just made, or ended, still reaches the browser's cookie on the 401
or on the answer that follows. A refusal is logged like any other 401 (``app_error``, code and status), with no body and no
cookies, and no path: a hand-off's id is a secret in the path of its URL.

Order, outermost first (`create_app`): request id, security headers, cross-site guard, session cookie, **this gate**, the
routes. A cross-site browser write is therefore refused (403) before the gate looks at it, and every refusal here carries the
request id and the security headers.
"""

from __future__ import annotations

from typing import Final

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from tayyari.api.deps import get_auth_context
from tayyari.core.errors import AppError, Unauthorized
from tayyari.core.logging import get_logger

log = get_logger(__name__)

READ: Final = frozenset({"GET", "HEAD"})
PUBLIC_EXACT: Final[dict[str, frozenset[str]]] = {
    "/api/health": READ,
    "/api/health/deep": READ,
    "/api/me": READ,
    "/api/billing/entitlements": READ,
    "/api/billing/webhooks/razorpay": frozenset({"POST"}),
    "/api/billing/webhooks/stripe": frozenset({"POST"}),
    "/api/weekly/unsubscribe": frozenset({"GET", "HEAD", "POST"}),
}
"""Exact paths that are public, with the methods that are. Any other method on them is a 401 like the rest."""
PUBLIC_API: Final = (
    "any /api/auth/...",
    *(f"{', '.join(sorted(methods))} {path}" for path, methods in sorted(PUBLIC_EXACT.items())),
    "GET, HEAD /api/p/{public_id}",
    "OPTIONS any",
)
"""The public allowlist in words (the module docstring has it with the reasons)."""
_AUTH_PREFIX: Final = "/api/auth/"
_PORTFOLIO_PREFIX: Final = "/api/p/"


def route_path(scope: Scope) -> str:
    """The path the router matches on: ``scope["path"]`` without ``root_path`` (Starlette's own rule)."""
    path = str(scope.get("path", ""))
    root = str(scope.get("root_path", ""))
    if root and path.startswith(root):
        if path == root:
            return ""
        if path[len(root)] == "/":
            return path[len(root) :]
    return path


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _plain(path: str) -> bool:
    """False for a path with an empty, ``.`` or ``..`` segment after ``/api``: it is never public, whatever it starts with."""
    return all(part not in ("", ".", "..") for part in path.split("/")[1:])


def is_public(method: str, path: str) -> bool:
    """Whether a request may pass without a sign-in (see the module docstring). ``path`` is `route_path`'s answer."""
    if method == "OPTIONS":
        return True
    if not _plain(path):
        return False
    if path.startswith(_AUTH_PREFIX):
        return True
    if method in READ and path.startswith(_PORTFOLIO_PREFIX) and path.count("/") == 3:
        return True
    methods = PUBLIC_EXACT.get(path)
    return methods is not None and method in methods


class SignInGate:
    """Pure ASGI middleware: 401 for an ``/api/*`` request with no signed-in user, unless it is public (see the module docstring).

    ``enabled`` False makes it a pass-through. It touches no response of its own except the refusal, so streaming bodies
    pass through untouched.
    """

    def __init__(self, app: ASGIApp, *, enabled: bool = True) -> None:
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = route_path(scope)
        raw = str(scope.get("path", ""))
        # Both spellings count as the API: a path that only looks like /api once a prefix is stripped must still be gated.
        if not (is_api_path(path) or is_api_path(raw)) or is_public(str(scope["method"]), path):
            await self.app(scope, receive, send)
            return
        refusal: AppError | None
        try:
            signed_in = await get_auth_context(Request(scope, receive))
        except (
            AppError
        ) as exc:  # the container is not ready yet (503): the middleware is outside the error handlers
            refusal = exc
        else:
            refusal = None if signed_in is not None else Unauthorized()
        if refusal is None:
            await self.app(scope, receive, send)
            return
        log.info("app_error", code=refusal.code, status=refusal.status, detail=refusal.detail, gate="sign_in")
        retry_after = refusal.retry_after_header()
        headers = {} if retry_after is None else {"Retry-After": retry_after}
        await JSONResponse(refusal.to_body(), status_code=refusal.status, headers=headers)(
            scope, receive, send
        )


__all__ = ["PUBLIC_API", "SignInGate", "is_public", "route_path"]
