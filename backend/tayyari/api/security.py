"""Browser-facing protections, as two pure ASGI middlewares (neither buffers or wraps a streaming body).

`CrossSiteGuard`
    The API has no cookies or login, so the harm of a cross-site request is cost: any web page could make
    its visitors' browsers start analyses (``sample=1`` runs a full live one) or chat turns on our bill and
    against the visitors' own limits. POST, PUT, PATCH and DELETE to ``/api`` are therefore refused with a
    403 JSON ``{"error": ...}`` when

    - ``Sec-Fetch-Site`` is ``cross-site`` (the browser says so, and a page cannot change it), or
    - an ``Origin`` header is present and its host is not the host the request was sent to.

    A request with neither header (curl, a server-to-server call, the tests) passes: this guards against
    browsers, not against scripts. ``Sec-Fetch-Site: same-origin`` also passes without the host comparison,
    because only our own pages can produce it, and it keeps the site working if a proxy rewrites ``Host``.
    The host is compared with ``Host`` and with ``X-Forwarded-Host`` (a page cannot set that header
    without a CORS preflight, which the app never answers). Default ports (80, 443) are ignored.

    The payment webhooks (``/api/billing/webhooks/``) are exempt: they are called by Razorpay's and Stripe's servers,
    carry no cookie, and authenticate with a signature over the raw body that the route checks first. A browser
    that posts there from another site gets nothing but a refused signature.

`SecurityHeadersMiddleware`
    Adds the standard hardening headers to every response (``X-Content-Type-Options``, ``Referrer-Policy``,
    ``X-Frame-Options``, ``Permissions-Policy``, and ``Strict-Transport-Security`` when the request came in
    over https, directly or as ``X-Forwarded-Proto``) and a ``Content-Security-Policy``:

    - HTML pages get the full policy: same-origin everything, Google Fonts for CSS and fonts, no plugins,
      no framing, and ``script-src 'self'`` plus a ``'sha256-...'`` source for every inline ``<script>`` of
      ``static/index.html`` (read when the app starts, and again if the file changes). Razorpay's Checkout.js is the
      one third-party script (see `RAZORPAY_CHECKOUT`); the pricing page is the only page that loads it. The chat view renders
      model-written Markdown, so ``img-src`` and ``connect-src`` are what keep an injected image or link from
      sending a conversation to another site.
    - Every other response (the JSON and NDJSON answers, assets) gets ``default-src 'none'; frame-ancestors
      'none'``: nothing that is not a page may load or run anything.

    A header a route already set is left as it is.

`subnet_hash` is the third small piece of client identity the routes need: the keyed hash of an IPv6 client's /48
network, which the routes pass to the rate limiter next to ``ip_hash``.

Install both with ``app.add_middleware`` in `create_app`: request id outermost, then the headers, then the guard.
(An unhandled exception is answered by Starlette's outermost error middleware, so that 500 carries neither.)
"""

from __future__ import annotations

import base64
import hashlib
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tayyari.core.hashing import hmac_hex
from tayyari.core.logging import get_logger
from tayyari.guard.client_ip import ip_subjects

log = get_logger(__name__)

CROSS_SITE_MESSAGE = "Cross-site requests are not allowed."
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
WEBHOOK_PREFIX = "/api/billing/webhooks/"
"""Server-to-server callbacks of the payment providers: signed, cookie-less, never from a browser page."""
_DEFAULT_PORTS = frozenset({80, 443})

# ───────────────────────── the client's network, for the limiter ─────────────────────────


def subnet_hash(key: bytes, client_ip: str | None) -> str | None:
    """The keyed hash of an IPv6 client's /48 network (what `RateLimiter` takes as ``ip_hash_48``), else None.

    A /64 is one household, but a client can hop between the 65,536 /64s of its /48 for free; the limiter
    counts the whole /48 as well, at a higher limit. IPv4 clients and addresses that are not IPs have no wider
    network, so the limiter is called without it.
    """
    if client_ip is None:
        return None
    subjects = ip_subjects(client_ip)
    return hmac_hex(key, subjects[1]) if len(subjects) > 1 else None


# ───────────────────────── cross-site guard ─────────────────────────


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _normalise_host(hostname: str | None, port: int | None) -> str | None:
    """``host`` or ``host:port`` in lower case, without a default port; None when there is no host."""
    if not hostname:
        return None
    host = hostname.lower()
    return host if port is None or port in _DEFAULT_PORTS else f"{host}:{port}"


def origin_host(origin: str) -> str | None:
    """The host of an ``Origin`` header value, or None when it is not an http(s) origin (``null``, ``file://``...)."""
    try:
        parts = urlsplit(origin.strip())
        if parts.scheme not in ("http", "https"):
            return None
        return _normalise_host(parts.hostname, parts.port)
    except ValueError:
        return None


def header_host(value: str) -> str | None:
    """The host of a ``Host`` or ``X-Forwarded-Host`` value (``example.com``, ``localhost:8000``, ``[::1]:80``)."""
    try:
        parts = urlsplit("//" + value.strip())
        return _normalise_host(parts.hostname, parts.port)
    except ValueError:
        return None


def cross_site_reason(headers: Headers) -> str | None:
    """Why a state-changing request must be refused (``"sec-fetch-site"`` or ``"origin"``), or None to let it in."""
    site = (headers.get("sec-fetch-site") or "").strip().lower()
    if site == "cross-site":
        return "sec-fetch-site"
    origin = headers.get("origin")
    if origin is None or site == "same-origin":
        return None
    wanted = origin_host(origin)
    if wanted is None:
        return "origin"
    served = [headers.get("host"), *(headers.get("x-forwarded-host") or "").split(",")]
    if any(value is not None and header_host(value) == wanted for value in served if value):
        return None
    return "origin"


class CrossSiteGuard:
    """Pure ASGI middleware: 403 JSON for a cross-site state-changing request to ``/api`` (see the module docstring)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] in UNSAFE_METHODS
            and _is_api_path(scope["path"])
            and not scope["path"].startswith(WEBHOOK_PREFIX)
        ):
            reason = cross_site_reason(Headers(scope=scope))
            if reason is not None:
                log.info("cross_site_refused", reason=reason, method=scope["method"], path=scope["path"])
                await JSONResponse({"error": CROSS_SITE_MESSAGE}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


# ───────────────────────── security headers ─────────────────────────

RAZORPAY_CHECKOUT = "https://checkout.razorpay.com"
"""Razorpay's Checkout.js and the modal frame it opens. Allowed for scripts and frames on every page because the
policy is one string for the whole app; only the pricing page ever loads the script (it inserts the tag on mount and
removes it on unmount). The modal also talks to Razorpay's API and its event collector, hence the two connect hosts."""
RAZORPAY_CONNECT = "https://api.razorpay.com https://lumberjack.razorpay.com"
RAZORPAY_FRAME = "https://checkout.razorpay.com https://api.razorpay.com"
"""Frames: Checkout.js opens its modal as an iframe served from api.razorpay.com (documented by Razorpay next to
checkout.razorpay.com), so the page needs both."""
PAGE_CSP_TEMPLATE = (
    "default-src 'self'; "
    "script-src {script_src}; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data:; "
    "connect-src 'self' {razorpay_connect}; "
    "frame-src 'self' {razorpay_frame}; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
API_CSP = "default-src 'none'; frame-ancestors 'none'"
HSTS = "max-age=31536000"
_FIXED_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Permissions-Policy", "camera=(), microphone=(self), geolocation=()"),
)


_SCRIPT_TYPES: frozenset[str] = frozenset(["", "text/javascript", "application/javascript", "module"])
"""``type`` values a browser runs. Anything else (``application/ld+json`` structured data) is a data block: never run, so a
CSP needs no hash for it."""


class _InlineScripts(HTMLParser):
    """Collects the text of every ``<script>`` element that has no ``src`` and is run by the browser (the ones a CSP must
    allow by hash); data blocks such as JSON-LD are skipped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.scripts: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            has_src = any(name == "src" for name, _ in attrs)
            kind = next(((value or "").strip().lower() for name, value in attrs if name == "type"), "")
            self._current = None if has_src or kind not in _SCRIPT_TYPES else []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._current is not None:
            self.scripts.append("".join(self._current))
            self._current = None


def inline_script_hashes(html: str) -> tuple[str, ...]:
    """CSP source expressions (``'sha256-...'``) for every inline ``<script>`` in ``html``, in document order.

    A browser hashes the script text after its parser has turned CRLF and CR into LF, so that is done first.
    """
    parser = _InlineScripts()
    parser.feed(html.replace("\r\n", "\n").replace("\r", "\n"))
    parser.close()
    out: list[str] = []
    for text in parser.scripts:
        digest = base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")
        source = f"'sha256-{digest}'"
        if source not in out:
            out.append(source)
    return tuple(out)


def page_csp(script_hashes: tuple[str, ...] = ()) -> str:
    """The Content-Security-Policy for HTML pages, allowing the given inline-script hashes."""
    return PAGE_CSP_TEMPLATE.format(
        script_src=" ".join(("'self'", RAZORPAY_CHECKOUT, *script_hashes)),
        razorpay_connect=RAZORPAY_CONNECT,
        razorpay_frame=RAZORPAY_FRAME,
    )


class PageCsp:
    """The page policy, with the script hashes read from ``index_html``.

    The file is read when this object is made (the app starting up). It is looked at again, with a ``stat``,
    at most every ``recheck_s`` seconds when a page is served, so a rebuilt ``index.html`` (the static folder
    is served without a restart) is followed instead of being blocked by a stale hash. A missing or unreadable
    file means ``script-src 'self'`` only.
    """

    def __init__(self, index_html: Path | None, *, recheck_s: float = 2.0) -> None:
        self._path = index_html
        self._recheck_s = recheck_s
        self._stamp: tuple[int, int] | None = None
        self._checked_at = 0.0
        self._policy = page_csp()
        self._reload()

    @property
    def policy(self) -> str:
        """The current policy string."""
        if self._path is not None and time.monotonic() - self._checked_at >= self._recheck_s:
            self._reload()
        return self._policy

    def _reload(self) -> None:
        self._checked_at = time.monotonic()
        path = self._path
        if path is None:
            return
        try:
            info = path.stat()
            stamp: tuple[int, int] | None = (info.st_mtime_ns, info.st_size)
        except OSError:
            stamp = None
        if stamp is not None and stamp == self._stamp:
            return
        self._stamp = stamp
        hashes: tuple[str, ...] = ()
        if stamp is not None:
            try:
                hashes = inline_script_hashes(path.read_text(encoding="utf-8-sig", errors="replace"))
            except OSError:
                log.warning("csp_index_unreadable")
        self._policy = page_csp(hashes)
        log.info("csp_ready", inline_scripts=len(hashes))


def _is_https(scope: Scope, headers: Headers) -> bool:
    if scope.get("scheme") == "https":
        return True
    forwarded = headers.get("x-forwarded-proto") or ""
    return any(part.strip().lower() == "https" for part in forwarded.split(","))


class SecurityHeadersMiddleware:
    """Pure ASGI middleware adding the hardening headers and a Content-Security-Policy to every response.

    ``index_html`` is the built web app's entry page, where the inline-script hashes come from. It only touches
    the response *start* message, so streaming bodies pass through untouched.
    """

    def __init__(self, app: ASGIApp, *, index_html: Path | None = None) -> None:
        self.app = app
        self.csp = PageCsp(index_html)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        https = _is_https(scope, Headers(scope=scope))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                for name, value in _FIXED_HEADERS:
                    headers.setdefault(name, value)
                if https:
                    headers.setdefault("Strict-Transport-Security", HSTS)
                is_page = headers.get("content-type", "").lower().startswith("text/html")
                headers.setdefault("Content-Security-Policy", self.csp.policy if is_page else API_CSP)
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = [
    "API_CSP",
    "CROSS_SITE_MESSAGE",
    "HSTS",
    "RAZORPAY_CHECKOUT",
    "RAZORPAY_CONNECT",
    "RAZORPAY_FRAME",
    "UNSAFE_METHODS",
    "WEBHOOK_PREFIX",
    "CrossSiteGuard",
    "PageCsp",
    "SecurityHeadersMiddleware",
    "cross_site_reason",
    "header_host",
    "inline_script_hashes",
    "origin_host",
    "page_csp",
    "subnet_hash",
]
