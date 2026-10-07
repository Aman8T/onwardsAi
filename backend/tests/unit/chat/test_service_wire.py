"""The chat service over the real `AnthropicClaudeClient`, with the API replaced by recorded SSE streams.

The other chat tests use `FakeClaudeClient`, which accepts whatever the service passes. These tests check what
actually goes on the wire for the three-block prompt and the tool gate, and that the client's usage rows,
refusals and truncation reach the person as the chat events the web app expects. No network: the SDK runs on an
`httpx2.MockTransport` and the fixtures are the ones the client's own tests use (`tests/data/sse`).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest_asyncio

from tayyari.chat.context import CHAT_RULES, render_origin_block
from tayyari.chat.service import CUT_OFF_NOTE, CoachChatService
from tayyari.contract import ChatErrorEvent, ChatEvent, DeltaEvent, ToolEvent
from tayyari.core.settings import Settings
from tayyari.decisions.service import GateResult
from tayyari.llm.anthropic_client import WEB_TOOLS, AnthropicClaudeClient
from tayyari.llm.errors import SPEND_CEILING_MESSAGE, ClaudeRefusal, SpendCeilingReached
from tayyari.llm.guarded import GuardedClaudeClient
from tayyari.llm.usage import MemoryUsageSink
from tests.unit.chat.support import IP_HASH, PROFILE_ID, Rig

SSE_DIR = Path(__file__).resolve().parents[2] / "data" / "sse"
SKILL_ORIGIN = {
    "kind": "skill",
    "refId": "motion-design",
    "title": "Motion & animation",
    "caption": "Frontend",
}


class FakeApi:
    """An httpx2 handler that records each request body and answers with queued SSE fixtures."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._fixtures: list[str] = []

    def queue(self, *fixtures: str) -> FakeApi:
        self._fixtures.extend(fixtures)
        return self

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        if not self._fixtures:
            raise AssertionError("unexpected API request: nothing queued")
        body = (SSE_DIR / f"{self._fixtures.pop(0)}.sse").read_bytes().replace(b"\r\n", b"\n")
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream", "request-id": "req_wire1"}, content=body
        )


@dataclass
class Wire:
    rig: Rig
    api: FakeApi
    sink: MemoryUsageSink
    client: AnthropicClaudeClient
    service: CoachChatService


@pytest_asyncio.fixture
async def wire(stored_rig: Rig, make_settings: Callable[..., Settings]) -> AsyncIterator[Wire]:
    rig = stored_rig
    api = FakeApi()
    sink = MemoryUsageSink()
    http_client = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(api))
    client = AnthropicClaudeClient(
        settings=make_settings(anthropic_api_key="sk-ant-test"),
        usage_sink=sink,
        clock=rig.clock,
        http_client=http_client,
    )
    yield Wire(rig, api, sink, client, rig.service_with(client))
    await client.aclose()


async def run(wire: Wire, message: str = "What next for me?", **fields: Any) -> list[ChatEvent]:
    request = wire.rig.request(message, **fields)
    return [event async for event in wire.service.stream_turn(request, wire.rig.rc)]


async def test_the_request_has_three_system_blocks_with_the_breakpoint_on_the_profile(wire: Wire) -> None:
    wire.api.queue("chat_search")

    events = await run(wire, "Find me jobs", origin=SKILL_ORIGIN)

    (body,) = wire.api.requests
    rules, profile, origin = body["system"]
    assert rules == {"type": "text", "text": CHAT_RULES}
    assert profile["cache_control"] == {"type": "ephemeral"}
    assert profile["text"].startswith("## About the user\nTheir first name is Riya")
    assert origin == {"type": "text", "text": render_origin_block(SKILL_ORIGIN)}
    assert body["messages"][-1]["content"] in ("Find me jobs", [{"type": "text", "text": "Find me jobs"}])
    assert body["tools"] == [dict(t) for t in WEB_TOOLS]
    assert "tool_choice" not in body
    assert body["model"] == wire.rig.settings.chat_model
    assert body["max_tokens"] == wire.rig.settings.chat_max_tokens
    assert body["cache_control"] == {"type": "ephemeral"}  # the tail breakpoint of the conversation

    names = [(e.name, e.detail[:20]) for e in events if isinstance(e, ToolEvent)]
    assert [n for n, _ in names] == ["WebSearch", "WebFetch"]  # the UI's names, not web_search / web_fetch
    assert events[-1].type == "done"


async def test_usage_is_recorded_against_the_chat_session(wire: Wire) -> None:
    wire.api.queue("chat_plain")

    events = await run(wire, "hello")

    session_id = events[-1].sessionId  # type: ignore[union-attr]
    (row,) = wire.sink.of_purpose("chat")
    assert (row.profile_id, row.session_id, row.status) == (PROFILE_ID, session_id, "ok")
    assert row.cache_read_tokens == 12000  # the fixture's cached prefix is what the layout is for
    stored = await wire.rig.stored(session_id)
    assert [m.content for m in stored.messages] == ["hello", "Hello there."]
    assert row.ip_hash == IP_HASH


async def test_a_no_from_the_gate_keeps_the_tools_declared_and_the_prefix_identical(wire: Wire) -> None:
    wire.api.queue("chat_plain", "chat_plain")

    await run(wire, "What is my strongest skill?", origin=SKILL_ORIGIN)
    wire.rig.decisions.gate_script = GateResult(allow_tools=False, reason="from the profile", p_outside=0.04)
    await run(wire, "What is my strongest skill?", origin=SKILL_ORIGIN)

    allowed, withheld = wire.api.requests
    assert "tool_choice" not in allowed
    assert withheld["tool_choice"] == {"type": "none"}
    assert withheld["tools"] == allowed["tools"] == [dict(t) for t in WEB_TOOLS]
    assert withheld["system"] == allowed["system"]


async def test_a_refusal_reaches_the_person_as_a_friendly_error_event(wire: Wire) -> None:
    wire.api.queue("chat_refusal")

    events = await run(wire, "something sensitive")

    assert events == [ChatErrorEvent(message=ClaudeRefusal("cyber").user_message)]
    assert wire.rig.sessions.appends == []


async def test_a_truncated_answer_ends_with_the_cut_off_note(wire: Wire) -> None:
    wire.api.queue("chat_max_tokens")

    events = await run(wire, "tell me everything")

    assert [e.type for e in events] == ["delta", "delta", "done"]
    assert events[0] == DeltaEvent(text="Partial answ")
    assert events[1] == DeltaEvent(text=CUT_OFF_NOTE)
    (row,) = wire.sink.of_purpose("chat")
    assert row.stop_reason == "max_tokens"


async def test_the_spend_guard_stops_the_turn_before_any_request(wire: Wire) -> None:
    async def tripped(purpose: str) -> None:
        raise SpendCeilingReached(120)

    service = wire.rig.service_with(GuardedClaudeClient(wire.client, tripped))

    events = [e async for e in service.stream_turn(wire.rig.request("hello"), wire.rig.rc)]

    assert events == [ChatErrorEvent(message=SPEND_CEILING_MESSAGE)]
    assert wire.api.requests == []
    assert wire.rig.sessions.appends == []


async def test_the_second_turn_replays_the_first_so_the_conversation_prefix_is_stable(wire: Wire) -> None:
    wire.api.queue("chat_plain", "chat_plain")

    first = await run(wire, "first question")
    session_id = first[-1].sessionId  # type: ignore[union-attr]
    await run(wire, "second question", sessionId=session_id)

    one, two = wire.api.requests
    assert one["system"] == two["system"]
    assert [m["role"] for m in two["messages"]] == ["user", "assistant", "user"]

    def text_of(message: dict[str, Any]) -> str:
        content = message["content"]
        return content if isinstance(content, str) else "".join(b.get("text", "") for b in content)

    assert [text_of(m) for m in two["messages"]] == ["first question", "Hello there.", "second question"]
