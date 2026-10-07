"""The HTTP routes of the real app, with fake analysis and chat services behind a real SQLite database.

Covers health and deep health, the sample and profile routes, the analyze and chat happy paths, every guard in
front of them (429, 503, busy) and the pre-stream JSON errors.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from starlette.requests import Request

from tayyari.api.app import create_app
from tayyari.api.routes import analyze as analyze_route
from tayyari.api.routes import health as health_route
from tayyari.contract.models import Profile
from tayyari.core.settings import ConcurrencySettings, Settings
from tayyari.llm.errors import SPEND_CEILING_MESSAGE
from tayyari.sample.provider import SampleProvider
from tests.fakes.repos import FakeRepositories
from tests.unit.api.helpers import AsgiExchange, http_scope
from tests.unit.wiring.support import (
    BASE_URL,
    FakeAnalysis,
    make_overrides,
    ndjson,
    roomy_guard,
    running_app,
)

MakeSettings = Callable[..., Settings]


# ───────────────────────────── health ─────────────────────────────


async def test_health_reports_models_and_sample(make_settings: MakeSettings) -> None:
    settings = make_settings(guard=roomy_guard())
    async with running_app(settings) as h:
        response = await h.client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "models": {
            "reader": settings.reader_model,
            "synth": settings.synth_model,
            "chat": settings.chat_model,
        },
        "hasSample": True,
    }


async def test_health_says_ephemeral_for_a_throwaway_database(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(database_url="sqlite:///:memory:")) as h:
        body = (await h.client.get("/api/health")).json()
    assert body["storage"] == "ephemeral"


async def test_health_says_postgres_without_touching_the_database(make_settings: MakeSettings) -> None:
    """Fake repositories and no database: nothing is dialled, yet the Postgres URL decides the field."""
    settings = make_settings(database_url="postgresql://user:pw@ep-x-pooler.neon.tech/db?sslmode=require")
    async with running_app(settings, repos=FakeRepositories().bundle) as h:
        body = (await h.client.get("/api/health")).json()
    assert body["storage"] == "postgres"
    assert h.container.db is None


async def test_health_does_not_touch_the_database(make_settings: MakeSettings) -> None:
    async with running_app(make_settings()) as h:
        assert h.container.db is not None

        async def boom() -> None:
            raise AssertionError("health must not use the database")

        h.container.db.ping = boom  # type: ignore[method-assign]
        response = await h.client.get("/api/health")
    assert response.status_code == 200


async def test_deep_health_pings_the_database(make_settings: MakeSettings) -> None:
    async with running_app(make_settings()) as h:
        response = await h.client.get("/api/health/deep")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "db": "ok"}


async def test_deep_health_is_503_json_when_the_database_fails(make_settings: MakeSettings) -> None:
    async with running_app(make_settings()) as h:
        assert h.container.db is not None

        async def down() -> None:
            raise ConnectionError("password=hunter2 host=ep-secret.neon.tech")

        h.container.db.ping = down  # type: ignore[method-assign]
        response = await h.client.get("/api/health/deep")
    assert response.status_code == 503
    assert response.json() == {"error": health_route.DB_UNREACHABLE_MESSAGE}
    assert "hunter2" not in response.text


async def test_deep_health_gives_up_on_a_hanging_database(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_route, "DEEP_CHECK_TIMEOUT_S", 0.05)
    async with running_app(make_settings()) as h:
        assert h.container.db is not None

        async def hang() -> None:
            await asyncio.sleep(30)

        h.container.db.ping = hang  # type: ignore[method-assign]
        response = await h.client.get("/api/health/deep")
    assert response.status_code == 503
    assert response.json() == {"error": health_route.DB_UNREACHABLE_MESSAGE}


async def test_deep_health_without_a_database_is_ok(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(), repos=FakeRepositories().bundle) as h:
        response = await h.client.get("/api/health/deep")
    assert response.json() == {"ok": True, "db": "none"}


async def test_a_request_before_startup_gets_a_503_json(make_settings: MakeSettings) -> None:
    """No lifespan ran, so there is no container: a JSON 503, not a 500."""
    app = create_app(make_settings(), overrides=make_overrides()[0])
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        response = await client.get("/api/health")
    assert response.status_code == 503
    assert "starting up" in response.json()["error"]


# ───────────────────────────── sample and profile ─────────────────────────────


async def test_sample_profile_is_the_bundled_file(make_settings: MakeSettings) -> None:
    async with running_app(make_settings()) as h:
        response = await h.client.get("/api/sample-profile")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-cache"
    sample = SampleProvider().sample_profile_path()
    assert sample is not None
    assert response.json() == json.loads(sample.read_text(encoding="utf-8"))
    Profile.from_dict(response.json())  # still a valid Profile


async def test_sample_profile_without_fixtures_is_a_404_with_the_hint(
    make_settings: MakeSettings, tmp_path: Path
) -> None:
    async with running_app(make_settings()) as h:
        h.container.sample = SampleProvider(tmp_path / "no-fixtures")
        response = await h.client.get("/api/sample-profile")
    assert response.status_code == 404
    assert response.json() == {
        "error": 'No pre-computed sample profile yet. Run "uv run python -m tayyari.cli make-sample" on the server.'
    }


async def test_profile_unknown_id_is_404(make_settings: MakeSettings) -> None:
    async with running_app(make_settings()) as h:
        response = await h.client.get(f"/api/profile/{'0' * 32}")
    assert response.status_code == 404
    assert response.json() == {"error": "Profile not found"}


@pytest.mark.parametrize("pid", ["-starts-with-dash", "a" * 300, "has space", "semi;colon", "caf%C3%A9"])
async def test_profile_invalid_id_is_the_same_404(make_settings: MakeSettings, pid: str) -> None:
    async with running_app(make_settings()) as h:
        response = await h.client.get(f"/api/profile/{pid}")
    assert response.status_code == 404
    assert response.json() == {"error": "Profile not found"}


async def test_profile_returns_the_seeded_sample(make_settings: MakeSettings) -> None:
    async with running_app(make_settings()) as h:
        response = await h.client.get(f"/api/profile/{h.sample_id()}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["id"] == h.sample_id()
    Profile.from_dict(response.json())


async def test_profile_falls_back_to_the_bundled_sample_when_not_stored(make_settings: MakeSettings) -> None:
    async with running_app(make_settings()) as h:
        h.container.repos = dataclasses.replace(h.container.repos, profiles=FakeRepositories().profiles)
        assert await h.container.repos.profiles.get_profile(h.sample_id()) is None
        response = await h.client.get(f"/api/profile/{h.sample_id()}")
    assert response.status_code == 200
    assert response.json()["id"] == h.sample_id()


# ───────────────────────────── analyze ─────────────────────────────


async def test_analyze_sample_streams_to_done_and_the_profile_is_retrievable(
    make_settings: MakeSettings,
) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        response = await h.analyze_sample(**{"x-request-id": "req-abc-123"})
        events = ndjson(response)
        done = events[-1]
        profile_response = await h.client.get(f"/api/profile/{done['profile']['id']}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-ndjson; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["x-request-id"] == "req-abc-123"
    assert [e["type"] for e in events] == ["phase", "done"]
    assert done["profile"]["id"].startswith("sample-")
    assert profile_response.status_code == 200
    assert profile_response.json() == done["profile"]

    ((req, rc),) = h.analysis.requests
    assert req.sample is True
    assert rc.request_id == "req-abc-123"
    assert rc.ip_hash is not None
    assert len(rc.ip_hash) == 16
    assert rc.client_ip == "127.0.0.1"


async def test_analyze_ip_hash_is_stable_per_client_and_differs_between_clients(
    make_settings: MakeSettings,
) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        await h.analyze_sample(**{"cf-connecting-ip": "203.0.113.7"})
        await h.analyze_sample(**{"cf-connecting-ip": "203.0.113.7"})
        await h.analyze_sample(**{"cf-connecting-ip": "203.0.113.8"})
    hashes = [rc.ip_hash for _, rc in h.analysis.requests]
    assert hashes[0] == hashes[1] != hashes[2]
    assert all("203.0.113" not in (value or "") for value in hashes)


async def test_analyze_without_input_is_a_400_before_any_work(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        response = await h.client.post("/api/analyze", files={"goal": (None, "become a designer")})
    assert response.status_code == 400
    assert response.json() == {"error": "Add a resume, a link or some notes to analyse."}
    assert h.analysis.requests == []


async def test_analyze_rejects_an_oversized_declared_body(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        response = await h.client.post(
            "/api/analyze",
            content=b"x",
            headers={"content-type": "multipart/form-data; boundary=b", "content-length": "99999999"},
        )
    assert response.status_code == 413
    assert "error" in response.json()
    assert h.analysis.requests == []


async def test_analyze_crash_ends_the_stream_with_an_error_event(make_settings: MakeSettings) -> None:
    analysis = FakeAnalysis(fail=RuntimeError("secret internal detail"))
    async with running_app(make_settings(guard=roomy_guard()), analysis=analysis) as h:
        response = await h.analyze_sample()
    events = ndjson(response)
    assert response.status_code == 200
    assert events[-1] == {"type": "error", "message": "Something went wrong on the server."}
    assert "secret" not in response.text


async def test_analyze_unexpected_failure_before_the_stream_is_a_500_json(
    make_settings: MakeSettings,
) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:

        def broken(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("boom")

        h.container.analysis.run = broken  # type: ignore[method-assign]
        response = await h.analyze_sample()
        # the slot was given back: the next request is not told the server is busy
        h.container.analysis.run = FakeAnalysis().run  # type: ignore[method-assign]
        again = await h.analyze_sample()
    assert response.status_code == 500
    assert response.json() == {"error": "Something went wrong on the server."}
    assert again.status_code == 200


# ───────────────────────────── analyze: guards ─────────────────────────────


async def test_analyze_is_429_after_the_per_ip_limit(make_settings: MakeSettings) -> None:
    settings = make_settings(guard=roomy_guard(analyze_per_minute=2))
    async with running_app(settings) as h:
        statuses = [(await h.analyze_sample()).status_code for _ in range(2)]
        limited = await h.analyze_sample()
        other_client = await h.analyze_sample(**{"cf-connecting-ip": "198.51.100.9"})
    assert statuses == [200, 200]
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    assert "analyses" in limited.json()["error"]
    assert len(h.analysis.requests) == 3  # the third request never reached the service
    assert other_client.status_code == 200  # the limit is per client


async def test_analyze_is_503_json_when_the_spend_ceiling_is_zero(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard(daily_ceiling_usd=0.0))) as h:
        response = await h.analyze_sample()
    assert response.status_code == 503
    assert response.json() == {"error": SPEND_CEILING_MESSAGE}
    assert int(response.headers["retry-after"]) >= 1
    assert h.analysis.requests == []


async def test_analyze_is_429_busy_while_every_slot_is_taken(make_settings: MakeSettings) -> None:
    settings = make_settings(guard=roomy_guard(), concurrency=ConcurrencySettings(max_concurrent_analyses=1))
    release = asyncio.Event()
    analysis = FakeAnalysis(block=release)
    async with running_app(settings, analysis=analysis) as h:
        first = asyncio.create_task(h.analyze_sample())
        await asyncio.wait_for(analysis.started.wait(), 5)
        busy = await h.analyze_sample()
        release.set()
        finished = await first
        after = await h.analyze_sample()

    assert busy.status_code == 429
    assert busy.headers["retry-after"] == "30"
    assert busy.json() == {"error": analyze_route.BUSY_MESSAGE}
    assert finished.status_code == 200
    assert ndjson(finished)[-1]["type"] == "done"
    assert after.status_code == 200  # the slot came back when the first stream ended
    assert len(analysis.requests) == 2


async def test_a_busy_rejection_does_not_use_up_the_callers_rate_limit(make_settings: MakeSettings) -> None:
    settings = make_settings(
        guard=roomy_guard(analyze_per_minute=2), concurrency=ConcurrencySettings(max_concurrent_analyses=1)
    )
    release = asyncio.Event()
    analysis = FakeAnalysis(block=release)
    async with running_app(settings, analysis=analysis) as h:
        first = asyncio.create_task(h.analyze_sample())
        await asyncio.wait_for(analysis.started.wait(), 5)
        assert (await h.analyze_sample()).status_code == 429  # busy, not rate limited
        release.set()
        assert (await first).status_code == 200
        # one analysis counted, the busy one did not: a second still fits in "2 per minute"
        assert (await h.analyze_sample()).status_code == 200
        assert (await h.analyze_sample()).status_code == 429


# ───────────────────────────── the slot is tied to the response ─────────────────────────────


def _slot_request() -> Request:
    return Request(http_scope(path="/api/analyze"))


async def _events_until_cancelled(started: asyncio.Event) -> Any:
    started.set()
    await asyncio.sleep(3600)
    yield None  # pragma: no cover


async def test_the_slot_is_released_once_when_the_stream_finishes() -> None:
    released: list[int] = []

    async def events() -> Any:
        return
        yield  # pragma: no cover

    response = analyze_route._SlotResponse(_slot_request(), events(), "analyze", lambda: released.append(1))
    exchange = AsgiExchange(http_scope(path="/api/analyze"))
    await asyncio.wait_for(response(exchange.scope, exchange.receive, exchange.send), 5)
    assert released == [1]
    assert exchange.complete


async def test_the_slot_is_released_when_the_client_disconnects() -> None:
    released: list[int] = []
    started = asyncio.Event()
    exchange = AsgiExchange(http_scope(path="/api/analyze"))
    response = analyze_route._SlotResponse(
        Request(exchange.scope, exchange.receive),
        _events_until_cancelled(started),
        "analyze",
        lambda: released.append(1),
    )
    task = exchange.run(response)
    await asyncio.wait_for(started.wait(), 5)
    assert released == []
    exchange.disconnect()
    await asyncio.wait_for(task, 5)
    assert released == [1]


async def test_the_slot_is_released_when_the_server_cancels_the_response() -> None:
    released: list[int] = []
    started = asyncio.Event()
    response = analyze_route._SlotResponse(
        _slot_request(), _events_until_cancelled(started), "analyze", lambda: released.append(1)
    )
    exchange = AsgiExchange(http_scope(path="/api/analyze"))
    task = exchange.run(response)
    await asyncio.wait_for(started.wait(), 5)
    assert released == []
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert released == [1]


# ───────────────────────────── chat ─────────────────────────────


async def test_chat_streams_deltas_tools_and_done(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        response = await h.chat_turn(
            origin={"kind": "skill", "refId": "python", "title": "Python", "caption": "Strong"}
        )
    events = ndjson(response)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-ndjson; charset=utf-8"
    assert [e["type"] for e in events] == ["delta", "tool", "delta", "done"]
    assert events[1] == {"type": "tool", "name": "WebSearch", "detail": "data analyst jobs"}
    assert len(events[-1]["sessionId"]) == 32

    ((body, rc),) = h.chat.requests
    assert body.profileId == h.sample_id()
    assert body.message == "What should I learn next?"
    assert body.origin is not None
    assert body.origin.kind == "skill"
    assert rc.ip_hash is not None


async def test_chat_keeps_the_session_id_it_was_given(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        events = ndjson(await h.chat_turn(sessionId="s" * 32))
    assert events[-1] == {"type": "done", "sessionId": "s" * 32}


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (
            {"profileId": "p", "message": ""},
            "Invalid chat request: message String should have at least 1 character",
        ),
        (
            {"profileId": "p", "message": "   "},
            "Invalid chat request: message String should have at least 1 character",
        ),
        ({"message": "hi"}, "Invalid chat request: profileId Field required"),
        (
            {"profileId": "p", "message": "x" * 8001},
            "Invalid chat request: message String should have at most 8000 characters",
        ),
        (
            {"profileId": "p", "message": "hi", "origin": {"kind": "nonsense"}},
            "Invalid chat request: origin.kind Input should be 'skill', 'domain', 'source', 'role', "
            "'requirement', 'you' or 'free'",
        ),
    ],
)
async def test_chat_validation_errors_use_the_legacy_text(
    make_settings: MakeSettings, body: dict[str, Any], error: str
) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        response = await h.client.post("/api/chat", json=body)
    assert response.status_code == 400
    assert response.json() == {"error": error}
    assert h.chat.requests == []


async def test_chat_does_not_echo_the_message_in_a_validation_error(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        response = await h.client.post(
            "/api/chat", json={"profileId": "p", "message": "my private plan " * 600}
        )
    assert response.status_code == 400
    assert "private plan" not in response.text


async def test_chat_non_object_body_is_a_400(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        response = await h.client.post("/api/chat", json=["not", "an", "object"])
    assert response.status_code == 400
    assert response.json()["error"].startswith("Invalid chat request: body ")


async def test_chat_invalid_json_is_the_legacy_400(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        response = await h.client.post(
            "/api/chat", content=b"{not json", headers={"content-type": "application/json"}
        )
    assert response.status_code == 400
    assert response.json() == {"error": "Invalid JSON body"}


async def test_chat_oversized_body_is_a_413(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        response = await h.client.post(
            "/api/chat", content=b"x" * (1024 * 1024 + 1), headers={"content-type": "application/json"}
        )
    assert response.status_code == 413
    assert response.json() == {"error": "Request body too large"}


async def test_chat_is_429_after_the_per_ip_limit(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard(chat_per_minute=2))) as h:
        statuses = [(await h.chat_turn()).status_code for _ in range(2)]
        limited = await h.chat_turn()
    assert statuses == [200, 200]
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1
    assert "messages" in limited.json()["error"]
    assert len(h.chat.requests) == 2


async def test_a_chat_validation_error_does_not_count_against_the_shared_windows(
    make_settings: MakeSettings,
) -> None:
    """Invalid requests are turned away before the database counters (only the in-memory burst bucket, sized at
    twice the limit, sees them), so two valid messages still fit in "2 per minute"."""
    async with running_app(make_settings(guard=roomy_guard(chat_per_minute=2))) as h:
        for _ in range(2):
            assert (await h.client.post("/api/chat", json={"message": ""})).status_code == 400
        assert (await h.chat_turn()).status_code == 200
        assert (await h.chat_turn()).status_code == 200
        assert (await h.chat_turn()).status_code == 429


async def test_chat_is_503_json_when_the_spend_ceiling_is_zero(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard(daily_ceiling_usd=0.0))) as h:
        response = await h.chat_turn()
    assert response.status_code == 503
    assert response.json() == {"error": SPEND_CEILING_MESSAGE}
    assert h.chat.requests == []


async def test_a_chat_service_that_crashes_ends_with_an_error_event(make_settings: MakeSettings) -> None:
    class Crashing:
        async def stream_turn(self, req: Any, rc: Any) -> Any:
            raise RuntimeError("secret")
            yield  # pragma: no cover

    async with running_app(make_settings(guard=roomy_guard()), chat=Crashing()) as h:  # type: ignore[arg-type]
        response = await h.chat_turn()
    assert response.status_code == 200
    assert ndjson(response)[-1] == {"type": "error", "message": "Something went wrong on the server."}


# ───────────────────────────── the rest of the surface ─────────────────────────────


async def test_every_response_carries_a_request_id(make_settings: MakeSettings) -> None:
    async with running_app(make_settings()) as h:
        generated = await h.client.get("/api/health")
        echoed = await h.client.get("/api/health", headers={"x-request-id": "from-the-client"})
        not_found = await h.client.get("/api/profile/" + "0" * 32)
    assert len(generated.headers["x-request-id"]) == 32
    assert echoed.headers["x-request-id"] == "from-the-client"
    assert "x-request-id" in not_found.headers


async def test_an_unknown_api_path_is_a_json_404_not_the_web_app(make_settings: MakeSettings) -> None:
    async with running_app(make_settings()) as h:
        for method in ("GET", "POST"):
            response = await h.client.request(method, "/api/does-not-exist")
            assert response.status_code == 404
            assert response.json() == {"error": "Not found"}


async def test_the_wrong_method_on_an_api_route_is_the_json_404(make_settings: MakeSettings) -> None:
    """Like the legacy app: the /api catch-all answers every method, so GET /api/analyze is a JSON 404."""
    async with running_app(make_settings()) as h:
        response = await h.client.get("/api/analyze")
    assert response.status_code == 404
    assert response.json() == {"error": "Not found"}


async def test_the_docs_pages_are_off(make_settings: MakeSettings) -> None:
    async with running_app(make_settings()) as h:
        for path in ("/docs", "/redoc", "/openapi.json"):
            response = await h.client.get(path)
            assert "swagger" not in response.text.lower()
            assert "openapi" not in response.text.lower()


async def test_routes_work_with_the_real_lifespan_manager_directly(make_settings: MakeSettings) -> None:
    """The same app object, driven without the harness: one more guard that the wiring is what the app needs."""
    overrides, _ = make_overrides()
    app = create_app(make_settings(), overrides=overrides)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            assert (await client.get("/api/health")).json()["ok"] is True
