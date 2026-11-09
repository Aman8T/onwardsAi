"""Serve the built web app (``backend/static``): files, content pages, the app's own routes and the 404 page.

`mount_spa(app, static_dir)` adds two catch-all routes:

    /api and /api/...   a JSON 404 ``{"error": "Not found"}`` for every method (an unknown API route must
                        never answer with an HTML page)
    /<anything>         GET and HEAD, answered by `locate`, first match wins:
                        1. a real file under ``static_dir``: a file under ``assets/`` (content-hashed by the build) is
                           cached for a year and ``immutable``, any other file for an hour (``public, max-age=3600``); an
                           ``.html`` file is ``no-cache`` instead. ``index.html`` itself, at any level, is a 301 to its
                           folder (``/index.html`` to ``/``): one address per page
                        2. a page, ``<path>/index.html`` (the generated content pages, and /pricing when the build
                           has its own): 200, ``no-cache``. With a trailing slash it is a 308 to the address
                           without one (``/how-it-works/`` to ``/how-it-works``)
                        3. one of the app's own client routes (`APP_ROUTES`): index.html, 200, ``no-cache``, so a
                           deep link opens the app
                        4. anything else: ``404.html`` with status 404 and ``no-cache``; the JSON 404 when the
                           build has none. ``/404.html`` itself is a 404 too.
                        The private routes (match, discover, practice, signout, account/delete, the published portfolios under /p/,
                        the dev galleries) send ``X-Robots-Tag: noindex`` and their shell has no canonical link, no
                        structured data and no ``og:url`` (`private_shell`), so none of them points search engines at Home;
                        "/", "/pricing" and the content pages do not. The built ``pricing/index.html`` may hold the
                        placeholders ``{{price:pack3}}`` and ``{{price:pass30}}``, which `fill_prices` replaces with the
                        configured rupee price (or "Coming soon") on every request.

With ``TAYYARI_AUTH__REDIRECT_OTHER_HOSTS`` on, a GET or HEAD for any of these on a host other than the public URL's is a 308 to the
same path and query on the public URL (the old address keeps working as a pointer; ``/api/*`` is never redirected, so scripts
that call the API there keep working).

With no web build (no index.html under ``static_dir``) every GET and HEAD is the JSON 404. A path that is not a plain
relative address (a leading slash, a backslash, a NUL, a ``..`` that leaves the folder) is never a file: it is item 4.
The ``dev/`` routes are served only when ``app.state.settings`` (set by `create_app`) is not prod. The files are
looked up on every request, so a build that appears later is served without a restart.

ORDER MATTERS: Starlette matches routes in the order they were registered, and these two match every path.
Call `mount_spa` after every other route (``include_router``, ``@app.get`` ...) has been added, or it
shadows them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote, urlsplit

import anyio.to_thread
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from tayyari.api.security import header_host
from tayyari.billing.products import PRODUCTS, format_price, on_sale
from tayyari.core.settings import Settings

NOT_FOUND_MESSAGE = "Not found"
INDEX_PAGE = "index.html"
NOT_FOUND_PAGE = "404.html"
INDEX_CACHE_CONTROL = "no-cache"
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
"""Files under ``assets/``: the build names them by content hash, so a changed file has a new address."""
FILE_CACHE_CONTROL = "public, max-age=3600"
"""Every other real file (favicon, robots.txt, sitemap.xml, the content pages' stylesheet): the name does not change."""
ASSETS_DIR = "assets/"
NOINDEX = "noindex"
_API_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


class AppRoute(NamedTuple):
    """A client route of the app. ``pattern`` is a regular expression that must match the whole path."""

    pattern: str
    private: bool = False
    dev_only: bool = False


# The app's own client routes, each answered with index.html. Keep this in step with where they are defined:
#   ""                 the home screen                          src/store.ts
#   pricing            /pricing (a build with pricing/index.html serves that page instead)  src/billing/route.ts
#   match...           /match, /match/<id>, .../resume, .../cover                          src/match/route.ts
#   discover...        /discover (its filters are in the query string)                     src/discover/route.ts
#   practice...        /practice                                                           src/practice/route.ts
#   signout            /signout, ends the app session, then returns to the Leafout site    src/auth/SignOut.tsx
#   account/delete     /account/delete, deletion started on the Leafout site (confirm first)  src/auth/
#   weekly/...         /weekly, The Leafout Weekly (the reading room, Manage and the archive)    src/weekly/
#   p/...              /p/<publicId>, the published portfolio                              src/main.tsx
#   dev/...            the galleries /dev/portfolios, /dev/match, /dev/discover, /dev/practice, /dev/prepare:
#                      rendered by development builds only, so served only on a non-prod server
APP_ROUTES: tuple[AppRoute, ...] = (
    AppRoute(""),
    AppRoute(r"dashboard/?", private=True),
    AppRoute(r"pricing/?"),
    AppRoute(r"match(?:/.*)?", private=True),
    AppRoute(r"discover(?:/.*)?", private=True),
    AppRoute(r"practice(?:/.*)?", private=True),
    AppRoute(r"weekly(?:/.*)?", private=True),
    AppRoute(r"apply(?:/.*)?", private=True),
    AppRoute(r"signout/?", private=True),
    AppRoute(r"account/delete/?", private=True),
    AppRoute(r"p/.*", private=True),
    AppRoute(r"dev/.*", private=True, dev_only=True),
)


@dataclass(frozen=True, slots=True)
class Answer:
    """What to send for a GET or HEAD: a file with its status and cache, a 308 to ``location``, or, with no
    ``file`` and no ``location``, the JSON 404."""

    status: int
    file: Path | None = None
    cache_control: str = INDEX_CACHE_CONTROL
    location: str | None = None
    noindex: bool = False


def resolve_static_file(root: Path, path: str) -> Path | None:
    """The existing file ``path`` names inside ``root``, or None (also for traversal and bad paths).

    ``root`` must already be resolved. Anything that resolves outside it (``..`` segments, an absolute path,
    a Windows drive, a symlink out of the folder) or cannot be resolved at all (an embedded NUL) is None.
    """
    if not path:
        return None
    try:
        target = (root / path).resolve()
        if target.is_relative_to(root) and target.is_file():
            return target
    except (ValueError, OSError):
        return None
    return None


def _plain(path: str) -> bool:
    """A relative address with no backslash (a Windows separator, and a browser reads ``/\\`` as ``//``) and no NUL."""
    return not path.startswith("/") and "\\" not in path and "\x00" not in path


def _segments_ok(path: str) -> bool:
    """No empty, ``.`` or ``..`` segment: a page is addressed by its own name, never by a way round the folders."""
    return all(part not in ("", ".", "..") for part in path.split("/"))


def _app_route(path: str, *, dev: bool) -> AppRoute | None:
    for route in APP_ROUTES:
        if route.dev_only and not dev:
            continue
        if re.fullmatch(route.pattern, path, flags=re.DOTALL):
            return route
    return None


def _not_found(not_found: Path | None) -> Answer:
    return Answer(404, not_found) if not_found is not None else Answer(404)


def _cache_control(file: Path, path: str) -> str:
    if file.suffix.lower() == ".html":
        return INDEX_CACHE_CONTROL
    return ASSET_CACHE_CONTROL if path.startswith(ASSETS_DIR) else FILE_CACHE_CONTROL


def locate(static_dir: Path, path: str, dev: bool = False) -> Answer:
    """What to answer a GET or HEAD for ``path`` (the part after the leading slash) with. See the module doc.

    ``dev`` serves the ``dev/`` routes (a non-prod server). Blocking file-system calls: run it in a worker thread.
    """
    root = static_dir.resolve()
    index = resolve_static_file(root, INDEX_PAGE)
    if index is None:
        return Answer(404)  # no web build: the JSON 404
    not_found = resolve_static_file(root, NOT_FOUND_PAGE)
    if not _plain(path):
        return _not_found(not_found)
    route = _app_route(path, dev=dev)
    private = route is not None and route.private
    # 0. a page's own index.html is a second address for the page: one 301 to the folder
    if (path == INDEX_PAGE or path.endswith("/" + INDEX_PAGE)) and resolve_static_file(
        root, path
    ) is not None:
        folder = path[: -len(INDEX_PAGE)].rstrip("/")
        if not folder or _segments_ok(folder):
            return Answer(301, location="/" + quote(folder))
    # 1. a real file (the 404 page is not served as a 200 at its own address; it answers as a 404 below)
    target = resolve_static_file(root, path)
    if target is not None and target != not_found and not path.endswith("/"):
        return Answer(200, target, _cache_control(target, path), noindex=private)
    # 2. a page, with or without its trailing slash
    base = path.rstrip("/")
    page = resolve_static_file(root, f"{base}/{INDEX_PAGE}") if base and _segments_ok(base) else None
    if page is not None:
        if base != path:
            return Answer(308, location="/" + quote(base))
        return Answer(200, page, noindex=private)
    # 3. the app's own client routes
    if route is not None:
        return Answer(200, index, noindex=private)
    # 4. anything else
    return _not_found(not_found)


def other_host_target(request: Request, public_url: str) -> str | None:
    """Where a page request for another host goes: the same path and query on ``public_url``; None when it is on that host.

    The host is read from ``Host`` and from ``X-Forwarded-Host`` (a proxy may rewrite the first), like the cross-site guard does.
    """
    wanted = header_host(urlsplit(public_url).netloc)
    served = [request.headers.get("host"), *(request.headers.get("x-forwarded-host") or "").split(",")]
    hosts = [header_host(value) for value in served if value]
    if wanted is None or not hosts or wanted in hosts:
        return None
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{public_url}{quote(request.url.path)}{query}"


# ── Moving from the old address ──────────────────────────────────────────────────────────────────────────────────
# A browser's localStorage (its profile, recent matches, theme) belongs to the old host and cannot be read on the new
# one. So the first time a person's browser asks the old host for a page, it gets a tiny moving page instead of the
# 308: its script reads the known keys and goes on to the same path on the public URL with them in the fragment
# (`src/lib/importOld.ts` takes them in). A cookie makes later visits plain 308s, and crawlers always get the 308.
MOVED_COOKIE = "tayyari_moved"
MOVING_SCRIPT_PATH = "__moving.js"
_BOT_UA = re.compile(
    r"bot|crawl|spider|slurp|preview|facebook|embedly|whatsapp|linkedin|twitter|slack|discord|telegram|skype|pinterest"
    r"|curl|wget|python|httpx|requests|fetch|scrape|headless|lighthouse|pagespeed|inspection"
    r"|gpt|claude|anthropic|openai|perplexity|google|bing|duckduck|yandex|baidu|semrush|ahrefs|mj12",
    re.I,
)
_BROWSER_UA = re.compile(r"^Mozilla/\d.*(?:Chrome|Chromium|CriOS|Safari|Firefox|FxiOS|Edg|OPR)", re.I)

MOVING_SCRIPT = """(function () {
  var target = document.documentElement.getAttribute('data-target') || '/';
  var keys = ['tayyari.v1', 'tayyari.matches.recent', 'tayyari.match.draft', 'tayyari.practice.prefs',
    'tayyari.scheme', 'tayyari.atmo', 'tayyari.hue0'];
  var out = {}, found = 0, hash = '';
  try {
    for (var i = 0; i < keys.length; i++) {
      var v = localStorage.getItem(keys[i]);
      if (v !== null) { out[keys[i]] = v; found++; }
    }
  } catch (e) {}
  if (found) {
    try {
      var bytes = new TextEncoder().encode(JSON.stringify(out)), bin = '';
      for (var j = 0; j < bytes.length; j++) bin += String.fromCharCode(bytes[j]);
      var packed = encodeURIComponent(btoa(bin));
      if (packed.length < 1500000) hash = '#leafout-import=' + packed;
    } catch (e) {}
  }
  document.cookie = 'tayyari_moved=1; Max-Age=31536000; Path=/; Secure; SameSite=Lax';
  location.replace(target + hash);
})();
"""


def moving_page(target: str) -> str:
    """The old host's one-time page: carries this browser's saved keys to ``target`` (see above)."""
    safe = target.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    return (
        '<!doctype html><html lang="en" data-target="' + safe + '"><head><meta charset="utf-8">'
        '<meta name="robots" content="noindex"><title>Moving to Leafout</title>'
        f'<script src="/{MOVING_SCRIPT_PATH}" defer></script></head>'
        '<body><p>Tayyari is now Leafout. <a href="' + safe + '">Continue</a></p></body></html>'
    )


def wants_moving_page(request: Request) -> bool:
    """A real browser asking for a page that has not moved yet: no ``tayyari_moved`` cookie, a user agent that looks like
    a browser (Mozilla plus Chrome, Safari, Firefox or Edge) and none of a wide list of crawlers, AI fetchers and scripts,
    and a path that is a page, not a file (``/robots.txt`` and ``/sitemap.xml`` are always the plain 308)."""
    if request.cookies.get(MOVED_COOKIE):
        return False
    if "." in request.url.path.rsplit("/", 1)[-1]:
        return False
    agent = request.headers.get("user-agent") or ""
    return bool(_BROWSER_UA.search(agent)) and not _BOT_UA.search(agent)


_CANONICAL = re.compile(r"<link\b[^>]*\brel=[\"']canonical[\"'][^>]*>\s*", re.I)
_JSON_LD = re.compile(
    r"<script\b[^>]*\btype=[\"']application/ld\+json[\"'][^>]*>.*?</script>\s*", re.I | re.S
)
_OG_URL = re.compile(r"<meta\b[^>]*\bproperty=[\"']og:url[\"'][^>]*>\s*", re.I)
_ROBOTS_META = re.compile(r"<meta\b[^>]*\bname=[\"']robots[\"'][^>]*>\s*", re.I)


def private_shell(html: str) -> str:
    """The app shell for a private route: no canonical link, no structured data, no ``og:url``, and a robots ``noindex`` meta.

    The shell carries Home's head, so left alone a private address would name Home as its canonical. A page with no
    ``</head>`` (a fragment) is returned with only those removals.
    """
    for pattern in (_CANONICAL, _JSON_LD, _OG_URL, _ROBOTS_META):
        html = pattern.sub("", html)
    return html.replace("</head>", '<meta name="robots" content="noindex" />\n  </head>', 1)


PRICE_PLACEHOLDER = re.compile(r"\{\{price:([a-z0-9]+)\}\}")
COMING_SOON = "Coming soon"


def fill_prices(html: str, settings: Settings) -> str:
    """Replace ``{{price:pack3}}`` and ``{{price:pass30}}`` with the rupee price on sale, or "Coming soon".

    The same rule as the pricing screen: a product that is not on sale (no price, under the provider's minimum, or Razorpay
    without its secrets) has no price to print. Any other ``{{price:...}}`` name also reads "Coming soon", so no raw
    placeholder ever reaches a visitor.
    """

    def one(found: re.Match[str]) -> str:
        product = next((key for key in PRODUCTS if key == found.group(1)), None)
        price = on_sale(settings, product, "INR") if product is not None else None
        return format_price(price, "INR") if price is not None else COMING_SOON

    return PRICE_PLACEHOLDER.sub(one, html)


def _is_pricing_page(path: str, file: Path) -> bool:
    return path.rstrip("/") == "pricing" and file.name == INDEX_PAGE and file.parent.name == "pricing"


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Add the API 404 and the web-app route. Register every other route first (see module doc)."""
    settings: Settings | None = getattr(app.state, "settings", None)
    dev = settings is not None and not settings.is_prod

    @app.api_route("/api", methods=_API_METHODS, include_in_schema=False)
    async def api_root_not_found() -> Response:
        raise HTTPException(404, NOT_FOUND_MESSAGE)

    @app.api_route("/api/{_path:path}", methods=_API_METHODS, include_in_schema=False)
    async def api_not_found(_path: str) -> Response:
        raise HTTPException(404, NOT_FOUND_MESSAGE)

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def web_app(request: Request, path: str) -> Response:
        if path == MOVING_SCRIPT_PATH:
            return Response(
                MOVING_SCRIPT, media_type="text/javascript", headers={"Cache-Control": "no-store"}
            )
        if settings is not None and settings.auth.redirect_other_hosts:
            elsewhere = other_host_target(request, settings.auth.public_url)
            if elsewhere is not None:
                no_store = {"Cache-Control": "no-store", "X-Robots-Tag": NOINDEX}
                if request.method == "GET" and wants_moving_page(request):
                    return HTMLResponse(moving_page(elsewhere), headers=no_store)
                return RedirectResponse(elsewhere, status_code=308, headers={"Cache-Control": "no-store"})
        answer = await anyio.to_thread.run_sync(locate, static_dir, path, dev)
        if answer.location is not None:
            query = f"?{request.url.query}" if request.url.query else ""
            return RedirectResponse(answer.location + query, status_code=answer.status)
        if answer.file is None:
            raise HTTPException(404, NOT_FOUND_MESSAGE)
        headers = {"Cache-Control": answer.cache_control}
        if answer.noindex:
            headers["X-Robots-Tag"] = NOINDEX
        if answer.status == 200 and answer.file.suffix.lower() == ".html":
            pricing = settings is not None and _is_pricing_page(path, answer.file)
            if answer.noindex or pricing:
                html = await anyio.to_thread.run_sync(answer.file.read_text, "utf-8")
                if answer.noindex:
                    html = private_shell(html)
                if pricing and settings is not None:
                    html = fill_prices(html, settings)
                return HTMLResponse(html, headers=headers)
        return FileResponse(answer.file, status_code=answer.status, headers=headers)


__all__ = [
    "APP_ROUTES",
    "ASSET_CACHE_CONTROL",
    "FILE_CACHE_CONTROL",
    "INDEX_CACHE_CONTROL",
    "NOINDEX",
    "NOT_FOUND_MESSAGE",
    "Answer",
    "AppRoute",
    "fill_prices",
    "locate",
    "mount_spa",
    "other_host_target",
    "private_shell",
    "resolve_static_file",
]
