"""Serve the built web app (``backend/static``) with index.html as the single-page-app fallback.

`mount_spa(app, static_dir)` adds two catch-all routes:

    /api and /api/...   a JSON 404 ``{"error": "Not found"}`` for every method (an unknown API route must
                        never answer with the HTML shell)
    /<anything>         GET and HEAD: a real file under ``static_dir`` is served with
                        ``Cache-Control: public, max-age=3600`` (the build's assets are content-hashed);
                        every other path gets index.html with ``Cache-Control: no-cache`` so client-side
                        routes deep-link and a new deploy is picked up at once. When index.html does not
                        exist (no web build deployed) the answer is a JSON 404.
    /p/<anything>       a published portfolio page (the shell the page renders in): the HTML also carries
                        ``X-Robots-Tag: noindex``, so a share link is not listed by search engines.

ORDER MATTERS: Starlette matches routes in the order they were registered, and these two match every path.
Call `mount_spa` after every other route (``include_router``, ``@app.get`` ...) has been added, or it
shadows them. The files are looked up on every request, so a build that appears later is served without a
restart.
"""

from __future__ import annotations

from pathlib import Path

import anyio.to_thread
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

NOT_FOUND_MESSAGE = "Not found"
INDEX_CACHE_CONTROL = "no-cache"
ASSET_CACHE_CONTROL = "public, max-age=3600"
PUBLIC_PAGE_PREFIX = "p/"
NOINDEX = "noindex"
_API_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


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


def locate(static_dir: Path, path: str) -> tuple[Path, str] | None:
    """The file to answer ``path`` with and its Cache-Control, or None when there is no web build.

    A real file under ``static_dir`` is cached for an hour (index.html itself excepted); everything else is
    index.html, never cached. Blocking file-system calls: run it in a worker thread.
    """
    index = static_dir / "index.html"
    if not index.is_file():
        return None
    target = resolve_static_file(static_dir.resolve(), path)
    if target is not None and target != index.resolve():
        return target, ASSET_CACHE_CONTROL
    return index, INDEX_CACHE_CONTROL


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Add the API 404 and the single-page-app routes. Register every other route first (see module doc)."""

    @app.api_route("/api", methods=_API_METHODS, include_in_schema=False)
    async def api_root_not_found() -> Response:
        raise HTTPException(404, NOT_FOUND_MESSAGE)

    @app.api_route("/api/{_path:path}", methods=_API_METHODS, include_in_schema=False)
    async def api_not_found(_path: str) -> Response:
        raise HTTPException(404, NOT_FOUND_MESSAGE)

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def web_app(path: str) -> Response:
        found = await anyio.to_thread.run_sync(locate, static_dir, path)
        if found is None:
            raise HTTPException(404, NOT_FOUND_MESSAGE)
        file, cache_control = found
        headers = {"Cache-Control": cache_control}
        if path.startswith(PUBLIC_PAGE_PREFIX):
            headers["X-Robots-Tag"] = NOINDEX
        return FileResponse(file, headers=headers)


__all__ = [
    "ASSET_CACHE_CONTROL",
    "INDEX_CACHE_CONTROL",
    "NOINDEX",
    "NOT_FOUND_MESSAGE",
    "PUBLIC_PAGE_PREFIX",
    "locate",
    "mount_spa",
    "resolve_static_file",
]
