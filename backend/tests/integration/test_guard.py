"""The guards in front of the expensive routes, with the real rate limiter and spend guard over a real SQLite
file: per-IP limits, the daily spend ceiling (fixed at zero, and reached through the ledger), the client address
behind Cloudflare, and the size cap on a chunked upload."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx

from tayyari.core.limits import MAX_RESUME_BYTES
from tayyari.core.settings import Settings
from tayyari.llm.errors import SPEND_CEILING_MESSAGE
from tayyari.llm.types import StructuredResult
from tayyari.storage.tables import RateLimitHitRow
from tests.fakes.claude import fake_usage, text_turn
from tests.integration.scenarios import NOTES, composite_synth, script_basic_analysis
from tests.integration.support import RecordingClaude, analysis_events, roomy_guard, running, types_of

MakeSettings = Callable[..., Settings]


def _is_json_error(response: httpx.Response) -> dict[str, str]:
    assert response.headers["content-type"].startswith("application/json")
    body: dict[str, str] = response.json()
    assert set(body) == {"error"}
    return body


async def test_the_per_ip_analyze_limit_answers_429_json_with_retry_after(
    build_settings: MakeSettings,
) -> None:
    settings = build_settings(guard=roomy_guard(analyze_per_minute=2))
    claude = RecordingClaude()
    script_basic_analysis(claude)
    async with running(settings, claude=claude) as env:
        first = [await env.analyze(notes=NOTES) for _ in range(2)]
        limited = await env.analyze(notes=NOTES)
        other_client = await env.analyze(notes=NOTES, headers={"cf-connecting-ip": "198.51.100.9"})
        hits = await env.rows(RateLimitHitRow)

    assert [r.status_code for r in first] == [200, 200]
    assert all(types_of(analysis_events(r))[-1] == "done" for r in first)

    assert limited.status_code == 429
    assert "analyses" in _is_json_error(limited)["error"]
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    # the rejected request never reached the pipeline: 3 analyses ran, one reader each
    assert len(claude.calls_for("reader")) == 3
    assert other_client.status_code == 200

    # the counters live in the database: the busy client counted 3 (the third is the one refused), the other 1
    per_minute = sorted(h.count for h in hits if h.bucket.startswith("analyze:min:"))
    assert per_minute == [1, 3]
    assert [h.count for h in hits if h.bucket == "analyze:global:hour"] == [3]


async def test_cf_connecting_ip_gives_each_client_its_own_bucket(build_settings: MakeSettings) -> None:
    settings = build_settings(guard=roomy_guard(analyze_per_minute=1))
    claude = RecordingClaude()
    script_basic_analysis(claude)
    alice, bob = {"cf-connecting-ip": "203.0.113.7"}, {"cf-connecting-ip": "203.0.113.8"}
    async with running(settings, claude=claude) as env:
        statuses = {
            "alice 1": (await env.analyze(notes=NOTES, headers=alice)).status_code,
            "alice 2": (await env.analyze(notes=NOTES, headers=alice)).status_code,
            "bob 1": (await env.analyze(notes=NOTES, headers=bob)).status_code,
            "alice 3": (await env.analyze(notes=NOTES, headers=alice)).status_code,
            "bob 2": (await env.analyze(notes=NOTES, headers=bob)).status_code,
        }
        hits = await env.rows(RateLimitHitRow)

    assert statuses == {"alice 1": 200, "alice 2": 429, "bob 1": 200, "alice 3": 429, "bob 2": 429}
    # two clients, two buckets (a hash of each address, never the address itself)
    buckets = {h.bucket for h in hits if h.bucket.startswith("analyze:min:")}
    assert len(buckets) == 2
    assert not any("203.0.113" in h.bucket for h in hits)


async def test_the_per_ip_chat_limit_is_separate_from_the_analyze_limit(build_settings: MakeSettings) -> None:
    settings = build_settings(guard=roomy_guard(chat_per_minute=1))
    claude = RecordingClaude()
    script_basic_analysis(claude)
    claude.script_chat(text_turn("Hi."), repeat=True)
    async with running(settings, claude=claude) as env:
        analysis = await env.analyze(notes=NOTES)
        chats = [(await env.chat()).status_code for _ in range(2)]
        limited = await env.chat()

    assert analysis.status_code == 200
    assert chats == [200, 429]
    assert "messages" in _is_json_error(limited)["error"]
    assert int(limited.headers["retry-after"]) >= 1


async def test_a_daily_ceiling_of_zero_answers_503_json_before_any_work(build_settings: MakeSettings) -> None:
    claude = RecordingClaude()
    script_basic_analysis(claude)
    async with running(build_settings(guard=roomy_guard(daily_ceiling_usd=0.0)), claude=claude) as env:
        analyze = await env.analyze(notes=NOTES)
        chat = await env.chat()
        sample = await env.client.get("/api/sample-profile")  # the pre-computed sample never needs AI

    for response in (analyze, chat):
        assert response.status_code == 503
        assert _is_json_error(response) == {"error": SPEND_CEILING_MESSAGE}
        assert 1 <= int(response.headers["retry-after"]) <= 86_400
    assert claude.calls == []
    assert sample.status_code == 200


async def test_the_ceiling_is_reached_through_the_spend_ledger(build_settings: MakeSettings) -> None:
    # One analysis whose synthesis costs $2.00 (1M input tokens of Sonnet) against a $1.00 ceiling: the run
    # itself is allowed (nothing was spent yet), the next request finds the ledger over the ceiling.
    settings = build_settings(guard=roomy_guard(daily_ceiling_usd=1.0, est_analyze_usd=0.0))
    claude = RecordingClaude()
    expensive = StructuredResult(
        data=composite_synth(link_source="notes", notes_source="notes"),
        usage=fake_usage(settings.synth_model, input_tokens=1_000_000, output_tokens=0),
        stop_reason="end_turn",
        attempts=1,
    )
    script_basic_analysis(claude, first_synth=expensive)
    async with running(settings, claude=claude) as env:
        first = await env.analyze(notes=NOTES)
        spend_after_first = (await env.spend())["total"][0]
        await env.container.spend_guard.refresh()  # the guard caches its reading for a few seconds
        second = await env.analyze(notes=NOTES)
        chat = await env.chat()

    assert first.status_code == 200
    assert types_of(analysis_events(first))[-1] == "done"
    assert spend_after_first >= 2_000_000
    for response in (second, chat):
        assert response.status_code == 503
        assert _is_json_error(response) == {"error": SPEND_CEILING_MESSAGE}


async def test_a_chunked_upload_over_the_cap_is_a_413_json_and_the_server_stays_healthy(
    build_settings: MakeSettings,
) -> None:
    block = b"x" * (64 * 1024)
    blocks = MAX_RESUME_BYTES // len(block) + 24  # 1.5 MiB more than the resume cap allows
    pulled = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal pulled
        yield (
            b'--b\r\nContent-Disposition: form-data; name="resume"; filename="cv.pdf"\r\n'
            b"Content-Type: application/pdf\r\n\r\n"
        )
        for _ in range(blocks):
            pulled += 1
            yield block

    claude = RecordingClaude()
    script_basic_analysis(claude)
    async with running(build_settings(), claude=claude) as env:
        response = await env.client.post(
            "/api/analyze", content=body(), headers={"content-type": "multipart/form-data; boundary=b"}
        )
        calls_after_413 = len(claude.calls)
        health = await env.client.get("/api/health")
        after = await env.analyze(notes=NOTES)

    assert "content-length" not in response.request.headers  # a chunked upload, no declared size
    assert response.request.headers["transfer-encoding"] == "chunked"
    assert response.status_code == 413
    assert _is_json_error(response) == {"error": "That file is larger than 10 MB."}
    assert pulled < blocks  # the server stopped reading at the cap instead of draining the upload
    assert calls_after_413 == 0  # nothing was analysed
    assert health.status_code == 200
    assert after.status_code == 200  # and the next request is served normally (no slot or state leaked)
