"""Shared test helpers for the ingest tests: a fake resolver, a fake "internet", a log recorder, generated files.

Nothing here touches the real network. `FakeDns` replaces `socket.getaddrinfo` (what the event loop's resolver
calls, so every hostname lookup in `web.py` goes through it) and raises for a name nobody registered;
`FakeWeb` sits behind a respx router with `assert_all_mocked`, so an unmocked request fails loudly instead of
leaving the machine. The PDF is built by hand and the DOCX by python-docx, so the parsers see real file formats.
The pytest fixtures that wire these up live in `conftest.py`.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import socket
import zipfile
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import httpx
import respx

BACKSLASH = chr(92)
PUBLIC_IP = "93.184.216.34"
"""A routable address used as "the real server" behind every fake host name."""
PUBLIC_IP_2 = "93.184.216.35"
PUBLIC_IP_3 = "93.184.216.36"
PUBLIC_IP_4 = "93.184.216.37"


# ----------------------------------------------------------------------------- DNS


class FakeDns:
    """Stands in for ``socket.getaddrinfo``. Register answers per host name; anything else is a test failure."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._rounds: dict[str, list[list[str] | None]] = {}

    def answer(self, host: str, *rounds: Sequence[str]) -> None:
        """Successive answers for `host`: the first lookup gets rounds[0], the second rounds[1], the last repeats."""
        self._rounds[host.lower()] = [list(r) for r in rounds]

    def missing(self, host: str) -> None:
        """`host` does not exist (NXDOMAIN)."""
        self._rounds[host.lower()] = [None]

    def flaky(self, host: str, *addresses: str) -> None:
        """`host` does not exist at the first lookup and answers with `addresses` from the second on."""
        self._rounds[host.lower()] = [None, list(addresses)]

    def lookups(self, host: str) -> int:
        return self.calls.count(host.lower())

    def __call__(self, host: Any, port: Any, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        name = str(host).lower()
        self.calls.append(name)
        if name not in self._rounds:
            raise AssertionError(f"unexpected DNS lookup for {name!r}")
        rounds = self._rounds[name]
        answer = rounds[min(self.calls.count(name) - 1, len(rounds) - 1)]
        if answer is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        out: list[tuple[Any, ...]] = []
        for address in answer:
            if ":" in address:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 0, 0, 0)))
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)))
        return out


# ----------------------------------------------------------------------------- network


class FakeWeb:
    """The "internet" behind the guard: one handler per validated address the client may connect to.

    Every request is recorded. A connection to an address nobody registered fails the test, which is how the
    SSRF tests prove that a private address was never contacted.
    """

    def __init__(self, router: respx.MockRouter) -> None:
        self.requests: list[httpx.Request] = []
        self._handlers: dict[str, Callable[[httpx.Request], Any]] = {}
        router.route().mock(side_effect=self._dispatch)

    def serve(self, address: str, handler: Callable[[httpx.Request], Any]) -> None:
        """Answer requests sent to `address` (the IP the client connected to, not the host name)."""
        self._handlers[address] = handler

    def to(self, address: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == address]

    def hosts(self) -> list[str]:
        """The Host header of every request, in order."""
        return [r.headers.get("host", "") for r in self.requests]

    async def _dispatch(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self._handlers.get(request.url.host)
        if handler is None:
            raise AssertionError(f"the client connected to an unexpected address: {request.url.host}")
        result = handler(request)
        if inspect.isawaitable(result):
            result = await result
        assert isinstance(result, httpx.Response)
        return result


class ChunkedStream(httpx.AsyncByteStream):
    """A response body delivered in the given chunks, optionally pausing before each one."""

    def __init__(self, chunks: Sequence[bytes], delay: float = 0.0) -> None:
        self.chunks = list(chunks)
        self.delay = delay
        self.sent = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.sent += 1
            yield chunk


# ----------------------------------------------------------------------------- logging


class LogRecorder:
    """A drop-in for the module loggers: keeps every event with its level and fields."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def _record(self, level: str, event: str, **fields: Any) -> None:
        self.events.append({"event": event, "level": level, **fields})

    def debug(self, event: str, **fields: Any) -> None:
        self._record("debug", event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._record("info", event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._record("warning", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._record("error", event, **fields)

    def named(self, event: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["event"] == event]


# ----------------------------------------------------------------------------- generated files


def _pdf_escape(text: str) -> str:
    return text.replace(BACKSLASH, BACKSLASH * 2).replace("(", BACKSLASH + "(").replace(")", BACKSLASH + ")")


def _assemble_pdf(objects: dict[int, bytes]) -> bytes:
    """Number the objects, write the cross-reference table and the trailer."""
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    xref_at = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for number in range(1, size):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def make_pdf_with_streams(streams: Sequence[tuple[bytes, str]]) -> bytes:
    """A PDF whose page i has the given raw content stream; the str is extra stream-dictionary entries
    (for example ``/Filter /FlateDecode``)."""
    objects: dict[int, bytes] = {}
    kids = [4 + 2 * i for i in range(len(streams))]
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(streams)} >>".encode()
    )
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for i, (stream, entries) in enumerate(streams):
        page_id, content_id = 4 + 2 * i, 5 + 2 * i
        objects[page_id] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode()
        objects[content_id] = (
            f"<< /Length {len(stream)} {entries} >>\nstream\n".encode() + stream + b"\nendstream"
        )
    return _assemble_pdf(objects)


def make_pdf(pages: Sequence[Sequence[str]]) -> bytes:
    """A minimal valid PDF: one Helvetica text object per page, one line per string (no lines: a blank page)."""
    streams: list[tuple[bytes, str]] = []
    for lines in pages:
        if lines:
            ops = [
                "BT",
                "/F1 11 Tf",
                "72 720 Td",
                "14 TL",
                *[f"({_pdf_escape(line)}) Tj T*" for line in lines],
                "ET",
            ]
            streams.append(("\n".join(ops).encode("latin-1", "replace"), ""))
        else:
            streams.append((b"", ""))
    return make_pdf_with_streams(streams)


def make_docx(
    paragraphs: Sequence[str] = (),
    *,
    headings: Sequence[str] = (),
    table: Sequence[Sequence[str]] = (),
) -> bytes:
    """A DOCX written by python-docx: headings first, then paragraphs, then an optional table."""
    import docx

    document = docx.Document()
    for heading in headings:
        document.add_heading(heading, level=1)
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    if table:
        grid = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                grid.cell(r, c).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def add_zip_member(archive: bytes, name: str, payload: bytes) -> bytes:
    """`archive` with one more (deflated) member."""
    buffer = io.BytesIO(archive)
    with zipfile.ZipFile(buffer, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, payload)
    return buffer.getvalue()


def replace_zip_member(archive: bytes, name: str, payload: bytes) -> bytes:
    """`archive` with the member `name` swapped for `payload` (everything else copied as it is)."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(archive)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for item in source.infolist():
            target.writestr(item.filename, payload if item.filename == name else source.read(item.filename))
    return out.getvalue()


def encrypt_pdf(pdf: bytes, *, user_password: str, owner_password: str = "owner") -> bytes:  # noqa: S107
    """RC4-128 encryption (pure Python in pypdf, so no `cryptography` package is needed)."""
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=io.BytesIO(pdf))
    writer.encrypt(user_password=user_password, owner_password=owner_password, algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
