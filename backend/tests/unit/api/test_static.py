from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from tayyari.api.errors import install_error_handlers
from tayyari.api.static import (
    ASSET_CACHE_CONTROL,
    INDEX_CACHE_CONTROL,
    NOINDEX,
    Answer,
    locate,
    mount_spa,
    resolve_static_file,
)
from tayyari.core.settings import Settings

INDEX = "<!doctype html><title>Tayyari</title><div id=root></div>"
PAGE = "<!doctype html><title>How it works</title><h1>How it works</h1>"
MISSING = "<!doctype html><title>Not found</title><h1>Nothing here</h1>"
SECRET = "top secret: not part of the web build"
PRIVATE_ROUTES = [
    "match",
    "match/abc123",
    "match/abc123/resume",
    "match/abc123/cover",
    "discover",
    "discover/",
    "practice",
    "practice/",
    "weekly",
    "weekly/",
    "weekly/archive",
    "p/0123456789abcdef01234567",
    "p/anything/else",
    "p/",
]
PUBLIC_APP_ROUTES = ["", "pricing", "pricing/"]


@pytest.fixture
def site(tmp_path: Path) -> Path:
    """``<tmp>/static`` (the web build) next to ``<tmp>/secret.txt`` (what traversal would reach)."""
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "how-it-works").mkdir()
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "404.html").write_text(MISSING, encoding="utf-8")
    (root / "how-it-works" / "index.html").write_text(PAGE, encoding="utf-8")
    (root / "assets" / "app-3f9a.js").write_text("console.log('hi')", encoding="utf-8")
    (root / "assets" / "app-3f9a.css").write_text("body{margin:0}", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (root / "sample-profile.json").write_text('{"sample": true}', encoding="utf-8")
    (root / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (root / "sitemap.xml").write_text('<?xml version="1.0"?><urlset/>', encoding="utf-8")
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    return root


def make_app(static_dir: Path, settings: Settings | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)
    if settings is not None:
        app.state.settings = settings  # what create_app sets; mount_spa reads whether it is prod

    @app.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/analyze")
    async def analyze() -> dict[str, str]:
        return {"route": "analyze"}

    mount_spa(app, static_dir)  # last: it matches every path
    return app


def client_for(static_dir: Path, settings: Settings | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_app(static_dir, settings)), base_url="http://test"
    )


# ───────────────────────────── real files ─────────────────────────────


async def test_the_root_is_index_html_and_never_cached(site: Path) -> None:
    async with client_for(site) as client:
        res = await client.get("/")
    assert res.status_code == 200
    assert res.text == INDEX
    assert res.headers["cache-control"] == "no-cache" == INDEX_CACHE_CONTROL
    assert res.headers["content-type"].startswith("text/html")
    assert "x-robots-tag" not in res.headers


@pytest.mark.parametrize(
    ("path", "media"),
    [
        ("/assets/app-3f9a.js", "javascript"),
        ("/assets/app-3f9a.css", "text/css"),
        ("/favicon.svg", "image/svg+xml"),
        ("/sample-profile.json", "application/json"),
        ("/robots.txt", "text/plain"),
        ("/sitemap.xml", "xml"),
    ],
)
async def test_real_files_are_served_with_an_hour_of_caching(site: Path, path: str, media: str) -> None:
    async with client_for(site) as client:
        res = await client.get(path)
    assert res.status_code == 200
    assert res.headers["cache-control"] == "public, max-age=3600" == ASSET_CACHE_CONTROL
    assert media in res.headers["content-type"]
    assert res.content == (site / path.lstrip("/")).read_bytes()


async def test_html_files_requested_by_name_are_never_cached(site: Path) -> None:
    async with client_for(site) as client:
        index = await client.get("/index.html")
        page = await client.get("/how-it-works/index.html")
    assert index.text == INDEX and page.text == PAGE
    assert index.status_code == page.status_code == 200
    assert index.headers["cache-control"] == page.headers["cache-control"] == "no-cache"


# ───────────────────────────── content pages ─────────────────────────────


async def test_a_content_page_is_served_at_its_address_and_never_cached(site: Path) -> None:
    async with client_for(site) as client:
        res = await client.get("/how-it-works")
        head = await client.head("/how-it-works")
    assert res.status_code == head.status_code == 200
    assert res.text == PAGE and head.content == b""
    assert res.headers["cache-control"] == head.headers["cache-control"] == "no-cache"
    assert "x-robots-tag" not in res.headers and "x-robots-tag" not in head.headers


@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_a_trailing_slash_on_a_page_redirects_to_its_canonical_address(site: Path, method: str) -> None:
    async with client_for(site) as client:
        res = await client.request(method, "/how-it-works/", follow_redirects=False)
    assert res.status_code == 308
    assert res.headers["location"] == "/how-it-works"


async def test_the_query_string_survives_the_redirect(site: Path) -> None:
    async with client_for(site) as client:
        res = await client.get("/how-it-works/?utm_source=mail", follow_redirects=False)
    assert res.status_code == 308
    assert res.headers["location"] == "/how-it-works?utm_source=mail"


async def test_the_root_is_never_redirected(site: Path) -> None:
    async with client_for(site) as client:
        res = await client.get("/", follow_redirects=False)
    assert res.status_code == 200


async def test_pricing_is_its_own_page_when_the_build_has_one(site: Path) -> None:
    (site / "pricing").mkdir()
    (site / "pricing" / "index.html").write_text("<title>Pricing</title>", encoding="utf-8")
    async with client_for(site) as client:
        res = await client.get("/pricing")
        slash = await client.get("/pricing/", follow_redirects=False)
    assert res.text == "<title>Pricing</title>"
    assert res.headers["cache-control"] == "no-cache"
    assert "x-robots-tag" not in res.headers
    assert slash.status_code == 308 and slash.headers["location"] == "/pricing"


# ───────────────────────────── the app's own routes ─────────────────────────────


@pytest.mark.parametrize("path", PUBLIC_APP_ROUTES + PRIVATE_ROUTES)
async def test_app_routes_get_index_html_with_200(site: Path, path: str) -> None:
    async with client_for(site) as client:
        res = await client.get(f"/{path}")
    assert res.status_code == 200
    assert res.text == INDEX
    assert res.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", PRIVATE_ROUTES)
async def test_private_app_routes_are_not_indexed(site: Path, path: str) -> None:
    async with client_for(site) as client:
        res = await client.get(f"/{path}")
        head = await client.head(f"/{path}")
    assert res.headers["x-robots-tag"] == head.headers["x-robots-tag"] == NOINDEX
    assert res.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", PUBLIC_APP_ROUTES)
async def test_the_home_and_pricing_are_left_to_search_engines(site: Path, path: str) -> None:
    async with client_for(site) as client:
        res = await client.get(f"/{path}")
    assert "x-robots-tag" not in res.headers


@pytest.mark.parametrize("path", ["dev/match", "dev/portfolios", "dev/discover", "dev/"])
async def test_dev_routes_are_served_only_on_a_non_prod_server(
    site: Path, make_settings: Callable[..., Settings], path: str
) -> None:
    async with client_for(site, make_settings(env="local")) as client:
        local = await client.get(f"/{path}")
    assert local.status_code == 200 and local.text == INDEX
    assert local.headers["x-robots-tag"] == NOINDEX
    async with client_for(site, make_settings(env="prod")) as client:
        prod = await client.get(f"/{path}")
    async with client_for(site) as client:  # no settings on the app: treated as prod
        bare = await client.get(f"/{path}")
    for res in (prod, bare):
        assert res.status_code == 404 and res.text == MISSING
        assert "x-robots-tag" not in res.headers


async def test_the_404_page_is_not_served_as_a_200_at_its_own_address(site: Path) -> None:
    async with client_for(site) as client:
        res = await client.get("/404.html")
    assert res.status_code == 404
    assert res.text == MISSING
    assert res.headers["cache-control"] == "no-cache"


# ───────────────────────────── anything else ─────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "profile/abc123",
        "some/deep/client/route",
        "assets",
        "assets/",
        "missing.js",
        "favicon.svg/",
        "p",
        "people/p/1",
        "matchmaker",
        "discovery",
        "practices",
        "pricing/extra",
        "how-it-works/extra",
        "apiary",
    ],
)
async def test_anything_else_is_the_404_page_with_status_404(site: Path, path: str) -> None:
    async with client_for(site) as client:
        res = await client.get(f"/{path}")
    assert res.status_code == 404
    assert res.text == MISSING
    assert res.headers["cache-control"] == "no-cache" == INDEX_CACHE_CONTROL
    assert "x-robots-tag" not in res.headers


async def test_head_for_an_unknown_path_is_404_with_no_body(site: Path) -> None:
    async with client_for(site) as client:
        res = await client.head("/profile/abc123")
    assert res.status_code == 404
    assert res.content == b""


async def test_without_a_404_page_an_unknown_path_is_the_json_404(site: Path) -> None:
    (site / "404.html").unlink()
    async with client_for(site) as client:
        res = await client.get("/profile/abc123")
        head = await client.head("/profile/abc123")
        app_route = await client.get("/match/abc123")
    assert (res.status_code, res.json()) == (404, {"error": "Not found"})
    assert head.status_code == 404
    assert app_route.text == INDEX


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


# ───────────────────────────── no web build ─────────────────────────────


async def test_without_index_html_everything_is_a_json_404(tmp_path: Path) -> None:
    empty = tmp_path / "no-build"
    empty.mkdir()
    (empty / "404.html").write_text(MISSING, encoding="utf-8")  # a 404 page alone is not a build
    async with client_for(empty) as client:
        for path in ("/", "/assets/app.js", "/profile/x", "/match/abc", "/how-it-works", "/pricing"):
            res = await client.get(path)
            assert (res.status_code, res.json()) == (404, {"error": "Not found"})
        assert (await client.get("/api/health")).json() == {"ok": True}


def test_a_build_without_index_html_answers_with_the_json_404_marker(tmp_path: Path) -> None:
    assert locate(tmp_path / "nothing", "") == Answer(404)
    assert locate(tmp_path / "nothing", "match/abc") == Answer(404)


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


# ───────────────────────────── path traversal and bad addresses ─────────────────────────────

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
def test_locate_answers_traversal_with_the_404_page(site: Path, path: str) -> None:
    assert locate(site, path) == Answer(404, (site / "404.html").resolve())


@pytest.mark.parametrize(
    "path",
    [
        "//evil.example/",  # a redirect here would be an open redirect
        "/how-it-works/",
        "\\how-it-works/",
        "how-it-works\\/",
        "../how-it-works/",
        "how-it-works/./",
        "how-it-works//..",
    ],
)
def test_a_redirect_only_ever_names_a_page_of_this_build(site: Path, path: str) -> None:
    answer = locate(site, path)
    assert answer.location is None
    assert answer == Answer(404, (site / "404.html").resolve())


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
            assert res.status_code == 404


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
    assert locate(site, "assets/escape.txt") == Answer(404, (site / "404.html").resolve())


def test_locate_answers_a_real_file_with_its_cache_control(site: Path) -> None:
    assert locate(site, "assets/app-3f9a.js") == Answer(
        200, (site / "assets" / "app-3f9a.js").resolve(), ASSET_CACHE_CONTROL
    )
    assert locate(site, "how-it-works/").location == "/how-it-works"
    assert locate(site, "how-it-works").file == (site / "how-it-works" / "index.html").resolve()
