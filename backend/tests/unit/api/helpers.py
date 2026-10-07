"""Helpers shared by the API infrastructure tests: multipart bodies, hand-built requests, an ASGI driver.

`httpx.ASGITransport` buffers a whole response before returning it, so it cannot show a heartbeat arriving in
the middle of a stream or a producer being cancelled by a disconnect. `AsgiExchange` drives an ASGI callable
directly instead: the test sees every message the moment it is sent and decides when the client goes away.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Scope

BOUNDARY = "tayyari-test-boundary"
KIB = 1024


def multipart_body(
    fields: Sequence[tuple[str, str]] = (),
    files: Sequence[tuple[str, str, bytes, str | None]] = (),
    *,
    boundary: str = BOUNDARY,
    closed: bool = True,
) -> tuple[bytes, str]:
    """A multipart/form-data body and its Content-Type: text ``fields`` (name, value), then ``files``
    (field name, file name, bytes, content type)."""
    out = bytearray()
    for name, value in fields:
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += value.encode() + b"\r\n"
    for name, filename, data, content_type in files:
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        if content_type is not None:
            out += f"Content-Type: {content_type}\r\n".encode()
        out += b"\r\n" + data + b"\r\n"
    if closed:
        out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def split(body: bytes, size: int) -> list[bytes]:
    """``body`` cut into chunks of ``size`` bytes (the last may be shorter)."""
    return [body[i : i + size] for i in range(0, len(body), size)]


@dataclass
class Pulled:
    """How much of a request body the code under test has actually read."""

    bytes: int = 0
    chunks: int = 0


def make_request(
    chunks: Iterable[bytes],
    *,
    content_type: str | None = None,
    headers: Sequence[tuple[str, str]] = (),
    content_length: int | None = None,
    pulled: Pulled | None = None,
    method: str = "POST",
    path: str = "/api/analyze",
) -> Request:
    """A Starlette `Request` whose body arrives chunk by chunk, counted in ``pulled`` as it is read.

    The chunks are consumed lazily (an iterator, or a generator that never materialises the whole body), and
    no Content-Length header is sent unless ``content_length`` is given: a chunked upload.
    """
    stats = pulled if pulled is not None else Pulled()
    iterator = iter(chunks)
    raw_headers: list[tuple[bytes, bytes]] = [(k.lower().encode(), v.encode()) for k, v in headers]
    if content_type is not None:
        raw_headers.append((b"content-type", content_type.encode()))
    if content_length is not None:
        raw_headers.append((b"content-length", str(content_length).encode()))
    done = False

    async def receive() -> Message:
        nonlocal done
        if done:
            return {"type": "http.disconnect"}
        try:
            chunk = next(iterator)
        except StopIteration:
            done = True
            return {"type": "http.request", "body": b"", "more_body": False}
        stats.bytes += len(chunk)
        stats.chunks += 1
        return {"type": "http.request", "body": chunk, "more_body": True}

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw_headers,
        "client": ("203.0.113.9", 4000),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope, receive)


def make_stalling_request(
    chunks: Iterable[bytes],
    *,
    content_type: str | None = None,
    release: asyncio.Event | None = None,
    tail: bytes = b"",
    pulled: Pulled | None = None,
    method: str = "POST",
    path: str = "/api/analyze",
) -> Request:
    """A request whose body arrives in ``chunks`` and then stalls, like a client that stopped sending.

    The body stays open until ``release`` is set (then ``tail`` arrives and the body ends); without a
    ``release`` it never ends.
    """
    stats = pulled if pulled is not None else Pulled()
    iterator = iter(chunks)
    base = make_request([], content_type=content_type, method=method, path=path)
    state = {"ended": False}

    async def receive() -> Message:
        if state["ended"]:
            return {"type": "http.disconnect"}
        try:
            chunk = next(iterator)
        except StopIteration:
            if release is None:
                await asyncio.Event().wait()  # a client that never sends another byte
            else:
                await release.wait()
            state["ended"] = True
            stats.bytes += len(tail)
            return {"type": "http.request", "body": tail, "more_body": False}
        stats.bytes += len(chunk)
        stats.chunks += 1
        return {"type": "http.request", "body": chunk, "more_body": True}

    return Request(base.scope, receive)


def repeated(chunk: bytes, count: int) -> Iterable[bytes]:
    """``count`` copies of the same chunk object, produced lazily (no big body is ever built in memory)."""
    return (chunk for _ in range(count))


def http_scope(
    *,
    path: str = "/",
    method: str = "POST",
    headers: Sequence[tuple[str, str]] = (),
    spec_version: str | None = None,
) -> Scope:
    asgi: dict[str, str] = {"version": "3.0"}
    if spec_version is not None:
        asgi["spec_version"] = spec_version
    return {
        "type": "http",
        "asgi": asgi,
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        "client": ("203.0.113.9", 4000),
        "server": ("testserver", 80),
        "scheme": "http",
    }


@dataclass
class AsgiExchange:
    """One HTTP exchange driven by hand: the test reads what is sent and decides when the client leaves.

    ``send_error`` makes the next ``send`` of a body message raise it (a client that hung up mid-stream).
    """

    scope: Scope
    messages: list[Message] = field(default_factory=list)
    send_error: BaseException | None = None
    body: bytes = b""
    """The request body, delivered in one message."""
    _disconnect: asyncio.Event = field(default_factory=asyncio.Event)
    _body_sent: asyncio.Event = field(default_factory=asyncio.Event)
    _requested: bool = False

    def disconnect(self) -> None:
        """The client closes the connection."""
        self._disconnect.set()

    async def receive(self) -> Message:
        if not self._requested:
            self._requested = True
            return {"type": "http.request", "body": self.body, "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body") and self.send_error is not None:
            error, self.send_error = self.send_error, None
            raise error
        self.messages.append(message)
        if message["type"] == "http.response.body":
            self._body_sent.set()

    @property
    def status(self) -> int:
        start = next(m for m in self.messages if m["type"] == "http.response.start")
        return int(start["status"])

    @property
    def headers(self) -> dict[str, str]:
        start = next(m for m in self.messages if m["type"] == "http.response.start")
        return {k.decode().lower(): v.decode() for k, v in start["headers"]}

    @property
    def body_parts(self) -> list[bytes]:
        """The non-empty body chunks, in the order they were sent."""
        return [m["body"] for m in self.messages if m["type"] == "http.response.body" and m.get("body")]

    @property
    def complete(self) -> bool:
        return any(m["type"] == "http.response.body" and not m.get("more_body") for m in self.messages)

    async def wait_for_parts(self, count: int, *, within: float = 2.0) -> None:
        """Wait until at least ``count`` body chunks have been sent."""

        async def _wait() -> None:
            while len(self.body_parts) < count:
                self._body_sent.clear()
                await self._body_sent.wait()

        await asyncio.wait_for(_wait(), within)

    def run(self, app: ASGIApp) -> asyncio.Task[None]:
        """Start ``app`` as a task; the caller awaits or cancels it."""
        return asyncio.create_task(app(self.scope, self.receive, self.send))


async def eventually(check: Callable[[], bool], *, within: float = 2.0) -> None:
    """Wait until ``check()`` is true, failing the test (TimeoutError) after ``within`` seconds."""

    async def _wait() -> None:
        while not check():  # noqa: ASYNC110  (polling an arbitrary predicate is the point)
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_wait(), within)


async def collect[T](stream: AsyncIterator[T]) -> list[T]:
    return [item async for item in stream]


class RecordingLog:
    """Stands in for a module's structlog logger: records ``(level, event, fields)`` instead of writing."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, event: str, **fields: Any) -> None:
        self.records.append((level, event, fields))

    def debug(self, event: str, **fields: Any) -> None:
        self._record("debug", event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._record("info", event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._record("warning", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._record("error", event, **fields)

    def events(self, level: str | None = None) -> list[str]:
        return [event for lvl, event, _ in self.records if level is None or lvl == level]
