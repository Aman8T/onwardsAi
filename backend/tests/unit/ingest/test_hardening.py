"""Security regressions for ingest: the second hardening round (review findings 2, 3, 11, 13 and 14).

Each test is a reviewer's reproduction turned into a check with a tight time or memory bound:

- a page that claims `charset=punycode` froze the process for minutes (2),
- a 29 MB DOCX of paragraphs and an 8 MB PDF content stream cost tens of seconds and hundreds of MB (3),
- a resolver that never answers filled the event loop's shared thread pool (11),
- any port on a public host could be probed through the error message (13),
- a NUL character in a resume, notes, page or README made Postgres refuse the row (14).

Nothing here touches the real network. Invisible characters are written with chr() on purpose.
"""

from __future__ import annotations

import asyncio
import io
import random
import socket
import threading
import time
import tracemalloc
import zipfile
import zlib
from collections.abc import Callable, Iterator

import docx
import httpx
import pypdf
import pytest

from tayyari.core import limits
from tayyari.ingest import (
    PlannedSource,
    SourceDoc,
    ingest_notes,
    ingest_planned,
    ingest_resume,
    parse_resume,
    strip_nul,
)
from tayyari.ingest import resume as resume_module
from tayyari.ingest import web as web_module
from tayyari.ingest.resume import (
    MAX_DOCX_ELEMENTS,
    MAX_DOCX_XML,
    MAX_PDF_FORM_INVOCATIONS,
    TOO_COMPLEX_MSG,
    ResumeError,
    _check_docx_cost,
    _count_paragraphs_and_runs,
    _extract_docx,
    _extract_pdf,
)
from tayyari.ingest.web import (
    DNS_FAILURE_TTL,
    DNS_WORKERS,
    MAX_BODY_BYTES,
    NOT_FOUND_MSG,
    UNREACHABLE_MSG,
    FetchProblem,
    _decode_body,
    fetch_text,
    ingest_web,
    resolve_public,
)
from tests.unit.ingest.helpers import (
    BACKSLASH,
    PUBLIC_IP,
    FakeDns,
    FakeWeb,
    _assemble_pdf,
    make_pdf,
    make_pdf_with_streams,
)

NUL = chr(0)
REPLACEMENT = chr(0xFFFD)
LEFT_QUOTE, RIGHT_QUOTE = chr(0x201C), chr(0x201D)
ACCEPT = "text/html"
HTML_HEADERS = {"content-type": "text/html; charset=utf-8"}

Handler = Callable[[httpx.Request], httpx.Response]


def page(html: str | bytes, headers: dict[str, str] | None = None, status: int = 200) -> Handler:
    body = html if isinstance(html, bytes) else html.encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={**HTML_HEADERS, **(headers or {})}, content=body)

    return handler


def redirect(to: str) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": to})

    return handler


async def read(url: str, kind: str = "link") -> tuple[SourceDoc, list[str]]:
    lines: list[str] = []
    doc = await ingest_web(url, kind, "Example", "Web page", lines.append)
    return doc, lines


async def watch_event_loop(stalls: list[float]) -> None:
    """Records how late each 20 ms tick ran: a blocked event loop shows up as one big number."""
    last = time.perf_counter()
    while True:
        await asyncio.sleep(0.02)
        now = time.perf_counter()
        stalls.append(now - last - 0.02)
        last = now


# ----------------------------------------------------------------------------- 2: page character sets


def test_a_punycode_label_is_not_honoured_and_costs_nothing() -> None:
    """The quadratic `punycode` codec took 743 s for a 2 MB body."""
    body = b"a" * 2_000_000
    started = time.perf_counter()
    text = _decode_body(body, "text/html; charset=punycode")
    assert time.perf_counter() - started < 0.2
    assert text == "a" * 2_000_000


async def test_a_punycode_page_costs_milliseconds_and_never_stalls_the_event_loop(
    dns: FakeDns, web: FakeWeb
) -> None:
    """The reviewer's page: 640 KB, `charset=punycode`. It stalled the loop for 12.5 s."""
    html = b"<html><head><title>Portfolio</title></head><body><p>" + b"a" * 640_000 + b"</p></body></html>"
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, page(html, {"content-type": "text/html; charset=punycode"}))
    stalls: list[float] = []
    ticker = asyncio.create_task(watch_event_loop(stalls))
    await asyncio.sleep(0.1)
    started = time.perf_counter()
    try:
        fetched = await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
        doc, _ = await read("https://example.test/")
    finally:
        ticker.cancel()
    assert time.perf_counter() - started < 2.0
    assert max(stalls) < 0.25
    assert fetched.text == html.decode()
    assert doc.ok is True


def test_every_character_set_that_is_honoured_is_a_fast_one() -> None:
    assert set(web_module._CHARSETS.values()) <= {"utf-8", "latin-1", "cp1252"}


@pytest.mark.parametrize(
    ("label", "raw", "expected"),
    [
        ("utf-8", "café".encode(), "café"),
        ("UTF-8", "café".encode(), "café"),
        ('"utf-8"', "café".encode(), "café"),
        ("utf8", "café".encode(), "café"),
        ("us-ascii", b"plain text", "plain text"),
        ("US-ASCII", "café".encode(), "café"),  # UTF-8 mislabelled as ASCII reads as UTF-8
        ("iso-8859-1", b"caf" + bytes([0xE9]), "café"),
        ("ISO_8859-1", b"caf" + bytes([0xE9]), "café"),
        ("latin1", b"caf" + bytes([0xE9]), "café"),
        ("l1", b"caf" + bytes([0xE9]), "café"),
        ("windows-1252", bytes([0x93]) + b"hi" + bytes([0x94]), LEFT_QUOTE + "hi" + RIGHT_QUOTE),
        ("cp1252", bytes([0x93]) + b"hi" + bytes([0x94]), LEFT_QUOTE + "hi" + RIGHT_QUOTE),
    ],
)
def test_the_listed_character_sets_are_honoured(label: str, raw: bytes, expected: str) -> None:
    assert _decode_body(raw, f"text/html; charset={label}") == expected


def test_utf16_is_read_only_with_a_byte_order_mark() -> None:
    text = "héllo wörld"
    little = text.encode("utf-16")  # a BOM, then the machine's byte order
    big = bytes([0xFE, 0xFF]) + text.encode("utf-16-be")
    assert _decode_body(little, "text/html; charset=utf-16") == text
    assert _decode_body(little, "text/html; charset=UTF-16LE") == text
    assert _decode_body(big, "text/html; charset=utf-16") == text
    assert _decode_body(little, "text/html") == text  # no label at all: the byte order mark decides
    no_bom = "héllo".encode("utf-16-le")
    assert (
        _decode_body(no_bom, "text/html; charset=utf-16") == "h" + REPLACEMENT + "llo"
    )  # UTF-8, NULs dropped


def test_a_byte_order_mark_does_not_override_a_label_that_names_another_set() -> None:
    little = "hello".encode("utf-16")  # FF FE, then "h", NUL, "e", NUL...
    assert _decode_body(little, "text/html; charset=latin-1") == chr(0xFF) + chr(0xFE) + "hello"


@pytest.mark.parametrize(
    "label", ["punycode", "utf-7", "idna", "koi8-r", "shift_jis", "gb2312", "undefined", "nonsense", "x" * 40]
)
def test_a_label_off_the_list_reads_as_utf8_with_replacement_characters(label: str) -> None:
    raw = bytes([0xC1]) + b"+AGEAYgBj-"  # koi8-r would give a Cyrillic letter, utf-7 would decode the rest
    assert _decode_body(raw, f"text/html; charset={label}") == REPLACEMENT + "+AGEAYgBj-"


def test_the_meta_tag_goes_through_the_same_list() -> None:
    quadratic = b'<html><head><meta charset="punycode"></head><body>' + b"a" * 600_000 + b"</body></html>"
    started = time.perf_counter()
    assert _decode_body(quadratic, "text/html") == quadratic.decode()
    assert time.perf_counter() - started < 0.2
    smart = b'<html><head><meta charset="windows-1252"></head><body>' + bytes([0x93]) + b"</body></html>"
    assert LEFT_QUOTE in _decode_body(smart, "text/html")
    equiv = b'<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">' + bytes([0xE9])
    assert _decode_body(equiv, "text/html").endswith("é")
    assert _decode_body(smart, "application/json") != _decode_body(
        smart, "text/html"
    )  # only markup is sniffed


def test_nul_characters_are_removed_from_a_page() -> None:
    assert _decode_body(b"a" + bytes(2) + b"b", "text/plain; charset=utf-8") == "ab"


# ----------------------------------------------------------------------------- 11: DNS


class StuckResolver:
    """A `socket.getaddrinfo` that never answers (until released) and records who ran it."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started: list[str] = []
        self.thread_names: set[str] = set()
        self._lock = threading.Lock()

    def __call__(self, host: str, port: object, *args: object, **kwargs: object) -> list[tuple[object, ...]]:
        with self._lock:
            self.started.append(host)
            self.thread_names.add(threading.current_thread().name)
        self.release.wait(15)
        raise socket.gaierror(socket.EAI_AGAIN, "temporary failure in name resolution")


@pytest.fixture
def stuck(monkeypatch: pytest.MonkeyPatch) -> Iterator[StuckResolver]:
    """A resolver that hangs, a 0.3 s lookup timeout, and a guarantee that every thread is let go afterwards."""
    resolver = StuckResolver()
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    monkeypatch.setattr(web_module, "DNS_TIMEOUT", 0.3)
    yield resolver
    resolver.release.set()


async def test_stuck_resolvers_cannot_starve_the_shared_thread_pool(stuck: StuckResolver) -> None:
    """The reviewer: 40 fetches to a name server that never answers made asyncio.to_thread take 24 s."""
    started = time.perf_counter()
    lookups = [asyncio.create_task(resolve_public(f"h{i}.stuck.test")) for i in range(40)]
    await asyncio.sleep(0.1)
    probe = time.perf_counter()
    assert await asyncio.to_thread(lambda: 42) == 42  # the default pool is untouched
    assert time.perf_counter() - probe < 0.25
    results = await asyncio.gather(*lookups, return_exceptions=True)
    assert time.perf_counter() - started < 2.0  # every wait ended at the 0.3 s lookup timeout
    assert all(isinstance(r, FetchProblem) and str(r) == UNREACHABLE_MSG for r in results)
    assert len(stuck.started) <= DNS_WORKERS  # the lookups queued behind them were cancelled, not run
    assert stuck.thread_names
    assert all(name.startswith("tayyari-dns") for name in stuck.thread_names)


async def test_a_timed_out_host_is_refused_at_once_without_another_thread(stuck: StuckResolver) -> None:
    with pytest.raises(FetchProblem) as first:
        await resolve_public("slow.stuck.test")
    assert str(first.value) == UNREACHABLE_MSG
    started = time.perf_counter()
    with pytest.raises(FetchProblem) as second:
        await resolve_public("slow.stuck.test")
    assert time.perf_counter() - started < 0.05  # remembered: no thread, no wait
    assert str(second.value) == UNREACHABLE_MSG
    assert stuck.started == ["slow.stuck.test"]


async def test_a_lookup_ends_at_the_timeout_even_though_its_thread_keeps_running(
    stuck: StuckResolver,
) -> None:
    started = time.perf_counter()
    with pytest.raises(FetchProblem):
        await resolve_public("one.stuck.test")
    assert 0.25 < time.perf_counter() - started < 1.0
    assert not stuck.release.is_set()  # the thread is still blocked in the resolver


async def test_a_failed_lookup_is_remembered_for_the_ttl_and_then_tried_again(
    dns: FakeDns, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(web_module, "_monotonic", lambda: clock["now"])
    dns.flaky("recovers.test", PUBLIC_IP)
    with pytest.raises(FetchProblem, match="Could not find that site"):
        await resolve_public("recovers.test")
    clock["now"] += DNS_FAILURE_TTL - 1
    with pytest.raises(FetchProblem) as remembered:
        await resolve_public("recovers.test")
    assert str(remembered.value) == NOT_FOUND_MSG
    assert dns.lookups("recovers.test") == 1  # the second answer came from the memory
    clock["now"] += 2  # past the 30 seconds
    assert await resolve_public("recovers.test") == [PUBLIC_IP]
    assert dns.lookups("recovers.test") == 2


async def test_only_failures_are_remembered(dns: FakeDns) -> None:
    dns.answer("fine.test", [PUBLIC_IP])
    assert await resolve_public("fine.test") == [PUBLIC_IP]
    assert await resolve_public("fine.test") == [PUBLIC_IP]
    assert dns.lookups("fine.test") == 2  # a good answer is never cached: rebinding is checked on every hop


async def test_the_failure_memory_is_bounded(dns: FakeDns, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_module, "DNS_FAILURE_CACHE_MAX", 5)
    for i in range(20):
        dns.missing(f"gone{i}.test")
        with pytest.raises(FetchProblem):
            await resolve_public(f"gone{i}.test")
    assert len(web_module._dns_failures) == 5
    assert "gone19.test" in web_module._dns_failures
    assert "gone0.test" not in web_module._dns_failures  # the oldest entries went first


async def test_lookups_run_in_the_resolver_pool_not_the_default_one(monkeypatch: pytest.MonkeyPatch) -> None:
    names: list[str] = []

    def lookup(host: str, port: object, *args: object, **kwargs: object) -> list[tuple[object, ...]]:
        names.append(threading.current_thread().name)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    assert await resolve_public("pool.test") == [PUBLIC_IP]
    assert [n.rsplit("_", 1)[0] for n in names] == ["tayyari-dns"]
    assert web_module.DNS_WORKERS == limits.WEB_DNS_WORKERS == 8


# ----------------------------------------------------------------------------- 13: ports and the error message


PORTS_NOT_ALLOWED = [8080, 8443, 22, 25, 81, 3306, 5432, 6379, 9200, 27017, 65535]


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("port", PORTS_NOT_ALLOWED)
async def test_a_link_to_any_other_port_is_refused_before_anything_is_looked_up_or_sent(
    scheme: str, port: int, no_dns: FakeDns, web: FakeWeb
) -> None:
    doc, lines = await read(f"{scheme}://example.test:{port}/")
    assert doc.ok is False
    assert doc.error == UNREACHABLE_MSG
    assert no_dns.calls == []
    assert web.requests == []
    assert lines == []


async def test_an_ip_literal_on_another_port_is_refused_too(no_dns: FakeDns, web: FakeWeb) -> None:
    doc, _ = await read(f"http://{PUBLIC_IP}:8080/")
    assert doc.error == UNREACHABLE_MSG
    assert web.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/",
        "https://example.test/",
        "http://example.test:80/",
        "https://example.test:443/",
        "http://example.test:443/",
        "https://example.test:80/",
    ],
)
async def test_ports_80_and_443_and_no_port_are_fetched(url: str, dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(
        PUBLIC_IP, page("<html><head><title>Hi</title></head><body><p>Some page text.</p></body></html>")
    )
    doc, _ = await read(url)
    assert doc.ok is True
    assert len(web.requests) == 1


async def test_a_redirect_to_another_port_is_refused_and_not_followed(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, redirect("http://example.test:8080/admin"))
    with pytest.raises(FetchProblem) as exc:
        await fetch_text("https://example.test/", MAX_BODY_BYTES, ACCEPT)
    assert str(exc.value) == UNREACHABLE_MSG
    assert len(web.requests) == 1
    assert dns.lookups("example.test") == 1  # no lookup for the redirect target's port


async def test_a_private_address_on_another_port_still_says_private(no_dns: FakeDns, web: FakeWeb) -> None:
    doc, _ = await read("http://127.0.0.1:8080/admin")
    assert doc.error == web_module.PRIVATE_MSG
    assert no_dns.calls == []


def connection_failures() -> dict[str, BaseException]:
    refused = httpx.ConnectError("[Errno 111] Connection refused")
    refused.__cause__ = ConnectionRefusedError(111, "Connection refused")
    return {
        "refused": refused,
        "unreachable": httpx.ConnectError("[Errno 101] Network is unreachable"),
        "connect-timeout": httpx.ConnectTimeout("timed out"),
        "read-timeout": httpx.ReadTimeout("timed out"),
        "os-error": OSError(113, "No route to host"),
    }


@pytest.mark.parametrize("name", list(connection_failures()))
async def test_refused_unreachable_and_timed_out_all_read_the_same(
    name: str, dns: FakeDns, web: FakeWeb
) -> None:
    failure = connection_failures()[name]

    def fail(request: httpx.Request) -> httpx.Response:
        raise failure

    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, fail)
    doc, _ = await read("https://example.test/")
    assert doc.error == UNREACHABLE_MSG


async def test_the_walls_that_are_known_keep_their_own_wording(dns: FakeDns, web: FakeWeb) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, page("denied", status=403))
    doc, _ = await read("https://example.test/")
    assert doc.error == "That site blocks automated reading or needs a login (HTTP 403)."
    dns.answer("www.linkedin.com", [PUBLIC_IP])
    doc, _ = await read("https://www.linkedin.com/in/someone", "linkedin")
    assert doc.error == web_module.LINKEDIN_BLOCKED
    dns.missing("nope.test")
    doc, _ = await read("https://nope.test/")
    assert doc.error == NOT_FOUND_MSG


# ----------------------------------------------------------------------------- 3: DOCX


def raw_docx(body: str, *, member: str = "word/document.xml", extra: dict[str, bytes] | None = None) -> bytes:
    """A minimal .docx whose main part is exactly `body`, written without python-docx so any XML can go in."""
    namespace = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    types = (
        '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    rels = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'officeDocument" Target="word/document.xml"/></Relationships>'
    )
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document {namespace}><w:body>{body}</w:body></w:document>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr(member, document)
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    return out.getvalue()


PARAGRAPH = "<w:p><w:r><w:t>x</w:t></w:r></w:p>"


def test_the_reviewers_29_mb_docx_is_refused_at_once_with_little_memory() -> None:
    """An 85 KB upload whose document.xml is 29 MB of paragraphs took 24 s and 544 MB to read."""
    data = raw_docx(PARAGRAPH * (29_000_000 // len(PARAGRAPH)))
    assert len(data) < 200_000
    tracemalloc.start()
    started = time.perf_counter()
    try:
        with pytest.raises(ResumeError) as exc:
            _extract_docx(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert time.perf_counter() - started < 0.5
    assert peak < 4 * 1024 * 1024
    assert str(exc.value) == TOO_COMPLEX_MSG
    assert exc.value.fmt == "docx"


async def test_a_refused_docx_reaches_the_user_with_the_one_message() -> None:
    doc = await ingest_resume(raw_docx(PARAGRAPH * 900_000), "flood.docx", lambda _line: None)
    assert doc.ok is False
    assert doc.error == "That file is too large or complex to read as a resume."
    assert doc.kind_label == "Resume · DOCX"


def test_a_part_over_the_xml_limit_is_refused_even_with_few_elements() -> None:
    padding = "<!--" + "x" * (MAX_DOCX_XML + 10) + "-->"
    data = raw_docx(PARAGRAPH + padding)
    with pytest.raises(ResumeError, match="too large or complex"):
        _check_docx_cost(data)


def test_the_parts_together_may_not_unpack_past_the_total() -> None:
    limit = limits.RESUME_MAX_DOCX_UNZIPPED_BYTES
    assert limit == 4 * 1024 * 1024
    fits = raw_docx(PARAGRAPH, extra={"word/media/photo.bin": bytes(limit - 100_000)})
    _check_docx_cost(fits)
    over = raw_docx(PARAGRAPH, extra={"word/media/photo.bin": bytes(limit + 1)})
    with pytest.raises(ResumeError, match="too large or complex"):
        _check_docx_cost(over)


def test_a_tag_flood_under_the_size_limit_is_refused_by_the_count_before_python_docx_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("python-docx must not build a tree for this file")

    monkeypatch.setattr(docx, "Document", must_not_run)
    data = raw_docx("<w:p/>" * 300_000)  # 1.8 MB of XML: under the 2 MiB part limit
    started = time.perf_counter()
    tracemalloc.start()
    try:
        with pytest.raises(ResumeError) as exc:
            _extract_docx(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert str(exc.value) == TOO_COMPLEX_MSG
    assert time.perf_counter() - started < 1.0
    assert peak < 4 * 1024 * 1024


def test_the_element_limit_is_exact() -> None:
    real = "<w:p><w:r><w:t>Hello</w:t></w:r></w:p>"  # a paragraph and a run: 2 tags
    assert MAX_DOCX_ELEMENTS == limits.RESUME_MAX_DOCX_ELEMENTS == 60_000
    _check_docx_cost(raw_docx("<w:p/>" * (MAX_DOCX_ELEMENTS - 2) + real))
    with pytest.raises(ResumeError):
        _check_docx_cost(raw_docx("<w:p/>" * (MAX_DOCX_ELEMENTS - 1) + real))


def test_another_namespace_prefix_does_not_hide_a_flood() -> None:
    body = (
        '<w:sdt xmlns:x="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' + "<x:p/>" * 70_000
    )
    with pytest.raises(ResumeError):
        _check_docx_cost(raw_docx(body + "</w:sdt>"))


def test_the_count_covers_every_xml_part_not_just_the_main_one() -> None:
    data = raw_docx("<w:p/>", extra={"word/header1.xml": ("<w:p/>" * (MAX_DOCX_ELEMENTS + 1)).encode()})
    with pytest.raises(ResumeError):
        _check_docx_cost(data)
    elsewhere = raw_docx("<w:p/>" * 70_000, member="word/document2.xml")  # not where python-docx looks first
    with pytest.raises(ResumeError):
        _check_docx_cost(elsewhere)


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        (b"<w:p><w:r><w:t>x</w:t></w:r></w:p>", 2),
        (
            b"<w:pPr/><w:rPr/><w:rFonts/><w:proofErr/><w:pict/><w:permStart/><w:p>",
            1,
        ),  # only p and r themselves
        (b"<x:p/><y:r a='1'/><p/><r/>", 4),
        (b'<w:r w:rsidR="00A1"><w:p\nw:x="1">', 2),
        (b"</w:p></w:r><?pi?><!DOCTYPE d>", 0),
        (b"", 0),
        (b"<", 0),
        (b"<w:p", 0),  # never closed: not a tag
    ],
)
def test_the_scanner_counts_paragraph_and_run_start_tags_only(xml: bytes, expected: int) -> None:
    assert _count_paragraphs_and_runs(io.BytesIO(xml), 10**9) == expected


@pytest.mark.parametrize("offset", range(0, 14))
def test_a_tag_cut_by_a_chunk_boundary_is_counted_exactly_once(offset: int) -> None:
    chunk = resume_module._SCAN_CHUNK
    data = (
        b" " * (chunk - offset)
        + b"<w:p><w:r>"
        + b" " * (2 * chunk)
        + b"<w:p/>"
        + b" " * (chunk - offset)
        + b"<x:r>"
    )
    assert _count_paragraphs_and_runs(io.BytesIO(data), 10**9) == 4


def test_the_streaming_count_agrees_with_counting_the_whole_part_at_once() -> None:
    rng = random.Random(7)
    pattern = resume_module._PARAGRAPH_OR_RUN
    for _ in range(12):
        pieces = [
            rng.choice([b"<w:p>", b"<w:r ", b"<w:pPr>", b"<x:r/>", b"</w:p>", b"text", b" ", b"<"])
            for _ in range(40_000)
        ]
        data = b"".join(pieces)
        assert _count_paragraphs_and_runs(io.BytesIO(data), 10**9) == len(pattern.findall(data))


def test_the_scanner_stops_early_once_the_limit_is_passed() -> None:
    stream = io.BytesIO(b"<w:p/>" * 1_000_000)
    assert _count_paragraphs_and_runs(stream, 1000) > 1000
    assert stream.tell() < 200_000  # it did not read the rest


def test_a_long_table_is_read_cell_by_cell_in_document_order() -> None:
    """Cells are read from the XML directly, so the cost follows the paragraphs and nothing builds a grid."""
    rows = 3000
    body = "<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid>" + "".join(
        f"<w:tr><w:tc><w:p><w:r><w:t>row {i}</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>b</w:t></w:r></w:p></w:tc></w:tr>"
        for i in range(rows)
    )
    data = raw_docx(body + "</w:tbl>")
    started = time.perf_counter()
    text = _extract_docx(data)
    assert time.perf_counter() - started < 5.0
    lines = text.split("\n")
    assert len(lines) == 2 * rows
    assert lines[:3] == ["row 0", "b", "row 1"]


def test_a_realistic_docx_is_nowhere_near_the_limits() -> None:
    from tests.unit.ingest.helpers import make_docx

    data = make_docx(
        [f"Paragraph {i} with a few words in it." for i in range(400)], headings=["Experience"] * 20
    )
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        elements = sum(
            _count_paragraphs_and_runs(archive.open(m), 10**9)
            for m in archive.infolist()
            if m.filename.endswith(".xml")
        )
    assert elements < MAX_DOCX_ELEMENTS // 20
    assert "Paragraph 399" in _extract_docx(data)


# ----------------------------------------------------------------------------- 3: PDF


def test_the_reviewers_8_mb_content_stream_is_bounded_in_time_and_memory() -> None:
    """A 23 KB PDF whose one content stream is 8 MB of text operators took 40 s and 384 MB."""
    content = b"BT /F1 12 Tf 10 10 Td (hello) Tj ET\n" * 220_000
    pdf = make_pdf_with_streams([(zlib.compress(content), "/Filter /FlateDecode")])
    assert len(content) > 7_000_000
    assert len(pdf) < 100_000
    tracemalloc.start()
    started = time.perf_counter()
    try:
        text, pages = _extract_pdf(pdf)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert time.perf_counter() - started < 2.0
    assert peak < 30 * 1024 * 1024
    assert (text, pages) == ("", 1)  # nothing readable: the reader looks at the file itself instead


async def test_that_pdf_is_treated_as_scanned_not_as_an_error() -> None:
    content = b"BT /F1 12 Tf 10 10 Td (hello) Tj ET\n" * 220_000
    pdf = make_pdf_with_streams([(zlib.compress(content), "/Filter /FlateDecode")])
    doc = await ingest_resume(pdf, "heavy.pdf", lambda _line: None)
    assert doc.ok is True
    assert doc.meta["scanned"] is True
    assert doc.pdf_bytes == pdf


def test_the_stream_limit_is_one_mebibyte_and_a_normal_page_is_still_read() -> None:
    assert resume_module.MAX_PDF_STREAM_BYTES == limits.RESUME_MAX_PDF_STREAM_BYTES == 1024 * 1024
    assert resume_module.MAX_PDF_PAGES == limits.RESUME_MAX_PDF_PAGES == 20
    pdf = make_pdf([["Experience", "Built things with React and TypeScript."] * 30])
    text, pages = _extract_pdf(pdf)
    assert "Built things with React and TypeScript." in text
    assert pages == 1


def test_a_page_that_streams_a_few_hundred_kb_of_operators_is_still_read() -> None:
    content = b"BT /F1 12 Tf 10 10 Td (hello) Tj ET\n" * 5_000  # 180 KB: far more than any resume page
    text, _ = _extract_pdf(make_pdf_with_streams([(zlib.compress(content), "/Filter /FlateDecode")]))
    assert text.count("hello") == 5_000


def test_at_most_twenty_pages_are_read_but_all_are_counted() -> None:
    pdf = make_pdf([[f"PAGE-{n}-MARKER"] for n in range(1, 41)])
    text, pages = _extract_pdf(pdf)
    assert pages == 40
    assert "PAGE-20-MARKER" in text
    assert "PAGE-21-MARKER" not in text


def test_the_form_expansion_limit_is_applied_while_reading() -> None:
    if not hasattr(pypdf, "apply_configuration"):
        pytest.skip("this pypdf has no per-call configuration")
    if not hasattr(pypdf.get_configuration(), "xform_maximum_invocations_per_extraction"):
        pytest.skip("this pypdf does not limit form expansion")
    with resume_module._pdf_limits():
        assert pypdf.get_configuration().xform_maximum_invocations_per_extraction == MAX_PDF_FORM_INVOCATIONS
    assert MAX_PDF_FORM_INVOCATIONS < 100


def form_invocation_pdf(invocations: int, form_ops: int) -> bytes:
    """One page that draws the same form XObject `invocations` times; the form holds `form_ops` text operators."""
    op = b"BT /F1 12 Tf 10 10 Td (hello) Tj ET\n"

    def stream(data: bytes, entries: str = "") -> bytes:
        return f"<< /Length {len(data)} {entries} >>\nstream\n".encode() + data + b"\nendstream"

    return _assemble_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 5 0 R "
                b"/Resources << /Font << /F1 3 0 R >> /XObject << /X0 6 0 R >> >> >>"
            ),
            5: stream(b"/X0 Do\n" * invocations),
            6: stream(
                op * form_ops,
                "/Type /XObject /Subtype /Form /BBox [0 0 100 100] /Resources << /Font << /F1 3 0 R >> >>",
            ),
        }
    )


def test_the_time_budget_also_stops_one_heavy_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """The budget used to be checked between pages only; a page that expands a form 5000 times ignored it."""
    pdf = form_invocation_pdf(
        invocations=5000, form_ops=3000
    )  # a 108 KB form, parsed again at every invocation
    monkeypatch.setattr(resume_module, "PDF_EXTRACT_BUDGET_S", 0.4)
    started = time.perf_counter()
    text, pages = _extract_pdf(pdf)
    elapsed = time.perf_counter() - started
    assert pages == 1
    assert elapsed < 4.0  # about 0.5 s; 64 expansions at a quarter of a second each take far longer
    assert text == ""


def test_a_pdf_pypdf_refuses_to_open_is_too_complex_not_corrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    class LimitReachedError(Exception):
        pass

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise LimitReachedError("page tree too large")

    monkeypatch.setattr(pypdf, "PdfReader", refuse)
    with pytest.raises(ResumeError) as exc:
        _extract_pdf(make_pdf([["x"]]))
    assert str(exc.value) == TOO_COMPLEX_MSG
    assert exc.value.fmt == "pdf"


def test_a_huge_text_file_is_cut_before_it_is_cleaned() -> None:
    data = ("Delivered a project with React and Python.\n" * 400_000).encode()  # 17 MB
    started = time.perf_counter()
    parsed = resume_module._parse(data, "huge.txt")
    assert time.perf_counter() - started < 2.0
    assert len(parsed.text) <= limits.RESUME_MAX_EXTRACT_CHARS


# ----------------------------------------------------------------------------- 14: NUL characters


def test_nul_is_removed_from_notes() -> None:
    doc = ingest_notes("Led" + NUL + " the migration" + NUL + NUL + ".\nAlso Python." + NUL)
    assert doc.text == "Led the migration.\nAlso Python."
    assert NUL not in doc.text
    assert doc.meta == {"chars": len(doc.text), "words": len(doc.text.split())}


async def test_nul_is_removed_from_a_text_resume_and_its_file_name() -> None:
    raw = ("React" + NUL + " developer with" + NUL + " experience.").encode()
    doc = await ingest_resume(raw, "my" + NUL + "cv.txt", lambda _line: None)
    assert doc.text == "React developer with experience."
    assert doc.src == "mycv.txt"
    assert NUL not in repr(doc.to_dict())


async def test_nul_written_as_a_pdf_escape_is_removed_from_the_text() -> None:
    escape = BACKSLASH.encode() + b"000"  # the octal escape for the NUL character
    line = b"(Frontend" + escape + b" engineer" + escape + b"with React) Tj"
    pdf = make_pdf_with_streams([(b"BT /F1 11 Tf 72 720 Td " + line + b" ET", "")])
    text = await parse_resume(pdf, "nul.pdf")
    assert NUL not in text
    assert "Frontend" in text
    doc = await ingest_resume(pdf, "nul.pdf", lambda _line: None)
    assert NUL not in repr(doc.to_dict())


async def test_nul_is_removed_from_a_web_page_and_its_title(dns: FakeDns, web: FakeWeb) -> None:
    html = (
        b"<html><head><title>Port"
        + bytes(1)
        + b"folio</title><meta name='description' content='Builds"
        + bytes(1)
        + b" apps'></head><body><p>Hello"
        + bytes(1)
        + b" world, I build things with Python and React every day.</p></body></html>"
    )
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(PUBLIC_IP, page(html))
    doc, _ = await read("https://example.test/")
    assert doc.ok is True
    assert doc.meta["title"] == "Portfolio"
    assert doc.meta["description"] == "Builds apps"
    assert "Hello world" in doc.text
    assert NUL not in repr(doc.to_dict())


def test_strip_nul_reaches_into_containers_and_keeps_what_is_clean() -> None:
    messy = {"a" + NUL: ["x" + NUL, ("y", "z" + NUL)], "n": 1, "none": None}
    assert strip_nul(messy) == {"a": ["x", ("y", "z")], "n": 1, "none": None}
    clean = {"a": ["x", ("y", "z")], "n": 1}
    assert strip_nul(clean) is clean  # nothing to remove: the same object, nothing rebuilt
    assert strip_nul("fine") == "fine"
    assert strip_nul(42) == 42


def test_a_source_doc_is_clean_from_the_moment_it_is_built() -> None:
    doc = SourceDoc(
        id="x" + NUL,
        kind="link",
        label="L" + NUL,
        url="https://x.test/" + NUL,
        text="a" + NUL + "b",
        notes=["n" + NUL],
        meta={"title": "t" + NUL, "tags": ["g" + NUL]},
        src="s" + NUL,
        kind_label="k",
        quick_signals=["React" + NUL],
        error=None,
    )
    assert (doc.id, doc.label, doc.url, doc.text, doc.src) == ("x", "L", "https://x.test/", "ab", "s")
    assert doc.notes == ["n"]
    assert doc.meta == {"title": "t", "tags": ["g"]}
    assert doc.quick_signals == ["React"]
    assert NUL not in repr(doc.to_dict())


async def test_text_set_after_construction_is_scrubbed_before_it_leaves_ingest() -> None:
    doc = SourceDoc(id="link-1", kind="link", label="Link")

    async def ingest(_log: Callable[[str], None]) -> SourceDoc:
        doc.text = "late" + NUL + " text"
        doc.meta["title"] = "late" + NUL
        doc.error = "bad" + NUL
        return doc

    planned = PlannedSource("link-1", "link", "Link", "Web page", "x.test" + NUL, "https://x.test/", ingest)
    out = await ingest_planned(planned, lambda _line: None)
    assert out.text == "late text"
    assert out.meta["title"] == "late"
    assert out.error == "bad"
    assert out.src == "x.test"
