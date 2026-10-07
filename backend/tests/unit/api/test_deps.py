from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse

from tayyari.api import deps
from tayyari.api.deps import (
    REQUEST_ID_MAX_CHARS,
    RequestIdMiddleware,
    build_request_context,
    get_request_context,
    new_request_id,
    sanitize_request_id,
)
from tayyari.api.errors import install_error_handlers
from tayyari.contract import RequestContext
from tayyari.core import context
from tayyari.core.hashing import hmac_hex
from tayyari.core.settings import GuardSettings, Settings
from tests.fakes.clock import FakeClock
from tests.unit.api.helpers import AsgiExchange, RecordingLog, http_scope

KEY = b"k" * 32
MakeSettings = Callable[..., Settings]


def xff_settings(make_settings: MakeSettings, hops: int = 1) -> Settings:
    """Settings that read the client from X-Forwarded-For (the shipped default prefers Cloudflare's header)."""
    return make_settings(guard=GuardSettings(trusted_proxy_hops=hops, client_ip_header=None))


def make_app(**state: Any) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)
    for name, value in state.items():
        setattr(app.state, name, value)

    @app.get("/ctx")
    async def ctx(rc: RequestContext = Depends(get_request_context)) -> dict[str, Any]:
        return {
            "request_id": rc.request_id,
            "ip_hash": rc.ip_hash,
            "client_ip": rc.client_ip,
            "started_at": rc.started_at.isoformat(),
            "bound": context.current(),
        }

    @app.get("/bound")
    async def bound() -> dict[str, str]:
        return context.current()

    @app.get("/twice")
    async def twice(request: Request) -> dict[str, bool]:
        first = await get_request_context(request)
        second = await get_request_context(request)
        return {"same": first is second}

    @app.get("/fresh")
    async def fresh(request: Request) -> dict[str, str | None]:
        rc = build_request_context(request)
        return {"request_id": rc.request_id, "ip_hash": rc.ip_hash}

    return app


def client_for(app: FastAPI, peer: str = "203.0.113.7") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(peer, 5000))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# ───────────────────────────── request ids ─────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("req-123", "req-123"),
        ("a.b_c-D9", "a.b_c-D9"),
        ("  spaced out  ", "spacedout"),
        ("evil\r\nX-Injected: 1", "evilX-Injected1"),
        ("<script>alert(1)</script>", "scriptalert1script"),
        ("café-日本", "caf-"),
        ("a" * 200, "a" * 64),
        ("", None),
        (None, None),
        ("   ", None),
        ("!!!///###", None),
    ],
)
def test_sanitize_request_id(raw: str | None, expected: str | None) -> None:
    assert sanitize_request_id(raw) == expected


def test_new_request_ids_are_unique_hex() -> None:
    ids = {new_request_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(re.fullmatch(r"[0-9a-f]{32}", i) for i in ids)
    assert REQUEST_ID_MAX_CHARS == 64


async def test_a_supplied_request_id_is_echoed() -> None:
    async with client_for(make_app()) as client:
        res = await client.get("/bound", headers={"X-Request-ID": "client-supplied-1"})
    assert res.headers["x-request-id"] == "client-supplied-1"
    assert res.json() == {"request_id": "client-supplied-1"}


async def test_a_missing_or_unusable_request_id_is_replaced_by_a_fresh_one() -> None:
    async with client_for(make_app()) as client:
        absent = await client.get("/bound")
        junk = await client.get("/bound", headers={"X-Request-ID": "!!!"})
        other = await client.get("/bound")
    for res in (absent, junk, other):
        assert re.fullmatch(r"[0-9a-f]{32}", res.headers["x-request-id"])
        assert res.json() == {"request_id": res.headers["x-request-id"]}
    assert (
        len({absent.headers["x-request-id"], junk.headers["x-request-id"], other.headers["x-request-id"]})
        == 3
    )


async def test_a_long_request_id_is_cut_to_64_characters() -> None:
    async with client_for(make_app()) as client:
        res = await client.get("/bound", headers={"X-Request-ID": "z" * 500})
    assert res.headers["x-request-id"] == "z" * 64


async def test_the_header_is_echoed_on_errors_and_unknown_routes_too() -> None:
    async with client_for(make_app()) as client:
        res = await client.get("/no-such-route", headers={"X-Request-ID": "lost-1"})
    assert res.status_code == 404
    assert res.headers["x-request-id"] == "lost-1"


async def test_the_request_id_is_bound_for_the_request_only() -> None:
    assert context.current() == {}
    async with client_for(make_app()) as client:
        res = await client.get("/bound", headers={"X-Request-ID": "scoped"})
    assert res.json() == {"request_id": "scoped"}
    assert context.current() == {}


async def test_concurrent_requests_keep_their_own_ids() -> None:
    async with client_for(make_app()) as client:
        results = await asyncio.gather(
            *(client.get("/bound", headers={"X-Request-ID": f"r{i}"}) for i in range(25))
        )
    assert [r.json()["request_id"] for r in results] == [f"r{i}" for i in range(25)]
    assert [r.headers["x-request-id"] for r in results] == [f"r{i}" for i in range(25)]


async def test_the_middleware_does_not_hold_back_a_streaming_body() -> None:
    gate = asyncio.Event()
    app = make_app()

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            yield b"first\n"
            await gate.wait()
            yield b"second\n"

        return StreamingResponse(chunks(), media_type="application/x-ndjson")

    exchange = AsgiExchange(http_scope(path="/stream", method="GET", headers=[("x-request-id", "stream-1")]))
    task = exchange.run(app)
    await exchange.wait_for_parts(1)
    assert exchange.body_parts == [b"first\n"]  # delivered while the generator is still waiting
    assert exchange.headers["x-request-id"] == "stream-1"
    assert not exchange.complete
    gate.set()
    await task
    assert exchange.body_parts == [b"first\n", b"second\n"]


async def test_lifespan_and_other_scope_types_pass_straight_through() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        events.append("up")
        yield
        events.append("down")

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/bound")
    async def bound() -> dict[str, str]:
        return context.current()

    async with LifespanManager(app):
        async with client_for(app) as client:
            assert (await client.get("/bound")).status_code == 200
    assert events == ["up", "down"]


# ───────────────────────────── the request context ─────────────────────────────


async def test_the_context_has_the_id_the_client_ip_and_its_keyed_hash(make_settings: MakeSettings) -> None:
    app = make_app(settings=xff_settings(make_settings), ip_hash_key=KEY)
    async with client_for(app) as client:
        res = await client.get(
            "/ctx", headers={"X-Forwarded-For": "198.51.100.4, 192.0.2.77", "X-Request-ID": "ctx-1"}
        )
    body = res.json()
    assert body["request_id"] == "ctx-1" == res.headers["x-request-id"]
    assert body["client_ip"] == "192.0.2.77"  # one trusted hop: the rightmost entry
    assert body["ip_hash"] == hmac_hex(KEY, "192.0.2.77")
    assert len(body["ip_hash"]) == 16


async def test_the_ip_hash_is_bound_into_the_log_context_during_the_request(
    make_settings: MakeSettings,
) -> None:
    app = make_app(settings=xff_settings(make_settings), ip_hash_key=KEY)
    async with client_for(app) as client:
        res = await client.get("/ctx", headers={"X-Forwarded-For": "192.0.2.77", "X-Request-ID": "bound-1"})
    assert res.json()["bound"] == {"request_id": "bound-1", "ip_hash": hmac_hex(KEY, "192.0.2.77")}
    assert context.current() == {}  # nothing leaks out of the request


async def test_trusted_hops_and_the_named_header_come_from_the_settings(make_settings: MakeSettings) -> None:
    headers = {"X-Forwarded-For": "198.51.100.4, 192.0.2.77", "CF-Connecting-IP": "203.0.113.50"}

    def app_with(guard: GuardSettings) -> FastAPI:
        return make_app(settings=make_settings(guard=guard), ip_hash_key=KEY)

    async def client_ip_of(app: FastAPI, sent: dict[str, str]) -> str:
        async with client_for(app, "10.0.0.1") as client:
            return str((await client.get("/ctx", headers=sent)).json()["client_ip"])

    one_hop = app_with(GuardSettings(trusted_proxy_hops=1, client_ip_header=None))
    two_hops = app_with(GuardSettings(trusted_proxy_hops=2, client_ip_header=None))
    no_hops = app_with(GuardSettings(trusted_proxy_hops=0, client_ip_header=None))
    named = app_with(GuardSettings(trusted_proxy_hops=1, client_ip_header="CF-Connecting-IP"))
    assert await client_ip_of(one_hop, headers) == "192.0.2.77"
    assert await client_ip_of(two_hops, headers) == "198.51.100.4"
    assert await client_ip_of(no_hops, headers) == "10.0.0.1"  # the TCP peer
    assert await client_ip_of(named, headers) == "203.0.113.50"  # the platform header wins
    assert (
        await client_ip_of(named, {"X-Forwarded-For": "198.51.100.4, 192.0.2.77"}) == "192.0.2.77"
    )  # absent: XFF


async def test_without_a_forwarding_header_the_peer_is_the_client(make_settings: MakeSettings) -> None:
    app = make_app(settings=xff_settings(make_settings), ip_hash_key=KEY)
    async with client_for(app, "198.18.0.9") as client:
        body = (await client.get("/ctx")).json()
    assert body["client_ip"] == "198.18.0.9"
    assert body["ip_hash"] == hmac_hex(KEY, "198.18.0.9")


async def test_the_raw_ip_never_appears_in_the_repr(make_settings: MakeSettings) -> None:
    app = make_app(settings=xff_settings(make_settings), ip_hash_key=KEY)

    @app.get("/repr")
    async def show(rc: RequestContext = Depends(get_request_context)) -> dict[str, str]:
        return {"repr": repr(rc)}

    async with client_for(app, "198.18.0.9") as client:
        text = (await client.get("/repr")).json()["repr"]
    assert "198.18.0.9" not in text
    assert "ip_hash=" in text


async def test_addresses_in_one_ipv6_network_hash_alike(make_settings: MakeSettings) -> None:
    app = make_app(settings=xff_settings(make_settings), ip_hash_key=KEY)
    async with client_for(app) as client:
        a = (await client.get("/ctx", headers={"X-Forwarded-For": "2001:db8:1:2:aaaa::1"})).json()["ip_hash"]
        b = (await client.get("/ctx", headers={"X-Forwarded-For": "2001:db8:1:2:bbbb::9"})).json()["ip_hash"]
        c = (await client.get("/ctx", headers={"X-Forwarded-For": "2001:db8:1:3::1"})).json()["ip_hash"]
    assert a == b
    assert a != c


async def test_a_different_key_gives_a_different_hash(make_settings: MakeSettings) -> None:
    headers = {"X-Forwarded-For": "192.0.2.77"}
    async with client_for(make_app(settings=xff_settings(make_settings), ip_hash_key=b"a" * 32)) as c1:
        first = (await c1.get("/ctx", headers=headers)).json()["ip_hash"]
    async with client_for(make_app(settings=xff_settings(make_settings), ip_hash_key=b"b" * 32)) as c2:
        second = (await c2.get("/ctx", headers=headers)).json()["ip_hash"]
    assert first != second


async def test_a_callable_key_is_read_once_and_kept(make_settings: MakeSettings) -> None:
    calls = 0

    def key() -> bytes:
        nonlocal calls
        calls += 1
        return KEY

    app = make_app(settings=xff_settings(make_settings), ip_hash_key=key)
    async with client_for(app) as client:
        hashes = {
            (await client.get("/ctx", headers={"X-Forwarded-For": "192.0.2.77"})).json()["ip_hash"]
            for _ in range(5)
        }
    assert hashes == {hmac_hex(KEY, "192.0.2.77")}
    assert calls == 1


async def test_a_key_that_is_not_ready_yet_is_asked_again_later(make_settings: MakeSettings) -> None:
    answers: list[bytes | None] = [None, KEY]

    def key() -> bytes | None:
        return answers.pop(0) if answers else KEY

    app = make_app(settings=xff_settings(make_settings), ip_hash_key=key)
    headers = {"X-Forwarded-For": "192.0.2.77"}
    async with client_for(app) as client:
        first = (await client.get("/ctx", headers=headers)).json()["ip_hash"]
        second = (await client.get("/ctx", headers=headers)).json()["ip_hash"]
    assert first is None
    assert second == hmac_hex(KEY, "192.0.2.77")


async def test_without_a_key_there_is_no_hash_and_one_warning(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = RecordingLog()
    monkeypatch.setattr(deps, "log", recorder)
    app = make_app(settings=xff_settings(make_settings))  # no ip_hash_key
    async with client_for(app) as client:
        bodies = [(await client.get("/ctx")).json() for _ in range(3)]
    assert [b["ip_hash"] for b in bodies] == [None, None, None]
    assert all(b["client_ip"] == "203.0.113.7" for b in bodies)
    assert recorder.events("warning") == ["ip_hash_key_missing"]


async def test_settings_default_to_get_settings_when_the_container_stored_none() -> None:
    app = make_app(ip_hash_key=KEY)  # no app.state.settings: the shipped defaults (Cloudflare header, 3 hops)
    async with client_for(app, "10.0.0.1") as client:
        cloudflare = await client.get("/ctx", headers={"CF-Connecting-IP": "192.0.2.77"})
        chain = await client.get("/ctx", headers={"X-Forwarded-For": "192.0.2.77, 198.51.100.1, 10.9.9.9"})
    assert cloudflare.json()["client_ip"] == "192.0.2.77"
    assert chain.json()["client_ip"] == "192.0.2.77"  # three trusted hops from the right


async def test_started_at_uses_the_clock_on_app_state(make_settings: MakeSettings) -> None:
    clock = FakeClock(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    app = make_app(settings=xff_settings(make_settings), ip_hash_key=KEY, clock=clock)
    async with client_for(app) as client:
        res = await client.get("/ctx")
    assert res.json()["started_at"] == "2026-01-02T03:04:05+00:00"


async def test_started_at_falls_back_to_the_system_clock(make_settings: MakeSettings) -> None:
    app = make_app(settings=xff_settings(make_settings), ip_hash_key=KEY)
    before = datetime.now(UTC)
    async with client_for(app) as client:
        res = await client.get("/ctx")
    started = datetime.fromisoformat(res.json()["started_at"])
    assert before <= started <= datetime.now(UTC)


async def test_the_context_is_built_once_per_request(make_settings: MakeSettings) -> None:
    app = make_app(settings=xff_settings(make_settings), ip_hash_key=KEY)
    async with client_for(app) as client:
        assert (await client.get("/twice")).json() == {"same": True}


async def test_each_request_gets_its_own_context(make_settings: MakeSettings) -> None:
    app = make_app(settings=xff_settings(make_settings), ip_hash_key=KEY)
    async with client_for(app) as client:
        first = (await client.get("/ctx", headers={"X-Forwarded-For": "192.0.2.1"})).json()
        second = (await client.get("/ctx", headers={"X-Forwarded-For": "192.0.2.2"})).json()
    assert first["ip_hash"] != second["ip_hash"]
    assert first["request_id"] != second["request_id"]


async def test_the_context_works_without_the_middleware(make_settings: MakeSettings) -> None:
    app = FastAPI()
    app.state.settings = make_settings()
    app.state.ip_hash_key = KEY

    @app.get("/ctx")
    async def ctx(rc: RequestContext = Depends(get_request_context)) -> dict[str, str | None]:
        return {"request_id": rc.request_id, "ip_hash": rc.ip_hash}

    async with client_for(app) as client:
        body = (await client.get("/ctx", headers={"X-Request-ID": "no-middleware"})).json()
    assert body["request_id"] == "no-middleware"
    assert body["ip_hash"] == hmac_hex(KEY, "203.0.113.7")
