"""The security-review fixes at the route level, through the real app (`create_app`) with fake services.

#7   profile reads and the deep health check are shed; the deep check is also cached
#8   cross-site POSTs are refused (403), chat needs application/json (415)
#12  security headers and the Content-Security-Policy
#5   chat stream slots (429) and spend reservations held for the whole stream
#4   upload read deadline (408), byte budget (503) and one upload per client (429)
#19, #20  empty-filename parts count; HEAD and the wrong method on /api/health
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from tayyari.api import app as app_module
from tayyari.api.routes import chat as chat_route
from tayyari.api.routes import health as health_route
from tayyari.api.security import API_CSP, CROSS_SITE_MESSAGE, inline_script_hashes, page_csp
from tayyari.api.uploads import (
    MSG_BAD_CHARSET,
    MSG_NOT_JSON,
    MSG_UPLOAD_IN_PROGRESS,
    MSG_UPLOAD_TIMEOUT,
    MSG_UPLOADS_BUSY,
)
from tayyari.core.paths import STATIC_DIR
from tayyari.core.settings import Settings
from tayyari.guard.slots import StreamSlots
from tayyari.llm.errors import SPEND_CEILING_MESSAGE
from tests.unit.api.helpers import AsgiExchange, eventually, http_scope
from tests.unit.wiring.support import FakeAnalysis, FakeChat, Harness, ndjson, roomy_guard, running_app

MakeSettings = Callable[..., Settings]

EVIL = {"origin": "https://evil.example", "sec-fetch-site": "cross-site"}
SAME_SITE = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
MULTIPART = "multipart/form-data; boundary=b"
SAMPLE_FORM = b'--b\r\nContent-Disposition: form-data; name="sample"\r\n\r\n1\r\n--b--\r\n'
NOTES_HEAD = b'--b\r\nContent-Disposition: form-data; name="notes"\r\n\r\n'
CLOSE = b"\r\n--b--\r\n"
OTHER_CLIENT = {"cf-connecting-ip": "198.51.100.77"}


def chat_payload(h: Harness, message: str = "What should I learn next?") -> dict[str, Any]:
    return {"profileId": h.sample_id(), "message": message}


GUARD_S = 15.0
"""A request that should be refused while another is held open must not hang the suite if it is let in instead."""


async def post_chat(h: Harness, **headers: str) -> httpx.Response:
    return await asyncio.wait_for(
        h.client.post("/api/chat", json=chat_payload(h), headers=headers or None), GUARD_S
    )


async def analyze(h: Harness, **headers: str) -> httpx.Response:
    return await asyncio.wait_for(h.analyze_sample(**headers), GUARD_S)


# ───────────────────────────── #20: HEAD and the wrong method on /api/health ─────────────────────────────


async def test_head_health_is_200(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        head = await h.client.head("/api/health")
        get = await h.client.get("/api/health")
    assert head.status_code == 200
    assert head.headers["content-type"] == get.headers["content-type"] == "application/json"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def test_the_wrong_method_on_health_is_a_405_json(make_settings: MakeSettings, method: str) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        res = await h.client.request(method, "/api/health")
    assert res.status_code == 405
    assert res.json() == {"error": health_route.METHOD_NOT_ALLOWED_MESSAGE}
    assert res.headers["allow"] == "GET, HEAD"


# ───────────────────────────── #7: profile reads and deep health are shed ─────────────────────────────


class CountingProfiles:
    def __init__(self) -> None:
        self.calls = 0
        self.owner_checks = 0

    async def get_access(self, profile_id: str) -> None:
        self.owner_checks += 1

    async def get_profile(self, profile_id: str) -> None:
        self.calls += 1


async def test_profile_reads_are_shed_before_they_reach_the_database(make_settings: MakeSettings) -> None:
    settings = make_settings(guard=roomy_guard(chat_per_minute=1))  # a burst of 2, then 1 per minute
    async with running_app(settings) as h:
        profiles = CountingProfiles()
        h.container.repos = dataclasses.replace(h.container.repos, profiles=profiles)  # type: ignore[arg-type]
        pid = "0" * 32
        statuses = [(await h.client.get(f"/api/profile/{pid}")).status_code for _ in range(2)]
        limited = await h.client.get(f"/api/profile/{pid}")
        queried, checked = profiles.calls, profiles.owner_checks
        other = await h.client.get(f"/api/profile/{pid}", headers=OTHER_CLIENT)
    assert statuses == [404, 404]
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1
    assert "error" in limited.json()
    assert queried == 2  # the shed request never queried
    assert checked == 2  # ... not even for the owner check that comes before the read
    assert other.status_code == 404  # the bucket is per client
    assert (profiles.calls, profiles.owner_checks) == (3, 3)


async def test_a_profile_read_takes_a_token_from_the_chat_bucket(make_settings: MakeSettings) -> None:
    """`RateLimiter.shed` only knows `analyze` and `chat`; profile reads use the caller's chat bucket."""
    async with running_app(make_settings(guard=roomy_guard(chat_per_minute=1))) as h:
        for _ in range(2):
            await h.client.get(f"/api/profile/{'0' * 32}")
        chat = await post_chat(h)
        analyzed = await analyze(h)
    assert chat.status_code == 429
    assert analyzed.status_code == 200


async def test_an_invalid_profile_id_is_shed_too(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard(chat_per_minute=1))) as h:
        statuses = [(await h.client.get("/api/profile/not-an-id")).status_code for _ in range(3)]
    assert statuses == [404, 404, 429]


class Pinger:
    """Stands in for ``Database.ping``: counts calls, can fail, can wait to be released."""

    def __init__(self, *, fail: bool = False, gate: asyncio.Event | None = None) -> None:
        self.calls = 0
        self.fail = fail
        self.gate = gate

    async def __call__(self) -> None:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise ConnectionError("password=hunter2")


def install_ping(h: Harness, pinger: Pinger) -> None:
    assert h.container.db is not None
    h.container.db.ping = pinger  # type: ignore[method-assign]


async def test_deep_health_is_shed(make_settings: MakeSettings) -> None:
    settings = make_settings(guard=roomy_guard(chat_per_minute=1, deep_health_min_interval_s=0))
    async with running_app(settings) as h:
        pinger = Pinger()
        install_ping(h, pinger)
        statuses = [(await h.client.get("/api/health/deep")).status_code for _ in range(3)]
    assert statuses == [200, 200, 429]
    assert pinger.calls == 2


async def test_deep_health_asks_the_database_at_most_once_per_interval(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard(deep_health_min_interval_s=5))) as h:
        pinger = Pinger()
        install_ping(h, pinger)
        first = await h.client.get("/api/health/deep")
        h.clock.advance(4.9)
        second = await h.client.get("/api/health/deep")
        assert pinger.calls == 1
        h.clock.advance(0.2)
        third = await h.client.get("/api/health/deep")
    assert [r.json() for r in (first, second, third)] == [{"ok": True, "db": "ok"}] * 3
    assert pinger.calls == 2


async def test_deep_health_remembers_a_failure_for_the_interval_too(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard(deep_health_min_interval_s=5))) as h:
        pinger = Pinger(fail=True)
        install_ping(h, pinger)
        first = await h.client.get("/api/health/deep")
        second = await h.client.get("/api/health/deep")
        assert pinger.calls == 1  # a down database is not asked again for every request
        pinger.fail = False
        h.clock.advance(5.1)
        recovered = await h.client.get("/api/health/deep")
    assert first.status_code == second.status_code == 503
    assert first.json() == second.json() == {"error": health_route.DB_UNREACHABLE_MESSAGE}
    assert "hunter2" not in first.text
    assert recovered.status_code == 200
    assert pinger.calls == 2


async def test_deep_health_with_a_zero_interval_asks_every_time(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard(deep_health_min_interval_s=0))) as h:
        pinger = Pinger()
        install_ping(h, pinger)
        for _ in range(3):
            assert (await h.client.get("/api/health/deep")).status_code == 200
    assert pinger.calls == 3


async def test_callers_that_arrive_together_share_one_deep_check(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard(deep_health_min_interval_s=5))) as h:
        gate = asyncio.Event()
        pinger = Pinger(gate=gate)
        install_ping(h, pinger)
        calls = [asyncio.create_task(h.client.get("/api/health/deep")) for _ in range(5)]
        await eventually(lambda: pinger.calls >= 1)
        await asyncio.sleep(0.05)
        gate.set()
        results = await asyncio.gather(*calls)
    assert [r.status_code for r in results] == [200] * 5
    assert pinger.calls == 1


# ───────────────────────────── #6 (caller side): the IPv6 /48 reaches the limiter ─────────────────────────────


async def test_hopping_between_64s_inside_one_48_is_limited(make_settings: MakeSettings) -> None:
    """Each /64 has its own allowance, but the /48 around them is counted too, at 4x the per-IP limit."""
    async with running_app(make_settings(guard=roomy_guard(chat_per_hour=1))) as h:
        hopping = [
            (await post_chat(h, **{"cf-connecting-ip": f"2001:db8:1:{i}::1"})).status_code for i in range(8)
        ]
        elsewhere = await post_chat(h, **{"cf-connecting-ip": "2001:db8:2:0::1"})
        v4 = [
            (await post_chat(h, **{"cf-connecting-ip": f"198.51.100.{i}"})).status_code for i in range(1, 6)
        ]
    assert hopping == [200] * 4 + [429] * 4  # the fifth /64 in the /48 is over 4 x 1 per hour
    assert elsewhere.status_code == 200  # another /48
    assert v4 == [200] * 5  # IPv4 clients have no wider network


# ───────────────────────────── #8: cross-site requests and the chat Content-Type ─────────────────────────────


async def test_a_cross_site_analyze_is_a_403_and_costs_nothing(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard(analyze_per_minute=1))) as h:
        for forged in (EVIL, {"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"}):
            res = await analyze(h, **forged)
            assert res.status_code == 403
            assert res.json() == {"error": CROSS_SITE_MESSAGE}
            assert "x-request-id" in res.headers
        assert h.analysis.requests == []
        # none of that used up the caller's allowance
        assert (await analyze(h, **SAME_SITE)).status_code == 200


async def test_a_cross_site_chat_is_a_403(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        res = await post_chat(h, **EVIL)
    assert res.status_code == 403
    assert res.json() == {"error": CROSS_SITE_MESSAGE}
    assert h.chat.requests == []


@pytest.mark.parametrize(
    "headers",
    [{}, SAME_SITE, {"origin": "http://testserver"}, {"sec-fetch-site": "none"}],
    ids=["no-headers", "same-origin", "origin-only", "user-initiated"],
)
async def test_same_origin_and_header_less_posts_are_allowed(
    make_settings: MakeSettings, headers: dict[str, str]
) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        analyzed = await analyze(h, **headers)
        chat = await post_chat(h, **headers)
    assert analyzed.status_code == 200
    assert chat.status_code == 200


async def test_reads_are_not_affected_by_the_guard(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        res = await h.client.get("/api/health", headers=EVIL)
        sample = await h.client.get("/api/sample-profile", headers=EVIL)
    assert res.status_code == 200
    assert sample.status_code == 200


@pytest.mark.parametrize(
    "content_type", ["text/plain", "text/plain;charset=UTF-8", "application/x-www-form-urlencoded"]
)
async def test_chat_needs_application_json(make_settings: MakeSettings, content_type: str) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        body = json.dumps(chat_payload(h)).encode()
        res = await h.client.post("/api/chat", content=body, headers={"content-type": content_type})
        no_type = await h.client.post("/api/chat", content=body, headers={"content-type": ""})
        ok = await h.client.post(
            "/api/chat", content=body, headers={"content-type": "application/json; charset=utf-8"}
        )
    assert res.status_code == 415
    assert res.json() == {"error": MSG_NOT_JSON}
    assert no_type.status_code == 415
    assert ok.status_code == 200
    assert len(h.chat.requests) == 1


# ───────────────────────────── #12: security headers and the CSP ─────────────────────────────

INLINE = "window.__boot = 1; /* inline */\n"


def static_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A tiny web build with one inline script; returns the script text."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(
        f'<!doctype html><title>Tayyari</title><script>{INLINE}</script><div id="root"></div>',
        encoding="utf-8",
    )
    (static / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    monkeypatch.setattr(app_module, "STATIC_DIR", static)
    return INLINE


async def test_the_page_carries_the_csp_with_the_hash_of_its_inline_script(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = static_site(tmp_path, monkeypatch)
    async with running_app(make_settings(guard=roomy_guard())) as h:
        index = await h.client.get("/")
        deep_link = await h.client.get("/match/abc")  # an app route (unknown paths are real 404s now)
        asset = await h.client.get("/assets/app.js")
    expected = page_csp(inline_script_hashes(f"<script>{script}</script>"))
    assert index.headers["content-security-policy"] == expected
    assert deep_link.headers["content-security-policy"] == expected
    assert "script-src 'self' https://checkout.razorpay.com 'sha256-" in expected
    assert asset.headers["content-security-policy"] == API_CSP
    for res in (index, deep_link, asset):
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["x-frame-options"] == "DENY"
        assert res.headers["referrer-policy"] == "no-referrer"
        assert res.headers["permissions-policy"] == "camera=(), microphone=(self), geolocation=()"
        assert "x-request-id" in res.headers


async def test_the_csp_of_the_real_web_build_allows_its_inline_boot_script(
    make_settings: MakeSettings,
) -> None:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        pytest.skip("no web build in backend/static")
    hashes = inline_script_hashes(index.read_text(encoding="utf-8"))
    assert hashes
    async with running_app(make_settings(guard=roomy_guard())) as h:
        res = await h.client.get("/")
    assert res.headers["content-security-policy"] == page_csp(hashes)


async def test_json_and_ndjson_answers_carry_the_minimal_csp_and_keep_their_streaming_headers(
    make_settings: MakeSettings,
) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        health = await h.client.get("/api/health")
        stream = await analyze(
            h,
        )
        chat = await post_chat(h)
        missing = await h.client.get("/api/nope")
        refused = await analyze(h, **EVIL)
    for res in (health, stream, chat, missing, refused):
        assert res.headers["content-security-policy"] == API_CSP
        assert res.headers["x-content-type-options"] == "nosniff"
    assert stream.headers["content-type"] == "application/x-ndjson; charset=utf-8"
    assert stream.headers["cache-control"] == "no-cache, no-transform"
    assert stream.headers["x-accel-buffering"] == "no"
    assert [e["type"] for e in ndjson(stream)] == ["phase", "done"]


async def test_hsts_follows_the_scheme(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        plain = await h.client.get("/api/health")
        proxied = await h.client.get("/api/health", headers={"x-forwarded-proto": "https"})
    assert "strict-transport-security" not in plain.headers
    assert proxied.headers["strict-transport-security"] == "max-age=31536000"


# ───────────────────────────── #5: chat stream slots ─────────────────────────────


async def test_a_client_over_its_chat_stream_limit_gets_a_429_until_one_finishes(
    make_settings: MakeSettings,
) -> None:
    release = asyncio.Event()
    chat = FakeChat(block=release)
    settings = make_settings(guard=roomy_guard(chat_streams_per_ip=1))
    async with running_app(settings, chat=chat) as h:
        first = asyncio.create_task(post_chat(h))
        await eventually(lambda: chat.running == 1)
        busy = await post_chat(h)
        elsewhere = asyncio.create_task(post_chat(h, **OTHER_CLIENT))  # another client has its own slot
        await eventually(lambda: chat.running == 2)
        release.set()
        finished = await first
        await elsewhere
        after = await post_chat(h)  # the slot came back when the stream ended
    assert busy.status_code == 429
    assert busy.json() == {"error": chat_route.BUSY_MESSAGE}
    assert busy.headers["retry-after"] == "10"
    assert finished.status_code == 200
    assert after.status_code == 200
    assert len(chat.requests) == 3  # the refused turn never reached the service


async def test_a_busy_refusal_never_reaches_the_chat_service(make_settings: MakeSettings) -> None:
    release = asyncio.Event()
    chat = FakeChat(block=release)
    async with running_app(make_settings(guard=roomy_guard(chat_streams_per_ip=1)), chat=chat) as h:
        first = asyncio.create_task(post_chat(h))
        await eventually(lambda: chat.running == 1)
        assert (await post_chat(h)).status_code == 429
        release.set()
        await first
    assert len(chat.requests) == 1


async def test_the_process_wide_chat_limit_applies_across_clients(make_settings: MakeSettings) -> None:
    release = asyncio.Event()
    chat = FakeChat(block=release)
    settings = make_settings(guard=roomy_guard(chat_streams_per_ip=0, chat_streams_per_process=1))
    async with running_app(settings, chat=chat) as h:
        first = asyncio.create_task(post_chat(h))
        await eventually(lambda: chat.running == 1)
        busy = await post_chat(h, **OTHER_CLIENT)
        release.set()
        await first
        after = await post_chat(h, **OTHER_CLIENT)
    assert busy.status_code == 429
    assert busy.json() == {"error": chat_route.BUSY_MESSAGE}
    assert after.status_code == 200


async def test_a_busy_refusal_does_not_use_up_the_rate_limit(make_settings: MakeSettings) -> None:
    release = asyncio.Event()
    chat = FakeChat(block=release)
    settings = make_settings(guard=roomy_guard(chat_streams_per_ip=1, chat_per_hour=2))
    async with running_app(settings, chat=chat) as h:
        first = asyncio.create_task(post_chat(h))
        await eventually(lambda: chat.running == 1)
        for _ in range(3):
            assert (await post_chat(h)).status_code == 429  # busy
        release.set()
        assert (await first).status_code == 200
        second = await post_chat(h)
        third = await post_chat(h)
    assert second.status_code == 200  # still within "2 per hour": the refusals did not count
    assert third.status_code == 429
    assert "hourly limit" in third.json()["error"]


async def test_a_refused_request_gives_its_chat_slot_back(make_settings: MakeSettings) -> None:
    """The slot is taken before the shared counters and the spend check, so their refusals must release it."""
    async with running_app(make_settings(guard=roomy_guard(chat_streams_per_ip=1, chat_per_hour=1))) as h:
        assert (await post_chat(h)).status_code == 200
        for _ in range(3):  # each is refused by the hourly limit, none by "busy"
            res = await post_chat(h)
            assert res.status_code == 429
            assert "hourly limit" in res.json()["error"]

    async with running_app(
        make_settings(guard=roomy_guard(chat_streams_per_ip=1, daily_ceiling_usd=0.0))
    ) as h:
        for _ in range(3):
            res = await post_chat(h)
            assert (res.status_code, res.json()) == (503, {"error": SPEND_CEILING_MESSAGE})


async def test_the_chat_slot_is_released_after_a_crash(make_settings: MakeSettings) -> None:
    chat = FakeChat(fail=RuntimeError("secret"))
    async with running_app(make_settings(guard=roomy_guard(chat_streams_per_ip=1)), chat=chat) as h:
        crashed = await post_chat(h)
        again = await post_chat(h)
    assert ndjson(crashed)[-1]["type"] == "error"
    assert again.status_code == 200  # not 429: the slot was released


async def test_the_chat_slot_and_reservation_are_released_on_disconnect(make_settings: MakeSettings) -> None:
    release = asyncio.Event()
    chat = FakeChat(block=release)
    settings = make_settings(guard=roomy_guard(chat_streams_per_ip=1, daily_ceiling_usd=0.15))
    async with running_app(settings, chat=chat) as h:
        body = json.dumps(chat_payload(h)).encode()
        scope = http_scope(
            path="/api/chat",
            headers=[("content-type", "application/json"), ("content-length", str(len(body)))],
        )
        scope["client"] = ("127.0.0.1", 123)  # the same client as h.client
        exchange = AsgiExchange(scope, body=body)
        task = exchange.run(h.app)
        await asyncio.wait_for(chat.started.wait(), 5)
        assert (await post_chat(h)).status_code == 429  # same client: its slot is taken
        assert (await post_chat(h, **OTHER_CLIENT)).status_code == 503  # 0.10 of 0.15 is reserved

        exchange.disconnect()
        await asyncio.wait_for(task, 5)
        assert chat.cancelled == 1
        release.set()
        again = await post_chat(h)
        elsewhere = await post_chat(h, **OTHER_CLIENT)
    assert again.status_code == 200
    assert elsewhere.status_code == 200


async def test_slots_are_built_from_the_guard_settings_on_first_use(make_settings: MakeSettings) -> None:
    settings = make_settings(guard=roomy_guard(chat_streams_per_ip=3, chat_streams_per_process=7))
    async with running_app(settings) as h:
        assert not hasattr(h.app.state, "chat_slots")
        assert (await post_chat(h)).status_code == 200
        slots = h.app.state.chat_slots
        assert isinstance(slots, StreamSlots)
        assert (await post_chat(h)).status_code == 200
        assert h.app.state.chat_slots is slots  # one set per process
        assert slots.active == 0


async def test_a_container_that_has_chat_slots_is_preferred(make_settings: MakeSettings) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        h.container.chat_slots = StreamSlots(0, 0, busy_message="from the container")  # type: ignore[attr-defined]
        request = type("R", (), {"app": h.app})()
        assert chat_route.get_chat_slots(request, h.container) is h.container.chat_slots  # type: ignore[arg-type,attr-defined]
        assert (await post_chat(h)).status_code == 200
        assert not hasattr(h.app.state, "chat_slots")


# ───────────────────────────── #5: spend reservations ─────────────────────────────


async def test_concurrent_chats_cannot_all_pass_the_same_stale_ledger(make_settings: MakeSettings) -> None:
    """A ceiling of $0.15 and $0.10 per turn: one turn fits, a second one at the same time must be refused."""
    release = asyncio.Event()
    chat = FakeChat(block=release)
    async with running_app(make_settings(guard=roomy_guard(daily_ceiling_usd=0.15)), chat=chat) as h:
        first = asyncio.create_task(post_chat(h))
        await eventually(lambda: chat.running == 1)
        refused = await post_chat(h, **OTHER_CLIENT)
        release.set()
        assert (await first).status_code == 200
        after = await post_chat(h, **OTHER_CLIENT)
    assert (refused.status_code, refused.json()) == (503, {"error": SPEND_CEILING_MESSAGE})
    assert int(refused.headers["retry-after"]) >= 1
    assert after.status_code == 200  # the reservation was released when the first stream ended


async def test_a_chat_reservation_is_released_on_success_and_on_error(make_settings: MakeSettings) -> None:
    settings = make_settings(guard=roomy_guard(daily_ceiling_usd=0.15))
    async with running_app(settings) as h:
        for _ in range(3):  # each would be refused if the one before still held its $0.10
            assert (await post_chat(h)).status_code == 200
    chat = FakeChat(fail=RuntimeError("secret"))
    async with running_app(settings, chat=chat) as h:
        for _ in range(3):
            res = await post_chat(h)
            assert res.status_code == 200
            assert ndjson(res)[-1]["type"] == "error"


async def test_concurrent_analyses_cannot_all_pass_the_same_stale_ledger(make_settings: MakeSettings) -> None:
    release = asyncio.Event()
    analysis = FakeAnalysis(block=release)
    async with running_app(make_settings(guard=roomy_guard(daily_ceiling_usd=0.5)), analysis=analysis) as h:
        first = asyncio.create_task(
            analyze(
                h,
            )
        )
        await asyncio.wait_for(analysis.started.wait(), 5)
        refused = await analyze(h, **OTHER_CLIENT)  # $0.40 reserved + $0.40 for this one > $0.50
        release.set()
        assert (await first).status_code == 200
        after = await analyze(h, **OTHER_CLIENT)
    assert (refused.status_code, refused.json()) == (503, {"error": SPEND_CEILING_MESSAGE})
    assert after.status_code == 200


async def test_an_analyze_reservation_is_released_on_error_and_on_failure_before_the_stream(
    make_settings: MakeSettings,
) -> None:
    settings = make_settings(guard=roomy_guard(daily_ceiling_usd=0.5))
    async with running_app(settings, analysis=FakeAnalysis(fail=RuntimeError("secret"))) as h:
        for _ in range(3):
            res = await analyze(
                h,
            )
            assert ndjson(res)[-1]["type"] == "error"

    async with running_app(settings) as h:

        def broken(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("boom")

        h.container.analysis.run = broken  # type: ignore[method-assign]
        assert (
            await analyze(
                h,
            )
        ).status_code == 500
        assert (
            await analyze(
                h,
            )
        ).status_code == 500  # a leaked reservation would make this a 503
        h.container.analysis.run = FakeAnalysis().run  # type: ignore[method-assign]
        assert (
            await analyze(
                h,
            )
        ).status_code == 200


async def test_an_analyze_reservation_is_released_when_the_client_disconnects(
    make_settings: MakeSettings,
) -> None:
    release = asyncio.Event()
    analysis = FakeAnalysis(block=release)
    async with running_app(make_settings(guard=roomy_guard(daily_ceiling_usd=0.5)), analysis=analysis) as h:
        scope = http_scope(path="/api/analyze", headers=[("content-type", MULTIPART)])
        exchange = AsgiExchange(scope, body=SAMPLE_FORM)
        task = exchange.run(h.app)
        await asyncio.wait_for(analysis.started.wait(), 5)
        assert (await analyze(h, **OTHER_CLIENT)).status_code == 503

        exchange.disconnect()
        await asyncio.wait_for(task, 5)
        assert analysis.cancelled == 1
        release.set()
        after = await analyze(h, **OTHER_CLIENT)
    assert after.status_code == 200


# ───────────────────────────── #4: the upload deadline, budget and per-client limit ─────────────────────────────


async def slow_form(started: asyncio.Event, release: asyncio.Event) -> AsyncIterator[bytes]:
    """A multipart body that sends its first part, then waits (a stalled or very slow client)."""
    yield NOTES_HEAD + b"I built "
    started.set()
    await release.wait()
    yield b"compilers." + CLOSE


async def test_a_stalled_upload_is_a_408_json_and_the_server_carries_on(make_settings: MakeSettings) -> None:
    settings = make_settings(guard=roomy_guard(upload_read_timeout_s=0.1))
    release = asyncio.Event()
    async with running_app(settings) as h:
        stalled = await asyncio.wait_for(
            h.client.post(
                "/api/analyze",
                content=slow_form(asyncio.Event(), release),
                headers={"content-type": MULTIPART},
            ),
            GUARD_S,
        )
        release.set()
        again = await analyze(h)  # the client's upload slot and the buffers were freed
    assert stalled.status_code == 408
    assert stalled.json() == {"error": MSG_UPLOAD_TIMEOUT}
    assert again.status_code == 200
    assert len(h.analysis.requests) == 1  # only the second one reached the service


async def test_a_stalled_chat_body_is_a_408_json(make_settings: MakeSettings) -> None:
    release = asyncio.Event()

    async def stall() -> AsyncIterator[bytes]:
        yield b'{"profileId":'
        await release.wait()
        yield b"}"  # pragma: no cover

    async with running_app(make_settings(guard=roomy_guard(upload_read_timeout_s=0.1))) as h:
        res = await asyncio.wait_for(
            h.client.post("/api/chat", content=stall(), headers={"content-type": "application/json"}),
            GUARD_S,
        )
        release.set()
    assert res.status_code == 408
    assert res.json() == {"error": MSG_UPLOAD_TIMEOUT}


async def test_an_upload_past_the_inflight_budget_is_a_503_json(make_settings: MakeSettings) -> None:
    settings = make_settings(guard=roomy_guard(upload_inflight_bytes=1000))
    async with running_app(settings) as h:
        body = NOTES_HEAD + b"n" * 5000 + CLOSE
        res = await h.client.post("/api/analyze", content=body, headers={"content-type": MULTIPART})
        small = await analyze(
            h,
        )
    assert res.status_code == 503
    assert res.json() == {"error": MSG_UPLOADS_BUSY}
    assert int(res.headers["retry-after"]) >= 1
    assert small.status_code == 200  # the refused upload gave its bytes back
    assert len(h.analysis.requests) == 1


async def test_the_inflight_budget_is_shared_between_clients(make_settings: MakeSettings) -> None:
    settings = make_settings(guard=roomy_guard(upload_inflight_bytes=600))
    started, release = asyncio.Event(), asyncio.Event()
    async with running_app(settings) as h:
        first = asyncio.create_task(
            h.client.post(
                "/api/analyze", content=slow_form(started, release), headers={"content-type": MULTIPART}
            )
        )
        await asyncio.wait_for(started.wait(), 5)
        crowded = await h.client.post(
            "/api/analyze",
            content=NOTES_HEAD + b"n" * 500 + CLOSE,
            headers={"content-type": MULTIPART, **OTHER_CLIENT},
        )
        release.set()
        finished = await first
    assert crowded.status_code == 503
    assert finished.status_code == 200


async def test_a_second_upload_from_one_client_is_a_429_while_the_first_is_being_read(
    make_settings: MakeSettings,
) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    async with running_app(make_settings(guard=roomy_guard())) as h:
        first = asyncio.create_task(
            h.client.post(
                "/api/analyze", content=slow_form(started, release), headers={"content-type": MULTIPART}
            )
        )
        await asyncio.wait_for(started.wait(), 5)
        second = await analyze(
            h,
        )
        elsewhere = await analyze(h, **OTHER_CLIENT)
        release.set()
        finished = await first
        after = await analyze(
            h,
        )
    assert second.status_code == 429
    assert second.json() == {"error": MSG_UPLOAD_IN_PROGRESS}
    assert second.headers["retry-after"] == "5"
    assert elsewhere.status_code == 200  # the limit is per client
    assert finished.status_code == 200
    assert after.status_code == 200


async def test_a_refused_upload_does_not_use_up_the_rate_limit(make_settings: MakeSettings) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    async with running_app(make_settings(guard=roomy_guard(analyze_per_minute=2))) as h:
        first = asyncio.create_task(
            h.client.post(
                "/api/analyze", content=slow_form(started, release), headers={"content-type": MULTIPART}
            )
        )
        await asyncio.wait_for(started.wait(), 5)
        assert (
            await analyze(
                h,
            )
        ).status_code == 429
        release.set()
        assert (await first).status_code == 200
        assert (
            await analyze(
                h,
            )
        ).status_code == 200  # 2 per minute: the refused one did not count


async def test_the_upload_gate_follows_guard_enabled(make_settings: MakeSettings) -> None:
    """With the guard switched off a second concurrent upload from the same client is allowed."""
    started, release = asyncio.Event(), asyncio.Event()
    async with running_app(make_settings(guard=roomy_guard(enabled=False))) as h:
        first = asyncio.create_task(
            h.client.post(
                "/api/analyze", content=slow_form(started, release), headers={"content-type": MULTIPART}
            )
        )
        await asyncio.wait_for(started.wait(), 5)
        assert (
            await analyze(
                h,
            )
        ).status_code == 200
        release.set()
        assert (await first).status_code == 200


# ───────────────────────────── #1, #19, #20: the form parser, through the app ─────────────────────────────


@pytest.mark.parametrize("charset", ["punycode", "undefined", "utf-16"])
async def test_an_odd_form_charset_is_a_400_json_not_a_500(make_settings: MakeSettings, charset: str) -> None:
    async with running_app(make_settings(guard=roomy_guard())) as h:
        res = await h.client.post(
            "/api/analyze",
            content=NOTES_HEAD + b"a" * 200_000 + CLOSE,
            headers={"content-type": f"{MULTIPART}; charset={charset}"},
        )
    assert res.status_code == 400
    assert res.json() == {"error": MSG_BAD_CHARSET}
    assert h.analysis.requests == []


async def test_a_flood_of_empty_file_parts_is_a_400(make_settings: MakeSettings) -> None:
    part = (
        b'--b\r\nContent-Disposition: form-data; name="resume"; filename=""\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n\r\n"
    )
    async with running_app(make_settings(guard=roomy_guard())) as h:
        res = await h.client.post(
            "/api/analyze", content=part * 5000 + b"--b--\r\n", headers={"content-type": MULTIPART}
        )
    assert res.status_code == 400
    assert res.json() == {"error": "Too many fields. Maximum number of fields is 20."}
