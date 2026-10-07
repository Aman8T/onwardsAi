"""Fetching a web page safely: address pinning, redirects, DNS rebinding, size and time limits.

The network is respx (a fake "internet" keyed by the address the client connects to) and the resolver is
`dns`, so a request to a private address cannot leave the machine and shows up as a failed assertion.
"""

from __future__ import annotations

import asyncio
import gzip
import time
import tracemalloc
import zlib
from collections.abc import Callable

import httpx
import pytest

from tayyari.ingest import web as web_module
from tayyari.ingest.web import (
    MAX_BODY_BYTES,
    MAX_REDIRECTS,
    PRIVATE_MSG,
    FetchProblem,
    fetch_text,
    ingest_web,
    read_capped,
)
from tests.unit.ingest.helpers import (
    PUBLIC_IP,
    PUBLIC_IP_2,
    PUBLIC_IP_3,
    PUBLIC_IP_4,
    ChunkedStream,
    FakeDns,
    FakeWeb,
)

HTML_HEADERS = {"content-type": "text/html; charset=utf-8"}
SIMPLE_PAGE = "<html><head><title>Hello</title></head><body><p>Some page text.</p></body></html>"
ACCEPT = "text/html"

Handler = Callable[[httpx.Request], httpx.Response]


def page(html: str = SIMPLE_PAGE, status: int = 200, headers: dict[str, str] | None = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={**HTML_HEADERS, **(headers or {})}, content=html.encode())

    return handler


def redirect(to: str, status: int = 302) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"location": to})

    return handler


def gzip_stream(raw_size: int, *, wbits: int = 31, level: int = 9) -> bytes:
    """`raw_size` zero bytes, compressed (gzip by default; wbits 15 is zlib deflate, -15 raw deflate)."""
    compressor = zlib.compressobj(level, zlib.DEFLATED, wbits)
    block = b"\0" * (1 << 20)
    parts = [compressor.compress(block) for _ in range(raw_size >> 20)]
    parts.append(compressor.flush())
    return b"".join(parts)


def in_chunks(data: bytes, size: int = 65536) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)]


# ----------------------------------------------------------------------------- the address is pinned


async def test_the_request_goes_to_the_validated_address_with_the_original_host_and_sni(
    dns: FakeDns, web: FakeWeb
) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, page())
    fetched = await fetch_text("https://example.test/a/b?x=1", MAX_BODY_BYTES, ACCEPT)
    [request] = web.requests
    assert request.url.host == PUBLIC_IP  # never the name: the client cannot resolve it a second time
    assert request.url.scheme == "https"
    assert request.url.path == "/a/b"
    assert request.url.query == b"x=1"
    assert request.headers["host"] == "example.test"
    assert request.extensions["sni_hostname"] == "example.test"  # certificate checked against the name
    assert fetched.status == 200
    assert fetched.url == "https://example.test/a/b?x=1"
    assert dns.lookups("example.test") == 1


async def test_plain_http_sends_no_sni_and_keeps_a_custom_port_in_the_host_header(
    dns: FakeDns, web: FakeWeb
) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, page())
    await fetch_text("http://example.test:8080/x", MAX_BODY_BYTES, ACCEPT)
    [request] = web.requests
    assert request.url.port == 8080
    assert request.headers["host"] == "example.test:8080"
    assert "sni_hostname" not in request.extensions


async def test_an_ip_literal_is_contacted_directly_without_a_lookup(no_dns: FakeDns, web: FakeWeb) -> None:
    web.serve(PUBLIC_IP, page())
    await fetch_text(f"https://{PUBLIC_IP}/", MAX_BODY_BYTES, ACCEPT)
    [request] = web.requests
    assert request.headers["host"] == PUBLIC_IP
    assert "sni_hostname" not in request.extensions
    assert no_dns.calls == []


async def test_requests_ask_for_gzip_only_and_carry_no_credentials(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, page())
    await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    headers = web.requests[0].headers
    assert headers["accept-encoding"] == "gzip, deflate"
    assert headers["accept"] == ACCEPT
    assert "authorization" not in headers
    assert "cookie" not in headers


async def test_an_unreachable_first_address_falls_through_to_the_next_validated_one(
    dns: FakeDns, web: FakeWeb
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    dns.answer("example.test", [PUBLIC_IP, PUBLIC_IP_2])
    web.serve(PUBLIC_IP, refuse)
    web.serve(PUBLIC_IP_2, page())
    fetched = await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    assert fetched.status == 200
    assert [r.url.host for r in web.requests] == [PUBLIC_IP, PUBLIC_IP_2]


async def test_only_the_first_three_addresses_are_tried(dns: FakeDns, web: FakeWeb) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    addresses = [PUBLIC_IP, PUBLIC_IP_2, PUBLIC_IP_3, PUBLIC_IP_4]
    dns.answer("example.test", addresses)
    for address in addresses:
        web.serve(address, refuse)
    with pytest.raises(httpx.ConnectError):
        await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    assert [r.url.host for r in web.requests] == addresses[:3]


# ----------------------------------------------------------------------------- redirects


async def test_six_redirects_are_followed_and_every_hop_is_resolved_again(dns: FakeDns, web: FakeWeb) -> None:
    def chain(request: httpx.Request) -> httpx.Response:
        n = int(request.url.path.strip("/") or 0)
        if n < MAX_REDIRECTS:
            return httpx.Response(302, headers={"location": f"/{n + 1}"})
        return page()(request)

    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, chain)
    fetched = await fetch_text("https://example.test/0", MAX_BODY_BYTES, ACCEPT)
    assert fetched.status == 200
    assert fetched.url == f"https://example.test/{MAX_REDIRECTS}"
    assert len(web.requests) == MAX_REDIRECTS + 1 == 7
    assert dns.lookups("example.test") == 7  # re-resolved and re-checked on every hop


async def test_a_seventh_redirect_is_refused_and_not_followed(dns: FakeDns, web: FakeWeb) -> None:
    def forever(request: httpx.Request) -> httpx.Response:
        n = int(request.url.path.strip("/") or 0)
        return httpx.Response(302, headers={"location": f"/{n + 1}"})

    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, forever)
    with pytest.raises(FetchProblem, match="too many times"):
        await fetch_text("https://example.test/0", MAX_BODY_BYTES, ACCEPT)
    assert len(web.requests) == MAX_REDIRECTS + 1  # the last redirect target is never requested


@pytest.mark.parametrize(
    ("location", "expected_path"),
    [
        ("/next", "/next"),
        ("next", "/dir/next"),
        ("../up", "/up"),
        ("?q=1", "/dir/start"),
        ("https://example.test/abs", "/abs"),
        ("//example.test/scheme-relative", "/scheme-relative"),
    ],
)
async def test_relative_and_absolute_redirects_are_resolved_against_the_current_url(
    location: str, expected_path: str, dns: FakeDns, web: FakeWeb
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/dir/start" and not request.url.query:
            return httpx.Response(302, headers={"location": location})
        return page()(request)

    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, handler)
    await fetch_text("https://example.test/dir/start", MAX_BODY_BYTES, ACCEPT)
    assert web.requests[-1].url.path == expected_path


async def test_a_redirect_to_another_site_uses_that_sites_own_validated_address(
    dns: FakeDns, web: FakeWeb
) -> None:
    dns.answer("a.test", [PUBLIC_IP])
    dns.answer("b.test", [PUBLIC_IP_2])
    web.serve(PUBLIC_IP, redirect("https://b.test/landing"))
    web.serve(PUBLIC_IP_2, page())
    fetched = await fetch_text("https://a.test/", MAX_BODY_BYTES, ACCEPT)
    assert fetched.url == "https://b.test/landing"
    assert [(r.url.host, r.headers["host"]) for r in web.requests] == [
        (PUBLIC_IP, "a.test"),
        (PUBLIC_IP_2, "b.test"),
    ]


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:8080/admin",
        "http://localhost:8080/admin",
        "http://[::1]/",
        "http://[::ffff:7f00:1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://10.0.0.1/",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.100.100.200/latest/meta-data/",
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://printer.local/",
        "http://nas.lan/",
        "http://router.home.arpa/",
        "http://app.localhost/",
        "http://0x7f.0.0.1/",
        "http://127.1/",
        "http://0177.0.0.1/",
        "http://2130706433/",  # decimal form: not even a valid link, so refused one step earlier
        "//internal.lan/x",
    ],
)
async def test_a_redirect_to_a_private_address_is_refused_and_never_contacted(
    target: str, dns: FakeDns, web: FakeWeb
) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, redirect(target))
    with pytest.raises(FetchProblem):
        await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    assert len(web.requests) == 1  # only the public first hop; the private target got no request
    assert dns.calls == ["example.test"]  # not even a lookup for the private target


@pytest.mark.parametrize(
    "target",
    [
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_PING",
        "ftp://example.test/x",
        "javascript:alert(1)",
        "data:text/html,x",
        "http://user:secret@example.test/",
        "https://example.test:99999/",
    ],
)
async def test_a_redirect_to_another_scheme_or_with_credentials_is_refused(
    target: str, dns: FakeDns, web: FakeWeb
) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, redirect(target))
    with pytest.raises(FetchProblem, match="cannot be read"):
        await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    assert len(web.requests) == 1


async def test_a_redirect_to_a_name_that_resolves_privately_is_refused(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("good.test", [PUBLIC_IP])
    dns.answer("evil.test", ["127.0.0.1"])  # e.g. 127.0.0.1.nip.io
    web.serve(PUBLIC_IP, redirect("https://evil.test/"))
    with pytest.raises(FetchProblem) as exc:
        await fetch_text("https://good.test/", MAX_BODY_BYTES, ACCEPT)
    assert str(exc.value) == PRIVATE_MSG
    assert len(web.requests) == 1


async def test_a_redirect_without_a_location_is_an_ordinary_response(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, lambda request: httpx.Response(304))
    fetched = await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    assert fetched.status == 304
    assert len(web.requests) == 1


# ----------------------------------------------------------------------------- DNS rebinding


async def test_rebinding_between_hops_is_caught_by_the_per_hop_lookup(dns: FakeDns, web: FakeWeb) -> None:
    """The name answers with a public address first and a private one on the next lookup."""
    dns.answer("rebind.test", [PUBLIC_IP], ["127.0.0.1"])
    web.serve(PUBLIC_IP, redirect("https://rebind.test/second"))
    with pytest.raises(FetchProblem) as exc:
        await fetch_text("https://rebind.test/first", MAX_BODY_BYTES, ACCEPT)
    assert str(exc.value) == PRIVATE_MSG
    assert dns.lookups("rebind.test") == 2  # the second hop looked the name up again
    assert len(web.requests) == 1


async def test_rebinding_after_the_check_changes_nothing_because_the_checked_address_is_used(
    dns: FakeDns, web: FakeWeb
) -> None:
    """Whatever the name resolves to later, the connection already goes to the address that was validated."""
    dns.answer("rebind.test", [PUBLIC_IP], ["127.0.0.1"])
    web.serve(PUBLIC_IP, page())
    fetched = await fetch_text("https://rebind.test/", MAX_BODY_BYTES, ACCEPT)
    assert fetched.status == 200
    assert [r.url.host for r in web.requests] == [
        PUBLIC_IP
    ]  # an IP, so the HTTP client never resolves the name
    assert dns.lookups("rebind.test") == 1


@pytest.mark.parametrize(
    "answers",
    [
        [PUBLIC_IP, "10.0.0.5"],
        ["10.0.0.5", PUBLIC_IP],
        [PUBLIC_IP, "169.254.169.254"],
        ["::1"],
        [PUBLIC_IP, "::ffff:127.0.0.1"],
        ["2606:4700:4700::1111", "fd00::1"],
    ],
)
async def test_one_private_answer_among_several_blocks_the_whole_name(
    answers: list[str], dns: FakeDns, web: FakeWeb
) -> None:
    dns.answer("multi.test", answers)
    web.serve(PUBLIC_IP, page())
    with pytest.raises(FetchProblem) as exc:
        await fetch_text("https://multi.test/", MAX_BODY_BYTES, ACCEPT)
    assert str(exc.value) == PRIVATE_MSG
    assert web.requests == []


# ----------------------------------------------------------------------------- body cap


async def test_a_body_over_the_cap_is_cut_and_flagged(dns: FakeDns, web: FakeWeb) -> None:
    stream = ChunkedStream([b"a" * 65536] * 80)  # 5 MiB
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, lambda request: httpx.Response(200, headers=HTML_HEADERS, stream=stream))
    fetched = await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    assert fetched.truncated is True
    assert len(fetched.text) == MAX_BODY_BYTES == 2 * 1024 * 1024
    assert stream.sent < 40  # reading stopped near the cap instead of draining the rest of the body


@pytest.mark.parametrize(("size", "cut"), [(999, False), (1000, False), (1001, True), (5000, True)])
async def test_the_cap_is_exact(size: int, cut: bool, dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, lambda request: httpx.Response(200, headers=HTML_HEADERS, content=b"x" * size))
    fetched = await fetch_text("https://example.test/", 1000, ACCEPT)
    assert fetched.truncated is cut
    assert len(fetched.text) == min(size, 1000)


async def test_a_dishonest_content_length_does_not_widen_the_cap(dns: FakeDns, web: FakeWeb) -> None:
    stream = ChunkedStream([b"b" * 4096] * 8)
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(
        PUBLIC_IP,
        lambda request: httpx.Response(200, headers={**HTML_HEADERS, "content-length": "10"}, stream=stream),
    )
    fetched = await fetch_text("https://example.test/", 5000, ACCEPT)
    assert fetched.truncated is True
    assert len(fetched.text) == 5000


# ----------------------------------------------------------------------------- compression


def response_with(encoding: str, chunks: list[bytes]) -> httpx.Response:
    return httpx.Response(200, headers={"content-encoding": encoding}, stream=ChunkedStream(chunks))


@pytest.mark.parametrize(
    ("encoding", "wbits"), [("gzip", 31), ("x-gzip", 31), ("GZIP", 31), ("deflate", 15), ("deflate", -15)]
)
async def test_a_decompression_bomb_is_cut_at_the_cap_without_the_memory_blowing_up(
    encoding: str, wbits: int
) -> None:
    """256 MiB of zeros in about 250 KB: httpx alone would inflate each 64 KB chunk to tens of MB."""
    bomb = gzip_stream(256 << 20, wbits=wbits)
    assert len(bomb) < 2_000_000
    tracemalloc.start()
    started = time.perf_counter()
    try:
        body, truncated = await read_capped(response_with(encoding, in_chunks(bomb)), MAX_BODY_BYTES)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert truncated is True
    assert len(body) == MAX_BODY_BYTES
    assert peak < 24 * 1024 * 1024  # a few times the 2 MB cap, nowhere near the 150 MB httpx used to need
    assert time.perf_counter() - started < 5


async def test_a_bomb_in_a_single_huge_chunk_is_bounded_too() -> None:
    bomb = gzip_stream(128 << 20)
    tracemalloc.start()
    try:
        body, truncated = await read_capped(response_with("gzip", [bomb]), MAX_BODY_BYTES)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert (len(body), truncated) == (MAX_BODY_BYTES, True)
    assert peak < 24 * 1024 * 1024


async def test_a_bomb_fetched_end_to_end_comes_back_truncated(dns: FakeDns, web: FakeWeb) -> None:
    bomb = gzip_stream(64 << 20)
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(
        PUBLIC_IP,
        lambda request: httpx.Response(
            200, headers={**HTML_HEADERS, "content-encoding": "gzip"}, stream=ChunkedStream(in_chunks(bomb))
        ),
    )
    fetched = await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    assert fetched.truncated is True
    assert len(fetched.text) == MAX_BODY_BYTES


@pytest.mark.parametrize("wbits", [31, 15, -15])
async def test_compressed_bodies_decode_across_chunk_boundaries(wbits: int) -> None:
    text = ("Hello, résumé. " * 500).encode()
    compressor = zlib.compressobj(6, zlib.DEFLATED, wbits)
    packed = compressor.compress(text) + compressor.flush()
    encoding = "gzip" if wbits == 31 else "deflate"
    body, truncated = await read_capped(response_with(encoding, in_chunks(packed, 7)), 1_000_000)
    assert (body, truncated) == (text, False)


async def test_a_compressed_body_of_exactly_the_cap_is_not_flagged() -> None:
    packed = gzip.compress(b"z" * 1000)
    assert await read_capped(response_with("gzip", [packed]), 1000) == (b"z" * 1000, False)
    assert await read_capped(response_with("gzip", [gzip.compress(b"z" * 1001)]), 1000) == (b"z" * 1000, True)


async def test_a_truncated_gzip_stream_returns_what_arrived() -> None:
    text = "".join(f"line {i} of the resume\n" for i in range(3000)).encode()
    packed = gzip.compress(text)
    body, truncated = await read_capped(response_with("gzip", [packed[: len(packed) // 2]]), 1_000_000)
    assert truncated is False
    assert 0 < len(body) < len(text)
    assert body.startswith(b"line 0 of the resume\nline 1 of the resume\n")


async def test_identity_and_missing_encoding_pass_through() -> None:
    for header in ("identity", ""):
        res = httpx.Response(
            200, headers={"content-encoding": header}, stream=ChunkedStream([b"plain ", b"text"])
        )
        assert await read_capped(res, 100) == (b"plain text", False)


async def test_a_corrupt_gzip_body_raises_a_decoding_error() -> None:
    with pytest.raises(httpx.DecodingError):
        await read_capped(response_with("gzip", [b"this is not gzip data at all"]), 1000)


@pytest.mark.parametrize("encoding", ["gzip, gzip", "br", "zstd", "gzip, br", "compress"])
async def test_stacked_or_unsupported_encodings_are_refused(encoding: str) -> None:
    with pytest.raises(httpx.DecodingError, match="Unsupported"):
        await read_capped(response_with(encoding, [gzip.compress(b"x")]), 1000)


async def test_an_already_loaded_response_is_still_read(dns: FakeDns, web: FakeWeb) -> None:
    """A response whose body was loaded (and decoded) already, as a mock built with `content=` is."""
    packed = gzip.compress(SIMPLE_PAGE.encode())
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(
        PUBLIC_IP,
        lambda request: httpx.Response(
            200, headers={**HTML_HEADERS, "content-encoding": "gzip"}, content=packed
        ),
    )
    fetched = await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    assert fetched.text == SIMPLE_PAGE


# ----------------------------------------------------------------------------- time limits


@pytest.fixture
def short_budget(monkeypatch: pytest.MonkeyPatch) -> float:
    monkeypatch.setattr(web_module, "REQUEST_TIMEOUT", 0.6)
    return 0.6


async def test_a_slow_drip_body_is_cut_by_the_total_timeout(
    dns: FakeDns, web: FakeWeb, short_budget: float
) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(
        PUBLIC_IP,
        lambda request: httpx.Response(
            200, headers=HTML_HEADERS, stream=ChunkedStream([b"x" * 10] * 50, delay=0.1)
        ),
    )
    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    assert time.perf_counter() - started < short_budget + 1.0


async def test_a_server_that_never_answers_is_cut_by_the_total_timeout(
    dns: FakeDns, web: FakeWeb, short_budget: float
) -> None:
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, hang)
    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    assert time.perf_counter() - started < short_budget + 1.0


async def test_the_budget_covers_every_redirect_hop_together(
    dns: FakeDns, web: FakeWeb, short_budget: float
) -> None:
    async def slow_hop(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.15)
        n = int(request.url.path.strip("/") or 0)
        return httpx.Response(302, headers={"location": f"/{n + 1}"})

    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, slow_hop)
    started = time.perf_counter()
    with pytest.raises(TimeoutError):  # six hops of 0.15 s are 0.9 s, over the 0.6 s budget
        await fetch_text("https://example.test/0", MAX_BODY_BYTES, ACCEPT)
    assert time.perf_counter() - started < short_budget + 1.0
    assert len(web.requests) < MAX_REDIRECTS + 1


async def test_a_dropped_connection_is_retried_once(dns: FakeDns, web: FakeWeb) -> None:
    attempts = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.RemoteProtocolError("server disconnected")
        return page()(request)

    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, flaky)
    fetched = await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    assert fetched.status == 200
    assert attempts == 2


async def test_the_retry_shares_the_one_budget_instead_of_starting_a_fresh_one(
    dns: FakeDns, web: FakeWeb, short_budget: float
) -> None:
    """A flaky site used to hold an analysis for up to twice the documented limit."""
    attempts = 0

    async def drop_then_hang(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await asyncio.sleep(0.1)
            raise httpx.ReadError("connection reset")
        await asyncio.sleep(30)
        return httpx.Response(200)

    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, drop_then_hang)
    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    elapsed = time.perf_counter() - started
    assert attempts == 2
    assert elapsed < short_budget + 0.25  # 0.1 + the 0.4 s pause + a fresh 0.6 budget would have taken 1.1 s


@pytest.mark.parametrize("status", [404, 429, 500, 503])
async def test_http_errors_are_returned_not_retried(status: int, dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, page("oops", status=status))
    fetched = await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    assert fetched.status == status
    assert len(web.requests) == 1


# ----------------------------------------------------------------------------- ingest_web on top of the guard


async def read(url: str, kind: str = "link") -> tuple[object, list[str]]:
    lines: list[str] = []
    doc = await ingest_web(url, kind, "Example", "Web page", lines.append)
    return doc, lines


PRIVATE_URLS = [
    "http://127.0.0.1/",
    "http://127.0.0.1:8080/admin",
    "http://[::1]/",
    "http://[::ffff:7f00:1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://10.0.0.1/",
    "http://192.168.1.1/",
    "http://172.16.5.4/",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.100.100.200/",
    "http://0.0.0.0/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://printer.local/",
    "http://nas.lan/",
    "http://router.home.arpa/",
    "http://app.localhost/",
    "http://127.1/",
    "http://127.0.1/",
    "http://0x7f.0.0.1/",
    "http://0177.0.0.1/",
    "http://0x7f.1/",
]


@pytest.mark.parametrize("url", PRIVATE_URLS)
async def test_a_private_link_is_refused_before_anything_is_looked_up_or_sent(
    url: str, no_dns: FakeDns, web: FakeWeb
) -> None:
    doc, _ = await read(url)
    assert doc.ok is False
    assert doc.error == PRIVATE_MSG
    assert no_dns.calls == []
    assert web.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",  # a single decimal number is not a dotted host name
        "http://localhost/",  # neither is a single label: only real, dotted host names are accepted
        "https://localhost:3000/",
        "http://user:secret@example.test/",
        "file:///etc/passwd",
        "ftp://example.test/",
        "javascript:alert(1)",
        "gopher://127.0.0.1:6379/",
        "http://127.0.0.1\\@example.test/",
        "http://example.test/a b",
        "",
    ],
)
async def test_malformed_or_dangerous_links_are_rejected_without_network_use(
    url: str, no_dns: FakeDns, web: FakeWeb
) -> None:
    doc, _ = await read(url)
    assert doc.ok is False
    assert doc.error == "That does not look like a valid web link."
    assert no_dns.calls == []
    assert web.requests == []


async def test_a_name_that_resolves_to_a_private_address_is_refused(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("127.0.0.1.nip.io", ["127.0.0.1"])
    doc, _ = await read("http://127.0.0.1.nip.io/")
    assert doc.error == PRIVATE_MSG
    assert web.requests == []


async def test_a_redirect_to_a_private_address_ends_as_a_failed_source(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, redirect("http://169.254.169.254/latest/meta-data/iam/security-credentials/"))
    doc, lines = await read("https://example.test/")
    assert doc.ok is False
    assert doc.error == PRIVATE_MSG
    assert len(web.requests) == 1
    assert lines == []


async def test_too_many_redirects_end_as_a_failed_source(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, redirect("/again"))
    doc, _ = await read("https://example.test/")
    assert doc.ok is False
    assert doc.error == "That link redirects too many times."
    assert len(web.requests) == MAX_REDIRECTS + 1


async def test_a_decoding_failure_is_explained(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(
        PUBLIC_IP,
        lambda request: httpx.Response(
            200, headers={**HTML_HEADERS, "content-encoding": "gzip"}, stream=ChunkedStream([b"not gzip"])
        ),
    )
    doc, _ = await read("https://example.test/")
    assert doc.ok is False
    assert doc.error == "That site sent a response that could not be decoded."


async def test_a_slow_site_is_reported_as_too_slow(dns: FakeDns, web: FakeWeb, short_budget: float) -> None:
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, hang)
    doc, _ = await read("https://example.test/")
    assert doc.ok is False
    assert doc.error.startswith("The page took too long to respond")


async def test_an_unknown_host_is_reported_as_not_found(dns: FakeDns, web: FakeWeb) -> None:
    dns.missing("nope.test")
    doc, _ = await read("https://nope.test/")
    assert doc.error == "Could not find that site. Check the link for typos."


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (404, "That page was not found (HTTP 404). Check the link."),
        (403, "That site blocks automated reading or needs a login (HTTP 403)."),
        (429, "That site is rate limiting requests. Try again in a few minutes."),
        (500, "That site returned a server error (HTTP 500)."),
        (418, "That page could not be opened (HTTP 418)."),
    ],
)
async def test_http_statuses_become_readable_errors(
    status: int, message: str, dns: FakeDns, web: FakeWeb
) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, page("nope", status=status))
    doc, _ = await read("https://example.test/")
    assert doc.ok is False
    assert doc.error == message


async def test_linkedin_failures_say_linkedin_blocks_readers(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("www.linkedin.com", [PUBLIC_IP])
    web.serve(PUBLIC_IP, page("denied", status=999))
    doc, _ = await read("https://www.linkedin.com/in/someone", "linkedin")
    assert doc.error == web_module.LINKEDIN_BLOCKED


async def test_a_normal_page_is_read(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, page())
    doc, lines = await read("https://example.test/")
    assert doc.ok is True
    assert doc.text.startswith("# Hello\n\nURL: https://example.test/")
    assert "Some page text." in doc.text
    assert doc.meta["status"] == 200
    assert lines[0] == "Opened example.test"


# ----------------------------------------------------------------------------- logging stays free of page content


async def test_logs_name_the_host_and_the_outcome_but_never_page_text_or_url_secrets(
    dns: FakeDns, web: FakeWeb, record_logs
) -> None:
    secret_text = "CONFIDENTIAL-PAGE-TEXT-7731"
    html = f"<html><head><title>Portfolio</title></head><body><p>{secret_text} and some more words.</p></body></html>"
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, page(html))
    doc, _ = await read("https://example.test/me?token=SECRET-TOKEN-4242")
    assert doc.ok is True
    [event] = record_logs.named("web_ingested")
    assert event["host"] == "example.test"
    assert event["status"] == 200
    flattened = " ".join(str(v) for e in record_logs.events for v in e.values())
    assert secret_text not in flattened
    assert "SECRET-TOKEN-4242" not in flattened


async def test_failures_are_logged_with_the_reason_and_host_only(
    dns: FakeDns, web: FakeWeb, record_logs
) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, page("gone", status=404))
    await read("https://example.test/private/path?token=SECRET-TOKEN-4242")
    [event] = record_logs.named("web_ingest_failed")
    assert event["host"] == "example.test"
    assert event["reason"].startswith("That page was not found")
    flattened = " ".join(str(v) for v in event.values())
    assert "SECRET-TOKEN-4242" not in flattened
    assert "/private/path" not in flattened
