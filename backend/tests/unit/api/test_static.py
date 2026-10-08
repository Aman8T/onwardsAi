from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from tayyari.api.errors import install_error_handlers
from tayyari.api.static import (
    ASSET_CACHE_CONTROL,
    INDEX_CACHE_CONTROL,
    locate,
    mount_spa,
    resolve_static_file,
)

INDEX = "<!doctype html><title>Tayyari</title><div id=root></div>"
SECRET = "top secret: not part of the web build"


@pytest.fixture
def site(tmp_path: Path) -> Path:
    """``<tmp>/static`` (the web build) next to ``<tmp>/secret.txt`` (what traversal would reach)."""
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "app-3f9a.js").write_text("console.log('hi')", encoding="utf-8")
    (root / "assets" / "app-3f9a.css").write_text("body{margin:0}", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (root / "sample-profile.json").write_text('{"sample": true}', encoding="utf-8")
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    return root


def make_app(static_dir: Path) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)

    @app.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/analyze")
    async def analyze() -> dict[str, str]:
        return {"route": "analyze"}

    mount_spa(app, static_dir)  # last: it matches every path
    return app


def client_for(static_dir: Path) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(static_dir)), base_url="http://test")


# ───────────────────────────── serving files ─────────────────────────────


async def test_the_root_is_index_html_and_never_cached(site: Path) -> None:
    async with client_for(site) as client:
        res = await client.get("/")
    assert res.status_code == 200
    assert res.text == INDEX
    assert res.headers["cache-control"] == "no-cache" == INDEX_CACHE_CONTROL
    assert res.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize(
    ("path", "media"),
    [
        ("/assets/app-3f9a.js", "javascript"),
        ("/assets/app-3f9a.css", "text/css"),
        ("/favicon.svg", "image/svg+xml"),
        ("/sample-profile.json", "application/json"),
    ],
)
async def test_real_files_are_served_with_an_hour_of_caching(site: Path, path: str, media: str) -> None:
    async with client_for(site) as client:
        res = await client.get(path)
    assert res.status_code == 200
    assert res.headers["cache-control"] == "public, max-age=3600" == ASSET_CACHE_CONTROL
    assert media in res.headers["content-type"]
    assert res.content == (site / path.lstrip("/")).read_bytes()


async def test_index_html_requested_by_name_is_still_not_cached(site: Path) -> None:
    async with client_for(site) as client:
        res = await client.get("/index.html")
    assert res.text == INDEX
    assert res.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path", ["/profile/abc123", "/some/deep/client/route", "/assets", "/assets/", "/missing.js"]
)
async def test_other_paths_fall_back_to_index_html(site: Path, path: str) -> None:
    async with client_for(site) as client:
        res = await client.get(path)
    assert res.status_code == 200
    assert res.text == INDEX
    assert res.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/p/0123456789abcdef01234567", "/p/anything/else", "/p/"])
async def test_public_portfolio_pages_are_not_indexed(site: Path, path: str) -> None:
    async with client_for(site) as client:
        res = await client.get(path)
        head = await client.head(path)
    assert res.status_code == 200 and res.text == INDEX
    assert res.headers["x-robots-tag"] == head.headers["x-robots-tag"] == "noindex"
    assert res.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path", ["/", "/profile/abc123", "/pricing", "/assets/app-3f9a.js", "/p", "/people/p/1"]
)
async def test_other_pages_and_files_are_left_to_search_engines(site: Path, path: str) -> None:
    async with client_for(site) as client:
        res = await client.get(path)
    assert "x-robots-tag" not in res.headers


async def test_head_requests_work(site: Path) -> None:
    async with client_for(site) as client:
        index = await client.head("/")
        asset = await client.head("/assets/app-3f9a.js")
    assert index.status_code == asset.status_code == 200
    assert index.content == b""
    assert asset.headers["cache-control"] == "public, max-age=3600"


async def test_other_methods_are_not_allowed_on_the_web_app(site: Path) -> None:
    async with client_for(site) as client:
        res = await client.post("/some/page")
    assert res.status_code == 405
    assert res.json() == {"error": "Method Not Allowed"}


# ───────────────────────────── the API is never shadowed ─────────────────────────────


async def test_registered_api_routes_win_because_the_spa_is_mounted_last(site: Path) -> None:
    async with client_for(site) as client:
        assert (await client.get("/api/health")).json() == {"ok": True}
        assert (await client.post("/api/analyze")).json() == {"route": "analyze"}


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@pytest.mark.parametrize(
    "path", ["/api", "/api/", "/api/nothing", "/api/profile/a/b/c", "/api/health/deeper"]
)
async def test_unknown_api_paths_are_a_json_404_never_the_html_shell(
    site: Path, method: str, path: str
) -> None:
    async with client_for(site) as client:
        res = await client.request(method, path)
    assert res.status_code == 404
    if method != "HEAD":
        assert res.json() == {"error": "Not found"}
    assert res.headers["content-type"].startswith("application/json")


async def test_a_path_that_merely_starts_with_api_is_a_web_route(site: Path) -> None:
    async with client_for(site) as client:
        res = await client.get("/apiary")
    assert res.status_code == 200
    assert res.text == INDEX


# ───────────────────────────── no web build ─────────────────────────────


async def test_without_index_html_everything_is_a_json_404(tmp_path: Path) -> None:
    empty = tmp_path / "no-build"
    empty.mkdir()
    async with client_for(empty) as client:
        for path in ("/", "/assets/app.js", "/profile/x"):
            res = await client.get(path)
            assert (res.status_code, res.json()) == (404, {"error": "Not found"})
        assert (await client.get("/api/health")).json() == {"ok": True}


async def test_a_missing_static_folder_is_a_json_404_not_a_crash(tmp_path: Path) -> None:
    async with client_for(tmp_path / "never-built") as client:
        res = await client.get("/")
    assert (res.status_code, res.json()) == (404, {"error": "Not found"})


async def test_a_build_that_appears_later_is_served_without_a_restart(tmp_path: Path) -> None:
    later = tmp_path / "later"
    async with client_for(later) as client:
        assert (await client.get("/")).status_code == 404
        later.mkdir()
        (later / "index.html").write_text(INDEX, encoding="utf-8")
        assert (await client.get("/")).text == INDEX


# ───────────────────────────── path traversal ─────────────────────────────

TRAVERSAL = [
    "../secret.txt",
    "../../secret.txt",
    "assets/../../secret.txt",
    "..\\secret.txt",
    "assets\\..\\..\\secret.txt",
    "/../secret.txt",
    "%2e%2e/secret.txt",
    "....//secret.txt",
    "C:/Windows/win.ini",
    "C:\\Windows\\win.ini",
    "\\\\server\\share\\x",
    "/etc/passwd",
    "assets/app-3f9a.js\x00.png",
    "\x00",
    "assets/" + "a" * 5000,
]


@pytest.mark.parametrize("path", TRAVERSAL, ids=[repr(p)[:30] for p in TRAVERSAL])
def test_resolve_static_file_refuses_anything_outside_the_build(site: Path, path: str) -> None:
    assert resolve_static_file(site.resolve(), path) is None


@pytest.mark.parametrize("path", TRAVERSAL, ids=[repr(p)[:30] for p in TRAVERSAL])
def test_locate_answers_traversal_with_index_html(site: Path, path: str) -> None:
    found = locate(site, path)
    assert found is not None
    file, cache_control = found
    assert file == site / "index.html"
    assert cache_control == "no-cache"


async def test_traversal_over_http_never_leaks_the_neighbouring_file(site: Path) -> None:
    async with client_for(site) as client:
        for raw in (
            "/..%2fsecret.txt",
            "/%2e%2e/secret.txt",
            "/..%5csecret.txt",
            "/assets/..%2f..%2fsecret.txt",
            "/%00",
        ):
            res = await client.get(raw)
            assert SECRET not in res.text
            assert res.status_code in (200, 400, 404)


def test_resolve_static_file_finds_real_files(site: Path) -> None:
    root = site.resolve()
    assert resolve_static_file(root, "assets/app-3f9a.js") == (root / "assets" / "app-3f9a.js")
    assert resolve_static_file(root, "assets/../favicon.svg") == (root / "favicon.svg")
    assert resolve_static_file(root, "assets") is None  # a directory
    assert resolve_static_file(root, "") is None
    assert resolve_static_file(root, "nope.js") is None


def test_a_symlink_out_of_the_build_is_not_followed(site: Path, tmp_path: Path) -> None:
    link = site / "assets" / "escape.txt"
    try:
        os.symlink(tmp_path / "secret.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here (Windows without developer mode)")
    assert resolve_static_file(site.resolve(), "assets/escape.txt") is None
    found = locate(site, "assets/escape.txt")
    assert found is not None
    assert found[0] == site / "index.html"
