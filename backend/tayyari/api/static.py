"""Serve the built web app (``backend/static``): files, content pages, the app's own routes and the 404 page.

`mount_spa(app, static_dir)` adds two catch-all routes:

    /api and /api/...   a JSON 404 ``{"error": "Not found"}`` for every method (an unknown API route must
                        never answer with an HTML page)
    /<anything>         GET and HEAD, answered by `locate`, first match wins:
                        1. a real file under ``static_dir``, cached for an hour (``public, max-age=3600``: the
                           build's assets are content-hashed); an ``.html`` file is ``no-cache`` instead
                        2. a page, ``<path>/index.html`` (the generated content pages, and /pricing when the build
                           has its own): 200, ``no-cache``. With a trailing slash it is a 308 to the address
                           without one (``/how-it-works/`` to ``/how-it-works``)
                        3. one of the app's own client routes (`APP_ROUTES`): index.html, 200, ``no-cache``, so a
                           deep link opens the app
                        4. anything else: ``404.html`` with status 404 and ``no-cache``; the JSON 404 when the
                           build has none. ``/404.html`` itself is a 404 too.
                        The private routes (match, discover, practice, the published portfolios under /p/, the dev
                        galleries) send ``X-Robots-Tag: noindex``; "/", "/pricing" and the content pages do not.

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
from urllib.parse import quote

import anyio.to_thread
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from tayyari.core.settings import Settings

NOT_FOUND_MESSAGE = "Not found"
INDEX_PAGE = "index.html"
NOT_FOUND_PAGE = "404.html"
INDEX_CACHE_CONTROL = "no-cache"
ASSET_CACHE_CONTROL = "public, max-age=3600"
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
#   p/...              /p/<publicId>, the published portfolio                              src/main.tsx
#   dev/...            the galleries /dev/portfolios, /dev/match, /dev/discover, /dev/practice, /dev/prepare:
#                      rendered by development builds only, so served only on a non-prod server
APP_ROUTES: tuple[AppRoute, ...] = (
    AppRoute(""),
    AppRoute(r"pricing/?"),
    AppRoute(r"match(?:/.*)?", private=True),
    AppRoute(r"discover(?:/.*)?", private=True),
    AppRoute(r"practice(?:/.*)?", private=True),
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


def _cache_control(file: Path) -> str:
    return INDEX_CACHE_CONTROL if file.suffix.lower() == ".html" else ASSET_CACHE_CONTROL


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
    # 1. a real file (the 404 page is not served as a 200 at its own address; it answers as a 404 below)
    target = resolve_static_file(root, path)
    if target is not None and target != not_found and not path.endswith("/"):
        return Answer(200, target, _cache_control(target), noindex=private)
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
        answer = await anyio.to_thread.run_sync(locate, static_dir, path, dev)
        if answer.location is not None:
            query = f"?{request.url.query}" if request.url.query else ""
            return RedirectResponse(answer.location + query, status_code=answer.status)
        if answer.file is None:
            raise HTTPException(404, NOT_FOUND_MESSAGE)
        headers = {"Cache-Control": answer.cache_control}
        if answer.noindex:
            headers["X-Robots-Tag"] = NOINDEX
        return FileResponse(answer.file, status_code=answer.status, headers=headers)


__all__ = [
    "APP_ROUTES",
    "ASSET_CACHE_CONTROL",
    "INDEX_CACHE_CONTROL",
    "NOINDEX",
    "NOT_FOUND_MESSAGE",
    "Answer",
    "AppRoute",
    "locate",
    "mount_spa",
    "resolve_static_file",
]
