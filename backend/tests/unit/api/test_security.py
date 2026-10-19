"""`CrossSiteGuard` and `SecurityHeadersMiddleware` (findings #8 and #12), on small ASGI apps of their own.

The full app, with its real routes and static files, is covered in ``tests/unit/wiring``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.types import Receive, Scope, Send

from tayyari.api.security import (
    API_CSP,
    CROSS_SITE_MESSAGE,
    HSTS,
    RAZORPAY_CHECKOUT,
    WEBHOOK_PREFIX,
    CrossSiteGuard,
    PageCsp,
    SecurityHeadersMiddleware,
    cross_site_reason,
    header_host,
    inline_script_hashes,
    origin_host,
    page_csp,
    subnet_hash,
)
from tayyari.core.paths import STATIC_DIR
from tests.unit.api.helpers import AsgiExchange, http_scope

SCRIPT = "window.boot = 1;\nwindow.more = 2;\n"


RZP = RAZORPAY_CHECKOUT


def sha(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def headers_of(**values: str) -> Headers:
    return Headers({name.replace("_", "-"): value for name, value in values.items()})


# ───────────────────────────── the pieces of the Origin check ─────────────────────────────


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        ("https://tayyari.example", "tayyari.example"),
        ("https://Tayyari.Example", "tayyari.example"),
        ("https://tayyari.example:443", "tayyari.example"),
        ("http://tayyari.example:80", "tayyari.example"),
        ("http://localhost:8000", "localhost:8000"),
        ("http://127.0.0.1:5173", "127.0.0.1:5173"),
        ("http://[::1]:8000", "::1:8000"),
        ("https://tayyari.example/some/path", "tayyari.example"),
    ],
)
def test_origin_host_is_the_normalised_host(origin: str, host: str) -> None:
    assert origin_host(origin) == host


@pytest.mark.parametrize(
    "origin", ["null", "", "file:///tmp/x.html", "chrome-extension://abc", "https://", "http://[bad"]
)
def test_an_origin_that_is_not_a_web_origin_has_no_host(origin: str) -> None:
    assert origin_host(origin) is None


@pytest.mark.parametrize(
    ("value", "host"),
    [
        ("tayyari.example", "tayyari.example"),
        ("Tayyari.Example:443", "tayyari.example"),
        ("localhost:8000", "localhost:8000"),
        ("[::1]:8000", "::1:8000"),
        (" example.com ", "example.com"),
    ],
)
def test_header_host_is_the_normalised_host(value: str, host: str) -> None:
    assert header_host(value) == host


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({}, None),
        ({"host": "tayyari.example"}, None),
        ({"sec_fetch_site": "cross-site"}, "sec-fetch-site"),
        (
            {"sec_fetch_site": "Cross-Site", "origin": "https://tayyari.example", "host": "tayyari.example"},
            "sec-fetch-site",
        ),
        ({"sec_fetch_site": "same-origin"}, None),
        ({"sec_fetch_site": "same-site"}, None),
        ({"sec_fetch_site": "none"}, None),
        ({"origin": "https://tayyari.example", "host": "tayyari.example"}, None),
        ({"origin": "https://evil.example", "host": "tayyari.example"}, "origin"),
        ({"origin": "https://tayyari.example.evil.example", "host": "tayyari.example"}, "origin"),
        ({"origin": "https://evil.example/tayyari.example", "host": "tayyari.example"}, "origin"),
        ({"origin": "http://tayyari.example:8080", "host": "tayyari.example"}, "origin"),
        ({"origin": "null", "host": "tayyari.example"}, "origin"),
        ({"origin": "https://tayyari.example"}, "origin"),  # no Host to compare with
        (
            {
                "origin": "https://tayyari.example",
                "host": "internal:8000",
                "x_forwarded_host": "tayyari.example",
            },
            None,
        ),
        (
            {
                "origin": "https://tayyari.example",
                "host": "internal",
                "x_forwarded_host": "other.example, tayyari.example",
            },
            None,
        ),
        (
            {
                "origin": "https://evil.example",
                "host": "tayyari.example",
                "x_forwarded_host": "tayyari.example",
            },
            "origin",
        ),
        # a same-origin fetch is vouched for by the browser even when a proxy rewrote Host
        (
            {"sec_fetch_site": "same-origin", "origin": "https://tayyari.example", "host": "internal:8000"},
            None,
        ),
    ],
)
def test_cross_site_reason(headers: dict[str, str], reason: str | None) -> None:
    assert cross_site_reason(headers_of(**headers)) == reason


# ───────────────────────────── the guard as middleware ─────────────────────────────


class Downstream:
    """A downstream app that records the requests it gets and answers 200 JSON."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        self.calls.append((scope["method"], scope["path"]))
        await JSONResponse({"ok": True})(scope, receive, send)


def guarded() -> tuple[Downstream, httpx.AsyncClient]:
    inner = Downstream()
    transport = httpx.ASGITransport(app=CrossSiteGuard(inner))
    return inner, httpx.AsyncClient(transport=transport, base_url="http://tayyari.example")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_a_cross_site_state_change_is_a_403_json_and_never_reaches_the_app(method: str) -> None:
    inner, client = guarded()
    async with client:
        for forged in (
            {"sec-fetch-site": "cross-site"},
            {"origin": "https://evil.example"},
            {"origin": "null"},
            {"sec-fetch-site": "cross-site", "origin": "https://evil.example"},
        ):
            res = await client.request(method, "/api/analyze", headers=forged)
            assert res.status_code == 403
            assert res.headers["content-type"] == "application/json"
            assert res.json() == {"error": CROSS_SITE_MESSAGE}
    assert inner.calls == []


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_same_origin_and_header_less_state_changes_pass(method: str) -> None:
    inner, client = guarded()
    async with client:
        for allowed in (
            {},  # curl, a server-to-server call
            {
                "origin": "http://tayyari.example"
            },  # a browser on the site (the client's Host is tayyari.example)
            {"origin": "http://tayyari.example", "sec-fetch-site": "same-origin"},
            {"sec-fetch-site": "none"},
        ):
            res = await client.request(method, "/api/chat", headers=allowed)
            assert res.status_code == 200
    assert len(inner.calls) == 4


async def test_reads_are_never_blocked() -> None:
    inner, client = guarded()
    async with client:
        for method in ("GET", "HEAD", "OPTIONS"):
            res = await client.request(method, "/api/health", headers={"sec-fetch-site": "cross-site"})
            assert res.status_code == 200
    assert len(inner.calls) == 3


async def test_only_the_api_is_guarded() -> None:
    _, client = guarded()
    async with client:
        outside = await client.post("/somewhere", headers={"origin": "https://evil.example"})
        lookalike = await client.post("/apiary", headers={"origin": "https://evil.example"})
        root = await client.post("/api", headers={"origin": "https://evil.example"})
        deep = await client.post("/api/profile/x", headers={"origin": "https://evil.example"})
    assert (outside.status_code, lookalike.status_code) == (200, 200)
    assert (root.status_code, deep.status_code) == (403, 403)


@pytest.mark.parametrize("provider", ["razorpay", "stripe"])
async def test_the_payment_webhooks_are_exempt_because_the_provider_is_not_a_browser(provider: str) -> None:
    inner, client = guarded()
    path = f"{WEBHOOK_PREFIX}{provider}"
    async with client:
        for headers in ({}, {"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"}):
            res = await client.post(path, headers=headers)
            assert res.status_code == 200  # the route checks the signature; the guard has nothing to add
    assert inner.calls == [("POST", path)] * 3


async def test_only_the_webhooks_are_exempt_not_the_rest_of_billing() -> None:
    inner, client = guarded()
    async with client:
        for path in ("/api/billing/checkout", "/api/billing/verify", "/api/billing/webhooks", "/api/billing"):
            res = await client.post(path, headers={"origin": "https://evil.example"})
            assert res.status_code == 403, path
    assert inner.calls == []


async def test_the_guard_does_not_touch_other_scopes() -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    guard = CrossSiteGuard(app)
    scope: Scope = {"type": "websocket", "path": "/api/x", "headers": [(b"origin", b"https://evil.example")]}
    await guard(scope, None, None)  # type: ignore[arg-type]
    await guard({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert seen == ["websocket", "lifespan"]


# ───────────────────────────── the inline-script hashes ─────────────────────────────


def test_every_inline_script_gets_its_hash_in_document_order() -> None:
    html = (
        "<!doctype html><html><head>"
        f"<script>{SCRIPT}</script>"
        '<script type="module" crossorigin src="/assets/app.js"></script>'
        "<script type='module'>import './x.js'</script>"
        "</head></html>"
    )
    assert inline_script_hashes(html) == (sha(SCRIPT), sha("import './x.js'"))


def test_the_hash_is_of_the_exact_text_between_the_tags() -> None:
    assert inline_script_hashes("<script> a </script>") == (sha(" a "),)
    assert inline_script_hashes("<script></script>") == (sha(""),)


def test_scripts_with_markup_like_text_inside_are_read_whole() -> None:
    body = "const a = '<b>x</b>'; if (1 < 2 && 3 > 2) { el.innerHTML = '</div>'; }"
    assert inline_script_hashes(f"<script>{body}</script><p>after</p>") == (sha(body),)


def test_windows_line_endings_hash_like_unix_ones() -> None:
    """A browser's HTML parser turns CRLF into LF before it hashes, so the file's line endings must not matter."""
    unix = f"<script>{SCRIPT}</script>"
    windows = unix.replace("\n", "\r\n")
    assert inline_script_hashes(windows) == inline_script_hashes(unix) == (sha(SCRIPT),)


def test_the_same_script_twice_is_listed_once() -> None:
    assert inline_script_hashes(f"<script>{SCRIPT}</script><script>{SCRIPT}</script>") == (sha(SCRIPT),)


def test_a_page_without_inline_scripts_has_no_hashes() -> None:
    assert inline_script_hashes('<script src="/a.js"></script><p>hi</p>') == ()


def test_the_real_index_html_hashes_match_an_independent_extraction() -> None:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        pytest.skip("no web build in backend/static")
    html = index.read_text(encoding="utf-8")
    plain = re.findall(r"<script>(.*?)</script>", html.replace("\r\n", "\n"), flags=re.DOTALL)
    assert plain, "index.html is expected to carry its inline boot script"
    assert inline_script_hashes(html) == tuple(dict.fromkeys(sha(text) for text in plain))


def test_structured_data_is_not_hashed_because_the_browser_never_runs_it() -> None:
    html = (
        '<script type="application/ld+json">{"@type": "WebSite"}</script>'
        "<script>boot()</script>"
        '<script type="module">go()</script>'
    )
    assert inline_script_hashes(html) == (sha("boot()"), sha("go()"))


# ───────────────────────────── the page policy ─────────────────────────────


def test_the_page_policy_has_every_directive() -> None:
    csp = page_csp((sha("x"),))
    assert csp == (
        "default-src 'self'; "
        f"script-src 'self' {RZP} {sha('x')}; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data:; "
        "connect-src 'self' https://api.razorpay.com https://lumberjack.razorpay.com; "
        f"frame-src 'self' {RZP} https://api.razorpay.com; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    assert "unsafe-eval" not in csp


def test_razorpay_is_the_only_third_party_that_may_run_script_or_be_framed() -> None:
    csp = page_csp((sha("x"),))
    directives = {d.split(" ", 1)[0]: d.split(" ")[1:] for d in csp.split("; ")}
    hosts = {
        name: [source for source in sources if source.startswith("https://")]
        for name, sources in directives.items()
    }
    assert hosts["script-src"] == [RZP]
    assert hosts["frame-src"] == [RZP, "https://api.razorpay.com"]
    assert hosts["connect-src"] == ["https://api.razorpay.com", "https://lumberjack.razorpay.com"]
    assert hosts["style-src"] == ["https://fonts.googleapis.com"]  # everything else is as it was
    assert hosts["img-src"] == [] and hosts["form-action"] == [] and hosts["default-src"] == []


def test_a_page_policy_without_hashes_is_self_only() -> None:
    assert f"script-src 'self' {RZP};" in page_csp()


def test_the_policy_follows_the_index_html_file(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text(f"<script>{SCRIPT}</script>", encoding="utf-8")
    csp = PageCsp(index, recheck_s=0)
    assert f"script-src 'self' {RZP} {sha(SCRIPT)};" in csp.policy
    index.write_text("<script>other();</script>", encoding="utf-8")
    assert (
        f"script-src 'self' {RZP} {sha('other();')};" in csp.policy
    )  # a rebuilt page is followed, no restart
    assert sha(SCRIPT) not in csp.policy
    index.unlink()
    assert f"script-src 'self' {RZP};" in csp.policy


def test_a_missing_index_html_means_self_only(tmp_path: Path) -> None:
    assert f"script-src 'self' {RZP};" in PageCsp(tmp_path / "nope.html").policy
    assert f"script-src 'self' {RZP};" in PageCsp(None).policy


def test_the_file_is_only_looked_at_every_recheck_interval(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text(f"<script>{SCRIPT}</script>", encoding="utf-8")
    csp = PageCsp(index, recheck_s=3600)
    index.write_text("<script>changed();</script>", encoding="utf-8")
    assert sha(SCRIPT) in csp.policy  # not re-read yet


def test_an_unreadable_index_html_means_self_only(tmp_path: Path) -> None:
    folder = tmp_path / "index.html"
    folder.mkdir()  # exists, but cannot be read as a file
    assert f"script-src 'self' {RZP};" in PageCsp(folder).policy


# ───────────────────────────── the headers as middleware ─────────────────────────────


def secured(
    response: Response, *, index: Path | None = None, scheme: str = "http"
) -> tuple[httpx.AsyncClient, SecurityHeadersMiddleware]:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await response(scope, receive, send)

    middleware = SecurityHeadersMiddleware(app, index_html=index)
    transport = httpx.ASGITransport(app=middleware)
    return httpx.AsyncClient(transport=transport, base_url=f"{scheme}://tayyari.example"), middleware


async def test_every_response_gets_the_hardening_headers() -> None:
    client, _ = secured(JSONResponse({"a": 1}))
    async with client:
        res = await client.get("/api/health")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["referrer-policy"] == "no-referrer"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["permissions-policy"] == "camera=(), microphone=(self), geolocation=()"
    assert "strict-transport-security" not in res.headers  # plain http


async def test_hsts_only_on_https() -> None:
    client, _ = secured(JSONResponse({}), scheme="https")
    async with client:
        direct = await client.get("/")
    assert direct.headers["strict-transport-security"] == HSTS == "max-age=31536000"

    client, _ = secured(JSONResponse({}))
    async with client:
        proxied = await client.get("/", headers={"x-forwarded-proto": "https"})
        proxied_chain = await client.get("/", headers={"x-forwarded-proto": "https, http"})
        plain = await client.get("/", headers={"x-forwarded-proto": "http"})
    assert proxied.headers["strict-transport-security"] == HSTS
    assert proxied_chain.headers["strict-transport-security"] == HSTS
    assert "strict-transport-security" not in plain.headers


async def test_json_answers_get_the_minimal_policy() -> None:
    client, _ = secured(JSONResponse({"a": 1}))
    async with client:
        res = await client.get("/api/profile/x")
    assert res.headers["content-security-policy"] == API_CSP == "default-src 'none'; frame-ancestors 'none'"


async def test_ndjson_and_assets_get_the_minimal_policy_too() -> None:
    ndjson = Response(b"{}\n", media_type="application/x-ndjson")
    client, _ = secured(ndjson)
    async with client:
        res = await client.post("/api/chat")
    assert res.headers["content-security-policy"] == API_CSP
    asset = PlainTextResponse("console.log(1)", media_type="text/javascript")
    client, _ = secured(asset)
    async with client:
        res = await client.get("/assets/app.js")
    assert res.headers["content-security-policy"] == API_CSP


async def test_html_gets_the_page_policy_with_the_hash_of_the_inline_script(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    html = f"<!doctype html><script>{SCRIPT}</script><div id=root></div>"
    index.write_text(html, encoding="utf-8")
    client, _ = secured(Response(html, media_type="text/html"), index=index)
    async with client:
        res = await client.get("/")
    assert res.headers["content-security-policy"] == page_csp((sha(SCRIPT),))
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
    assert res.headers["x-frame-options"] == "DENY"


async def test_html_without_an_index_file_gets_script_src_self_only() -> None:
    client, _ = secured(Response("<p>hi</p>", media_type="text/html; charset=utf-8"))
    async with client:
        res = await client.get("/")
    assert f"script-src 'self' {RZP};" in res.headers["content-security-policy"]


async def test_a_header_the_route_already_set_is_kept() -> None:
    custom = JSONResponse(
        {}, headers={"Content-Security-Policy": "default-src 'self'", "X-Frame-Options": "SAMEORIGIN"}
    )
    client, _ = secured(custom)
    async with client:
        res = await client.get("/")
    assert res.headers["content-security-policy"] == "default-src 'self'"
    assert res.headers["x-frame-options"] == "SAMEORIGIN"
    assert res.headers["x-content-type-options"] == "nosniff"


async def test_a_streaming_body_is_passed_through_chunk_by_chunk() -> None:
    """The middleware only edits the response start, so it must never hold a stream back."""
    first_sent = asyncio.Event()
    finish = asyncio.Event()

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/x-ndjson")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"n":1}\n', "more_body": True})
        first_sent.set()
        await finish.wait()
        await send({"type": "http.response.body", "body": b'{"n":2}\n', "more_body": False})

    exchange = AsgiExchange(http_scope(path="/api/chat"))
    task = exchange.run(SecurityHeadersMiddleware(app))
    await exchange.wait_for_parts(1)
    assert not task.done()  # the app is still running, and the first line is already out
    assert exchange.body_parts == [b'{"n":1}\n']
    assert exchange.headers["content-security-policy"] == API_CSP
    assert exchange.headers["x-content-type-options"] == "nosniff"
    finish.set()
    await asyncio.wait_for(task, 5)
    assert exchange.body_parts == [b'{"n":1}\n', b'{"n":2}\n']
    assert first_sent.is_set()


async def test_other_scopes_pass_through() -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    middleware = SecurityHeadersMiddleware(app)
    await middleware({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    await middleware({"type": "websocket", "headers": []}, None, None)  # type: ignore[arg-type]
    assert seen == ["lifespan", "websocket"]


async def test_the_headers_are_on_an_early_refusal_when_stacked_outside_the_guard() -> None:
    """The app puts the headers outside the guard, so a 403 carries them too."""
    inner = Downstream()
    stack = SecurityHeadersMiddleware(CrossSiteGuard(inner))
    transport = httpx.ASGITransport(app=stack)
    async with httpx.AsyncClient(transport=transport, base_url="http://tayyari.example") as client:
        res = await client.post("/api/analyze", headers={"origin": "https://evil.example"})
    assert res.status_code == 403
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["content-security-policy"] == API_CSP


def test_request_object_is_not_needed_for_the_unit_helpers() -> None:
    """`cross_site_reason` takes plain `Headers`: a Starlette request's headers work as they are."""
    scope = http_scope(
        path="/api/x", headers=[("origin", "https://evil.example"), ("host", "tayyari.example")]
    )
    request: Any = Request(scope)
    assert cross_site_reason(request.headers) == "origin"


# ───────────────────────────── the /48 hash for the limiter ─────────────────────────────

KEY = b"k" * 32


def test_ipv6_clients_in_one_48_share_a_subnet_hash() -> None:
    a = subnet_hash(KEY, "2001:db8:1:1::5")
    b = subnet_hash(KEY, "2001:db8:1:ffff:aaaa::1")  # another /64 of the same /48
    other = subnet_hash(KEY, "2001:db8:2:1::5")
    assert a is not None
    assert a == b
    assert other not in (None, a)
    assert len(a) == 16
    assert "2001" not in a


def test_the_subnet_hash_is_keyed() -> None:
    assert subnet_hash(KEY, "2001:db8:1::1") != subnet_hash(b"x" * 32, "2001:db8:1::1")


@pytest.mark.parametrize("client", [None, "203.0.113.9", "testclient", ""])
def test_ipv4_and_unknown_clients_have_no_subnet_hash(client: str | None) -> None:
    assert subnet_hash(KEY, client) is None
