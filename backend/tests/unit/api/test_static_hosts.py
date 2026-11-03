"""The web app's pages on the wrong host: with ``TAYYARI_AUTH__REDIRECT_OTHER_HOSTS`` on, a GET or HEAD for a page on any
host other than the public URL's is a 308 to the same path and query on the public URL; the API and the right host are
untouched; with the setting off (the default) nothing is redirected. Also the two client routes the Leafout site sends
people to (``/signout``, ``/account/delete``): the app's index, private, and nothing next to them."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from starlette.requests import Request

from tayyari.api.static import other_host_target, wants_moving_page
from tayyari.core.settings import Settings
from tests.unit.api.helpers import http_scope
from tests.unit.api.test_static import INDEX, NOINDEX, client_for, site  # noqa: F401  (site is a fixture)

PUBLIC = "https://app.leafout.in"


def settings_for(redirect: bool = True) -> Settings:
    return Settings(_env_file=None, env="test", auth={"public_url": PUBLIC, "redirect_other_hosts": redirect})


def request(
    host: str | None, *, forwarded: str | None = None, path: str = "/match", query: str = ""
) -> Request:
    headers = [("host", host)] if host else []
    if forwarded:
        headers.append(("x-forwarded-host", forwarded))
    scope = http_scope(path=path, method="GET", headers=headers)
    scope["query_string"] = query.encode()
    return Request(scope)


@pytest.mark.parametrize(
    ("host", "forwarded", "target"),
    [
        ("tayyari.fastapicloud.dev", None, f"{PUBLIC}/match?x=1"),
        ("app.leafout.in.evil.example", None, f"{PUBLIC}/match?x=1"),
        ("leafout.in", None, f"{PUBLIC}/match?x=1"),
        ("internal:8000", "tayyari.fastapicloud.dev", f"{PUBLIC}/match?x=1"),
        ("app.leafout.in", None, None),
        ("APP.LEAFOUT.IN:443", None, None),
        ("internal:8000", "other.example, app.leafout.in", None),
        ("internal:8000", "app.leafout.in", None),
    ],
)
def test_the_target_is_the_same_path_and_query_on_the_public_url(
    host: str, forwarded: str | None, target: str | None
) -> None:
    assert other_host_target(request(host, forwarded=forwarded, query="x=1"), PUBLIC) == target


def test_a_request_with_no_host_is_left_alone() -> None:
    assert other_host_target(request(None), PUBLIC) is None


def test_the_path_is_quoted_and_the_query_kept_whole() -> None:
    found = other_host_target(request("old.example", path="/p/a b/é", query="a=1&b=%20"), PUBLIC)
    assert found == f"{PUBLIC}/p/a%20b/%C3%A9?a=1&b=%20"


async def get(client: httpx.AsyncClient, path: str, host: str, method: str = "GET") -> httpx.Response:
    return await client.request(method, path, headers={"host": host}, follow_redirects=False)


async def test_pages_on_another_host_are_308s_and_the_api_is_not(site: Path) -> None:  # noqa: F811
    async with client_for(site, settings_for()) as client:
        for method in ("GET", "HEAD"):
            moved = await get(client, "/match/abc?x=1", "tayyari.fastapicloud.dev", method)
            assert moved.status_code == 308 and moved.headers["location"] == f"{PUBLIC}/match/abc?x=1"
            assert moved.headers["cache-control"] == "no-store"
        for path in ("/", "/pricing", "/p/0123456789abcdef01234567", "/how-it-works", "/robots.txt", "/nope"):
            res = await get(client, path, "tayyari.fastapicloud.dev")
            assert res.status_code == 308 and res.headers["location"] == f"{PUBLIC}{path}", path
        for path in ("/api/health", "/api/unknown"):
            res = await get(client, path, "tayyari.fastapicloud.dev")
            assert res.status_code != 308, path
        here = await get(client, "/", "app.leafout.in")
        assert here.status_code == 200 and here.text == INDEX


async def test_nothing_is_redirected_unless_the_setting_is_on(site: Path) -> None:  # noqa: F811
    async with client_for(site, settings_for(redirect=False)) as client:
        res = await get(client, "/match", "tayyari.fastapicloud.dev")
        assert res.status_code == 200
    async with client_for(site) as client:  # no settings on the app at all
        res = await get(client, "/match", "tayyari.fastapicloud.dev")
        assert res.status_code == 200


def test_the_setting_is_off_by_default_and_never_read_from_nowhere() -> None:
    assert Settings(_env_file=None, env="test").auth.redirect_other_hosts is False


@pytest.mark.parametrize("path", ["signout", "signout/", "account/delete", "account/delete/"])
async def test_the_signout_and_delete_pages_are_the_apps_index_and_private(site: Path, path: str) -> None:  # noqa: F811
    async with client_for(site) as client:
        res = await client.get(f"/{path}")
    assert res.status_code == 200 and res.text == INDEX
    assert res.headers["x-robots-tag"] == NOINDEX
    assert res.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path", ["account", "account/", "account/deleted", "account/delete/x", "signouts", "signout/x"]
)
async def test_nothing_next_to_them_is_an_app_route(site: Path, path: str) -> None:  # noqa: F811
    async with client_for(site) as client:
        res = await client.get(f"/{path}")
    assert res.status_code == 404


BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


async def test_a_browser_that_has_not_moved_gets_the_moving_page_once(site: Path) -> None:  # noqa: F811
    async with client_for(site, settings_for()) as client:
        first = await client.get(
            "/match/abc?x=1", headers={"host": "tayyari.fastapicloud.dev", "user-agent": BROWSER}
        )
        assert first.status_code == 200
        assert 'data-target="https://app.leafout.in/match/abc?x=1"' in first.text
        assert '<script src="/__moving.js"' in first.text
        assert first.headers["x-robots-tag"] == "noindex"
        moved = await client.get(
            "/match/abc",
            headers={"host": "tayyari.fastapicloud.dev", "user-agent": BROWSER, "cookie": "tayyari_moved=1"},
            follow_redirects=False,
        )
        assert moved.status_code == 308


async def test_the_moving_script_is_served_on_the_old_host_and_crawlers_get_the_308(site: Path) -> None:  # noqa: F811
    async with client_for(site, settings_for()) as client:
        script = await client.get("/__moving.js", headers={"host": "tayyari.fastapicloud.dev"})
        assert script.status_code == 200 and "leafout-import=" in script.text
        assert script.headers["content-type"].startswith("text/javascript")
        bot = await client.get(
            "/pricing",
            headers={"host": "tayyari.fastapicloud.dev", "user-agent": "Googlebot/2.1"},
            follow_redirects=False,
        )
        assert bot.status_code == 308


async def test_a_quote_in_the_target_cannot_break_out_of_the_attribute(site: Path) -> None:  # noqa: F811
    async with client_for(site, settings_for()) as client:
        res = await client.get(
            '/x"><script>alert(1)</script>',
            headers={"host": "tayyari.fastapicloud.dev", "user-agent": BROWSER},
        )
        assert "<script>alert(1)" not in res.text


@pytest.mark.parametrize(
    "agent",
    [
        "Mozilla/5.0 ChatGPT-User/1.0",
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0)",
        "Mozilla/5.0 (compatible; PerplexityBot/1.0)",
        "Mozilla/5.0 Chrome/130.0 Safari/537.36 Chrome-Lighthouse",
        "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
        "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/130.0 Safari/537.36",
        "python-httpx/0.27",
        "Mozilla/5.0",
        "",
    ],
)
async def test_only_a_real_browser_gets_the_moving_page(site: Path, agent: str) -> None:  # noqa: F811
    async with client_for(site, settings_for()) as client:
        res = await client.get(
            "/how-it-works",
            headers={"host": "tayyari.fastapicloud.dev", "user-agent": agent},
            follow_redirects=False,
        )
    assert res.status_code == 308 and res.headers["location"] == f"{PUBLIC}/how-it-works", agent


@pytest.mark.parametrize("path", ["/robots.txt", "/sitemap.xml", "/assets/app.js"])
async def test_files_on_the_old_host_are_always_a_plain_308_even_for_a_browser(site: Path, path: str) -> None:  # noqa: F811
    async with client_for(site, settings_for()) as client:
        res = await client.get(
            path, headers={"host": "tayyari.fastapicloud.dev", "user-agent": BROWSER}, follow_redirects=False
        )
    assert res.status_code == 308 and res.headers["location"] == f"{PUBLIC}{path}"


@pytest.mark.parametrize(
    "agent",
    [
        BROWSER,
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0",
    ],
)
async def test_the_real_browsers_still_get_the_moving_page(site: Path, agent: str) -> None:  # noqa: F811
    async with client_for(site, settings_for()) as client:
        res = await client.get("/pricing", headers={"host": "tayyari.fastapicloud.dev", "user-agent": agent})
    assert res.status_code == 200 and "data-target=" in res.text


def test_wants_moving_page_reads_the_cookie_the_agent_and_the_path() -> None:
    def ask(agent: str, path: str = "/pricing", cookie: str | None = None) -> bool:
        headers = [("user-agent", agent)] + ([("cookie", cookie)] if cookie else [])
        return wants_moving_page(Request(http_scope(path=path, method="GET", headers=headers)))

    assert ask(BROWSER)
    assert not ask(BROWSER, cookie="tayyari_moved=1")
    assert not ask(BROWSER, path="/robots.txt")
    assert not ask("Mozilla/5.0 ChatGPT-User/1.0")
