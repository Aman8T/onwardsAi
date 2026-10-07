"""The same behaviour through a real uvicorn server on a loopback port.

`httpx.ASGITransport` cannot show a stream arriving piece by piece or a client hanging up, so these tests
start uvicorn on port 0 and talk to it over a socket: heartbeats arrive while the stream is quiet, closing the
connection cancels the producer, and an oversized chunked upload is answered with the JSON 413.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tayyari.api.deps import RequestIdMiddleware
from tayyari.api.errors import install_error_handlers
from tayyari.api.ndjson import NdjsonResponse
from tayyari.api.uploads import parse_analyze_form
from tayyari.contract import AnalysisEvent, ChatEvent, ev_chat_done, ev_log
from tests.unit.api.helpers import eventually

STARTUP_S = 10.0


@dataclass
class Probe:
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    asgi: dict[str, str] = field(default_factory=dict)


def make_app(probe: Probe) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    @app.post("/stream")
    async def stream(request: Request) -> NdjsonResponse:
        probe.asgi = dict(request.scope.get("asgi", {}))

        async def events() -> AsyncIterator[AnalysisEvent | ChatEvent]:
            try:
                yield ev_log("Jev", "hello")
                await asyncio.sleep(60)
                yield ev_chat_done("never")
            except asyncio.CancelledError:
                probe.cancelled.set()
                raise

        return NdjsonResponse(request, events(), "analyze", heartbeat_s=0.2, poll_s=0.05)

    @app.post("/analyze")
    async def analyze(request: Request) -> JSONResponse:
        req = await parse_analyze_form(request)
        return JSONResponse({"resume": len(req.resume_bytes or b"")})

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    return app


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve())
    try:
        await eventually(lambda: server.started or task.done(), within=STARTUP_S)
        if task.done():
            task.result()
            pytest.fail("the server stopped during startup")
        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)


async def test_heartbeats_arrive_while_quiet_and_a_hang_up_cancels_the_producer() -> None:
    probe = Probe()
    async with serving(make_app(probe)) as client:
        async with client.stream("POST", "/stream", headers={"X-Request-ID": "live-1"}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/x-ndjson; charset=utf-8"
            assert response.headers["cache-control"] == "no-cache, no-transform"
            assert response.headers["x-accel-buffering"] == "no"
            assert response.headers["x-request-id"] == "live-1"
            pieces: list[bytes] = []
            async for piece in response.aiter_raw():
                pieces.append(piece)
                if len(pieces) >= 3:
                    break
        # leaving the block closed the connection: the client is gone
        await asyncio.wait_for(probe.cancelled.wait(), 5)
    assert pieces[0] == b'{"type":"log","who":"Jev","text":"hello"}\n'
    assert pieces[1:] == [b"\n", b"\n"]  # blank-line heartbeats, delivered while the stream was quiet
    assert probe.asgi["version"] == "3.0"


async def test_an_oversized_chunked_upload_is_answered_and_the_server_stays_healthy() -> None:
    probe = Probe()

    async def body() -> AsyncIterator[bytes]:
        yield (
            b'--b\r\nContent-Disposition: form-data; name="resume"; filename="cv.pdf"\r\n'
            b"Content-Type: application/pdf\r\n\r\n"
        )
        for _ in range(400):  # 25 MB, no Content-Length
            yield b"x" * 65536

    async with serving(make_app(probe)) as client:
        try:
            res = await client.post(
                "/analyze", content=body(), headers={"content-type": "multipart/form-data; boundary=b"}
            )
        except httpx.TransportError:
            pass  # the server answered early and closed while the client was still sending: also fine
        else:
            assert res.status_code == 413
            assert res.json() == {"error": "That file is larger than 10 MB."}
        health = await client.get("/health")
        assert health.json() == {"ok": True}
        small = await client.post("/analyze", files={"resume": ("cv.pdf", b"%PDF-1.4 hi", "application/pdf")})
        assert small.json() == {"resume": 11}


async def test_http_errors_are_json_over_a_real_connection() -> None:
    probe = Probe()
    async with serving(make_app(probe)) as client:
        res = await client.post("/analyze", data={"goal": "only a goal"})
        assert res.status_code == 400
        assert res.json() == {"error": "Add a resume, a link or some notes to analyse."}
        missing = await client.get("/nothing")
        assert missing.status_code == 404
        assert missing.json() == {"error": "Not Found"}
