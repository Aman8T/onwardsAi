"""A client that hangs up mid-stream: the run is cancelled all the way down, nothing is saved, no task is left
behind and the next request is served normally.

The request is driven by hand (`AsgiPost`) because `httpx.ASGITransport` buffers a whole response and cannot hang
up. It is run twice, for the two ways a server can announce the connection: ASGI spec 2.3 (uvicorn: Starlette
itself watches for the disconnect) and 2.4 (only the app's own polling notices)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from tayyari.contract import Profile
from tayyari.core.settings import ConcurrencySettings, Settings
from tayyari.storage.tables import ChatMessageRow, ChatSessionRow, LlmCallRow, ProfileRow
from tests.fakes.claude import ChatCall, StructuredCall, text_turn
from tests.fakes.jev import FakeJev
from tests.integration.scenarios import NOTES, script_basic_analysis
from tests.integration.support import (
    AsgiPost,
    JevScript,
    RecordingClaude,
    alive_tasks,
    analysis_events,
    assert_tasks_drained,
    chat_events,
    done_of,
    eventually,
    multipart_body,
    reader_output,
    running,
    source_id_of,
    types_of,
)

MakeSettings = Callable[..., Settings]
SPEC_VERSIONS = pytest.mark.parametrize("spec_version", ["2.3", "2.4"])


@SPEC_VERSIONS
async def test_hanging_up_mid_analysis_cancels_the_run_and_the_next_request_works(
    build_settings: MakeSettings, spec_version: str
) -> None:
    # one slot: a slot that leaked would make the later request answer "busy"
    settings = build_settings(concurrency=ConcurrencySettings(max_concurrent_analyses=1))
    started, cancelled = asyncio.Event(), asyncio.Event()
    readers_started = 0

    async def reader(call: StructuredCall) -> dict[str, Any]:
        nonlocal readers_started
        readers_started += 1
        if readers_started == 1:
            started.set()
            try:
                await asyncio.Event().wait()  # Claude never answers; only the hang-up ends this
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return reader_output(source_id_of(call))

    claude = RecordingClaude()
    script_basic_analysis(claude, reader=reader)
    async with running(settings, claude=claude, jev=FakeJev(JevScript())) as env:
        body, content_type = multipart_body(fields=[("notes", NOTES)])
        before = set(asyncio.all_tasks())
        post = AsgiPost("/api/analyze", body, content_type, spec_version=spec_version)
        request = post.run(env.app)
        await asyncio.wait_for(started.wait(), 5)
        await eventually(lambda: len(post.lines) >= 2, what="the first events of the stream")
        assert post.status == 200
        assert json.loads(post.lines[0])["type"] == "readers"
        assert alive_tasks(before), (
            "a running analysis is made of tasks: the drain check below is not vacuous"
        )

        post.disconnect()
        await asyncio.wait_for(request, 10)  # the app returns: the response is over
        await asyncio.wait_for(cancelled.wait(), 5)  # ... and the reader's Claude call was cancelled

        # nothing was saved or billed, the slot is free and no task is left running
        assert [p.id for p in await env.rows(ProfileRow)] == [env.sample_id]  # only the seeded sample
        assert await env.rows(LlmCallRow) == []
        assert not env.container.analysis_slots.locked()
        await assert_tasks_drained(before)
        assert env.container.runner.pending_tasks == 0

        # a later request works, start to finish
        again = await env.analyze(notes=NOTES)
        events = analysis_events(again)
        profile_response = await env.client.get(f"/api/profile/{done_of(events).profile.id}")

    assert again.status_code == 200
    assert types_of(events)[-1] == "done"
    assert types_of(events).count("done") == 1
    assert profile_response.status_code == 200
    assert Profile.from_dict(profile_response.json()) == done_of(events).profile


@SPEC_VERSIONS
async def test_hanging_up_mid_chat_stores_nothing_and_the_next_turn_works(
    build_settings: MakeSettings, spec_version: str
) -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()
    turns = 0

    async def slow_turn(_call: ChatCall) -> list[Any]:
        nonlocal turns
        turns += 1
        if turns == 1:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return text_turn("Back with you.")

    claude = RecordingClaude()
    claude.script_chat(slow_turn, repeat=True)
    async with running(build_settings(), claude=claude, jev=FakeJev(JevScript())) as env:
        payload = json.dumps({"profileId": env.sample_id, "message": "Plan my next month"}).encode()
        before = set(asyncio.all_tasks())
        post = AsgiPost("/api/chat", payload, "application/json", spec_version=spec_version)
        request = post.run(env.app)
        await asyncio.wait_for(started.wait(), 5)
        post.disconnect()
        await asyncio.wait_for(request, 10)
        await asyncio.wait_for(cancelled.wait(), 5)

        # the turn left no messages (a session row may exist, but it has no turns) and no usage row
        assert await env.rows(ChatMessageRow) == []
        assert {s.turn_count for s in await env.rows(ChatSessionRow)} <= {0}
        assert await env.rows(LlmCallRow, LlmCallRow.purpose == "chat") == []
        await assert_tasks_drained(before)

        again = await env.chat("Plan my next month")
        messages = await env.rows(ChatMessageRow)

    events = chat_events(again)
    assert again.status_code == 200
    assert types_of(events)[-1] == "done"
    assert [m.role for m in sorted(messages, key=lambda m: m.seq)] == ["user", "assistant"]
