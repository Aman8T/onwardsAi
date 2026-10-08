"""`AnthropicClaudeClient` against hand-written SSE fixtures (tests/data/sse) served through an httpx2 MockTransport.

The SDK (anthropic 1.x) is built on httpx2, so the transport is an `httpx2.MockTransport` injected as the client's
`http_client`. Nothing here touches the network. The fixtures are the wire format the API streams (message_start,
content blocks, deltas, message_delta with the stop reason and usage), so the real SDK parsing and snapshotting run.

Costs are asserted against `claude_cost_micro` with the real price table, so the tests stay honest when prices change.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import anthropic
import httpx2
import pytest
import pytest_asyncio

from tayyari.contract.records import LlmCallRecord
from tayyari.core.limits import CHAT_MAX_PAUSE_CONTINUATIONS, CHAT_TOOL_DETAIL_MAX_CHARS
from tayyari.core.settings import Settings
from tayyari.llm import anthropic_client
from tayyari.llm.anthropic_client import (
    EMIT_TOOL,
    WEB_FETCH_MAX_CONTENT_TOKENS,
    WEB_TOOLS,
    AnthropicClaudeClient,
    parse_json_object,
)
from tayyari.llm.errors import (
    UNAVAILABLE_MESSAGE,
    ClaudeAuthError,
    ClaudeBadRequest,
    ClaudeConnectionError,
    ClaudeError,
    ClaudeInvalidOutput,
    ClaudeNotConfigured,
    ClaudeOverloaded,
    ClaudeRefusal,
    ClaudeTimeout,
    ClaudeTruncated,
)
from tayyari.llm.models import RawTokens, claude_cost_micro, spec_for
from tayyari.llm.types import (
    CallContext,
    ChatStreamEvent,
    StructuredResult,
    TextBlock,
    TextDelta,
    ToolUse,
    TurnEnd,
)
from tayyari.llm.usage import MemoryUsageSink
from tests.fakes.clock import FakeClock

SSE_DIR = Path(__file__).resolve().parents[2] / "data" / "sse"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-4-5"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "score": {"type": "integer"}},
    "required": ["name", "score"],
    "additionalProperties": False,
}
ANSWER = {"name": "Ada", "score": 7}
ANALYSIS_ID = UUID("01927a3e-0000-7000-8000-000000000001")
SYNTH_CTX = CallContext(
    purpose="synth", profile_id="prof-1", session_id="sess-1", analysis_id=ANALYSIS_ID, ip_hash="ab12cd34"
)
READER_CTX = CallContext(purpose="reader")
CHAT_CTX = CallContext(purpose="chat", profile_id="prof-1", session_id="sess-1")
USER = [TextBlock("data")]
HELLO = [{"role": "user", "content": "hi"}]
LONG_URL_DETAIL = "https://example.com/" + "a" * (CHAT_TOOL_DETAIL_MAX_CHARS - len("https://example.com/"))


# ───────────────────────────── the fake API ─────────────────────────────


def sse(name: str) -> bytes:
    return (SSE_DIR / f"{name}.sse").read_bytes().replace(b"\r\n", b"\n")


def sse_chunks(name: str) -> list[bytes]:
    """The fixture split into its events (each chunk ends with the blank line)."""
    return [part + b"\n\n" for part in sse(name).split(b"\n\n") if part.strip()]


Reply = Callable[[httpx2.Request], httpx2.Response]


def ok(name: str, request_id: str = "req_test1") -> Reply:
    def make(request: httpx2.Request) -> httpx2.Response:
        headers = {"content-type": "text/event-stream", "request-id": request_id}
        return httpx2.Response(200, headers=headers, content=sse(name))

    return make


def error(
    status: int,
    error_type: str,
    message: str,
    *,
    request_id: str = "req_err1",
    headers: dict[str, str] | None = None,
) -> Reply:
    def make(request: httpx2.Request) -> httpx2.Response:
        body = {"type": "error", "error": {"type": error_type, "message": message}, "request_id": request_id}
        return httpx2.Response(status, headers={"request-id": request_id, **(headers or {})}, json=body)

    return make


def streaming(
    name: str,
    events: int,
    *,
    request_id: str = "req_stream1",
    reached: asyncio.Event | None = None,
    then_raise: Exception | None = None,
) -> Reply:
    """The first ``events`` events of a fixture, then either an error or a stream that never ends."""
    prefix = b"".join(sse_chunks(name)[:events])

    def make(request: httpx2.Request) -> httpx2.Response:
        async def body() -> AsyncIterator[bytes]:
            yield prefix
            if reached is not None:
                reached.set()
            if then_raise is not None:
                raise then_raise
            await asyncio.sleep(3600)  # the test cancels the call long before this

        headers = {"content-type": "text/event-stream", "request-id": request_id}
        return httpx2.Response(200, headers=headers, content=body())

    return make


def timed(
    name: str, clock: FakeClock, *, first_chunk_events: int, before_first: float, after: float
) -> Reply:
    """A fixture streamed in three chunks with the (fake) clock advanced between them: gives latency and TTFT."""
    chunks = sse_chunks(name)

    def make(request: httpx2.Request) -> httpx2.Response:
        async def body() -> AsyncIterator[bytes]:
            yield b"".join(chunks[:first_chunk_events])
            clock.advance(before_first)
            yield b"".join(chunks[first_chunk_events : first_chunk_events + 2])
            clock.advance(after)
            yield b"".join(chunks[first_chunk_events + 2 :])

        headers = {"content-type": "text/event-stream", "request-id": "req_timed1"}
        return httpx2.Response(200, headers=headers, content=body())

    return make


class FakeApi:
    """An httpx2 handler: records every request body and answers from a queue."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._replies: list[Reply] = []
        self._repeat_last = False

    def queue(self, *replies: Reply, repeat_last: bool = False) -> FakeApi:
        self._replies.extend(replies)
        self._repeat_last = repeat_last
        return self

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        if not self._replies:
            raise AssertionError("unexpected API request: nothing queued")
        reply = self._replies[0] if self._repeat_last and len(self._replies) == 1 else self._replies.pop(0)
        return reply(request)


@dataclass
class Rig:
    client: AnthropicClaudeClient
    api: FakeApi
    sink: MemoryUsageSink
    clock: FakeClock
    settings: Settings

    @property
    def rows(self) -> list[LlmCallRecord]:
        return self.sink.records


@pytest_asyncio.fixture
async def make_rig(make_settings: Callable[..., Settings]) -> AsyncIterator[Callable[..., Rig]]:
    rigs: list[Rig] = []

    def build(sink: Any = None, **overrides: Any) -> Rig:
        settings = make_settings(**{"anthropic_api_key": "sk-ant-test", **overrides})
        api = FakeApi()
        usage_sink = sink if sink is not None else MemoryUsageSink()
        clock = FakeClock()
        http_client = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(api))
        client = AnthropicClaudeClient(
            settings=settings, usage_sink=usage_sink, clock=clock, http_client=http_client
        )
        rig = Rig(client, api, usage_sink, clock, settings)
        rigs.append(rig)
        return rig

    yield build
    for rig in rigs:
        await rig.client.aclose()


@pytest.fixture
def rig(make_rig: Callable[..., Rig]) -> Rig:
    return make_rig()


class RecordingLog:
    """Stands in for the module's structlog logger (other tests configure structlog with logger caching)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def _add(self, level: str, event: str, **fields: Any) -> None:
        self.events.append((level, event, fields))

    def info(self, event: str, **fields: Any) -> None:
        self._add("info", event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._add("warning", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._add("error", event, **fields)

    def named(self, event: str) -> list[dict[str, Any]]:
        return [fields for _level, name, fields in self.events if name == event]


@pytest.fixture
def recorded_log(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    stub = RecordingLog()
    monkeypatch.setattr(anthropic_client, "log", stub)
    return stub


def cost(model: str, **tokens: int) -> int:
    return claude_cost_micro(spec_for(model), RawTokens(**tokens))


async def run_structured(
    rig: Rig,
    *,
    model: str = SONNET,
    system: list[TextBlock] | None = None,
    user: list[TextBlock] | None = None,
    max_tokens: int = 16000,
    effort: Any = "medium",
    pdf: bytes | None = None,
    on_progress: Callable[[int], None] | None = None,
    ctx: CallContext = SYNTH_CTX,
) -> StructuredResult:
    return await rig.client.structured(
        model=model,
        system=system if system is not None else [],
        user=user if user is not None else USER,
        schema=SCHEMA,
        max_tokens=max_tokens,
        effort=effort,
        pdf=pdf,
        on_progress=on_progress,
        ctx=ctx,
    )


async def run_chat(
    rig: Rig,
    *,
    model: str = SONNET,
    system: list[TextBlock] | None = None,
    messages: list[dict[str, Any]] | None = None,
    tools: Any = "web",
    max_tokens: int = 8000,
    effort: Any = "medium",
    cache_tail: bool = True,
    ctx: CallContext = CHAT_CTX,
) -> list[ChatStreamEvent]:
    events: list[ChatStreamEvent] = []
    async for event in rig.client.stream_chat(
        model=model,
        system=system if system is not None else [TextBlock("rules"), TextBlock("profile", cache=True)],
        messages=messages if messages is not None else HELLO,
        tools=tools,
        max_tokens=max_tokens,
        effort=effort,
        cache_tail=cache_tail,
        ctx=ctx,
    ):
        events.append(event)
    return events


# ───────────────────────────── structured: the request ─────────────────────────────


async def test_structured_sonnet_success_builds_the_request_and_records_usage(rig: Rig) -> None:
    rig.api.queue(timed("structured_ok", rig.clock, first_chunk_events=6, before_first=0.25, after=0.75))
    progress: list[int] = []
    result = await run_structured(
        rig,
        system=[TextBlock("rules", cache=True), TextBlock("more")],
        on_progress=progress.append,
    )

    assert result.data == ANSWER
    assert result.attempts == 1
    assert result.stop_reason == "end_turn"
    assert result.usage.model == SONNET
    assert (result.usage.input_tokens, result.usage.output_tokens) == (1200, 18)
    assert result.usage.request_id == "req_timed1"
    assert result.usage.cost_micro_usd == cost(SONNET, input_tokens=1200, output_tokens=18) == 2580
    assert progress == [15, 27]  # characters of the two text deltas; the thinking block adds none

    (body,) = rig.api.requests
    assert body["model"] == SONNET
    assert body["max_tokens"] == 16000
    assert body["stream"] is True
    assert body["system"] == [
        {"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "more"},
    ]
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "data"}]}]
    assert body["output_config"] == {
        "format": {"type": "json_schema", "schema": SCHEMA},
        "effort": "medium",
    }
    assert "thinking" not in body  # adaptive models are never sent a thinking budget
    assert "tools" not in body
    assert "cache_control" not in body  # the automatic tail breakpoint is for chat only

    (row,) = rig.rows
    assert row.purpose == "synth"
    assert row.model == SONNET
    assert row.status == "ok"
    assert row.error_kind is None
    assert row.stop_reason == "end_turn"
    assert (row.input_tokens, row.output_tokens, row.cache_write_tokens, row.cache_read_tokens) == (
        1200,
        18,
        0,
        0,
    )
    assert row.cost_micro_usd == 2580
    assert row.attempt == 1
    assert row.request_id == "req_timed1"
    assert row.latency_ms == 1000
    assert row.ttft_ms == 250  # the first thinking/text delta arrived 0.25 s after the request began
    assert row.created_at == rig.clock.now()
    assert (row.profile_id, row.session_id, row.analysis_id, row.ip_hash) == (
        "prof-1",
        "sess-1",
        ANALYSIS_ID,
        "ab12cd34",
    )
    assert isinstance(row.id, UUID)
    assert row.id.version == 7


async def test_haiku_request_has_no_effort_and_no_thinking(rig: Rig) -> None:
    rig.api.queue(ok("structured_haiku_ok"))
    result = await run_structured(rig, model=HAIKU, effort="medium", ctx=READER_CTX)

    assert result.data == ANSWER
    (body,) = rig.api.requests
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    assert "thinking" not in body
    assert result.usage.cost_micro_usd == cost(HAIKU, input_tokens=300, output_tokens=20)


async def test_unset_effort_sends_no_effort_to_sonnet(rig: Rig) -> None:
    rig.api.queue(ok("structured_ok"))
    await run_structured(rig, effort=None)
    assert rig.api.requests[0]["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}


@pytest.mark.parametrize(
    ("model", "ctx", "reader_thinking", "max_tokens", "expected"),
    [
        (HAIKU, READER_CTX, True, 8000, {"type": "enabled", "budget_tokens": 4096}),
        (HAIKU, READER_CTX, True, 3000, {"type": "enabled", "budget_tokens": 1976}),
        (HAIKU, READER_CTX, True, 2000, None),  # the budget must stay below max_tokens (and be at least 1024)
        (HAIKU, READER_CTX, False, 8000, None),
        (HAIKU, SYNTH_CTX, True, 8000, None),  # only readers think
        (SONNET, READER_CTX, True, 8000, None),  # adaptive models never get a budget
    ],
)
async def test_thinking_budget_only_for_budget_models_and_readers(
    make_rig: Callable[..., Rig],
    model: str,
    ctx: CallContext,
    reader_thinking: bool,
    max_tokens: int,
    expected: dict[str, Any] | None,
) -> None:
    rig = make_rig(reader_thinking=reader_thinking)
    rig.api.queue(ok("structured_haiku_ok"))
    await run_structured(rig, model=model, ctx=ctx, max_tokens=max_tokens, effort=None)
    assert rig.api.requests[0].get("thinking") == expected


async def test_cache_flags_become_ephemeral_breakpoints_on_system_and_user_blocks(rig: Rig) -> None:
    rig.api.queue(ok("structured_ok"))
    await run_structured(
        rig,
        system=[TextBlock("static", cache=True), TextBlock("dynamic")],
        user=[TextBlock("shared data", cache=True), TextBlock("the question")],
    )
    (body,) = rig.api.requests
    assert [("cache_control" in b) for b in body["system"]] == [True, False]
    content = body["messages"][0]["content"]
    assert content == [
        {"type": "text", "text": "shared data", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "the question"},
    ]


async def test_empty_blocks_are_dropped_and_no_system_key_is_sent_without_system(rig: Rig) -> None:
    rig.api.queue(ok("structured_ok"))
    await run_structured(rig, system=[TextBlock("  ")], user=[TextBlock(""), TextBlock("data")])
    (body,) = rig.api.requests
    assert "system" not in body
    assert body["messages"][0]["content"] == [{"type": "text", "text": "data"}]


async def test_pdf_document_block_comes_before_the_text(rig: Rig) -> None:
    rig.api.queue(ok("structured_ok"))
    pdf = b"%PDF-1.4 fake scanned resume"
    await run_structured(rig, pdf=pdf, user=[TextBlock("Read the resume."), TextBlock("Be brief.")])
    content = rig.api.requests[0]["messages"][0]["content"]
    assert content[0] == {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.b64encode(pdf).decode("ascii"),
        },
    }
    assert [block["text"] for block in content[1:]] == ["Read the resume.", "Be brief."]


async def test_a_pdf_alone_is_a_valid_user_turn(rig: Rig) -> None:
    rig.api.queue(ok("structured_ok"))
    await run_structured(rig, pdf=b"%PDF", user=[])
    assert [block["type"] for block in rig.api.requests[0]["messages"][0]["content"]] == ["document"]


async def test_no_user_content_at_all_is_a_programming_error(rig: Rig) -> None:
    with pytest.raises(ValueError):
        await run_structured(rig, user=[TextBlock(" ")])
    assert rig.api.requests == []


# ───────────────────────────── structured: the answer ─────────────────────────────


async def test_trailing_prose_after_the_json_object_is_tolerated(rig: Rig) -> None:
    rig.api.queue(ok("structured_trailing_prose"))
    result = await run_structured(rig)
    assert result.data == ANSWER
    assert result.attempts == 1


async def test_usage_includes_cache_tokens_and_their_cost(rig: Rig) -> None:
    rig.api.queue(ok("structured_cached"))
    result = await run_structured(rig, system=[TextBlock("x" * 100, cache=True)])
    usage = result.usage
    assert (usage.input_tokens, usage.cache_write_tokens, usage.cache_read_tokens) == (40, 2000, 9000)
    expected = cost(
        SONNET, input_tokens=40, output_tokens=18, cache_write_tokens=2000, cache_read_tokens=9000
    )
    assert usage.cost_micro_usd == expected == 80 + 180 + 5000 + 1800
    (row,) = rig.rows
    assert (row.cache_write_tokens, row.cache_read_tokens, row.cost_micro_usd) == (2000, 9000, expected)


async def test_refusal_is_typed_with_its_category_and_not_retried(rig: Rig) -> None:
    rig.api.queue(ok("structured_refusal", "req_refuse"))
    with pytest.raises(ClaudeRefusal) as caught:
        await run_structured(rig)
    assert caught.value.category == "general_harms"
    assert caught.value.request_id == "req_refuse"
    assert "declined" in caught.value.user_message
    assert len(rig.api.requests) == 1
    (row,) = rig.rows
    assert (row.status, row.error_kind, row.stop_reason) == ("error", "refusal", "refusal")
    assert row.input_tokens == 700
    assert row.cost_micro_usd == cost(SONNET, input_tokens=700)


async def test_max_tokens_twice_raises_truncated_after_a_cheaper_second_attempt(rig: Rig) -> None:
    rig.api.queue(ok("structured_truncated"), ok("structured_truncated"))
    with pytest.raises(ClaudeTruncated) as caught:
        await run_structured(rig, max_tokens=1000, effort="high")

    assert caught.value.max_tokens == 1500
    first, second = rig.api.requests
    assert first["max_tokens"] == 1000
    assert first["output_config"]["effort"] == "high"
    assert second["max_tokens"] == 1500  # x1.5
    assert second["output_config"]["effort"] == "medium"  # one step down
    note = second["messages"][0]["content"][-1]["text"]
    assert note.startswith("IMPORTANT: your previous attempt failed (")
    assert "cut off" in note
    assert second["messages"][0]["content"][0] == {
        "type": "text",
        "text": "data",
    }  # the repair is an extra block
    assert len(rig.clock.sleeps) == 1
    assert 1.0 <= rig.clock.sleeps[0] <= 2.0
    assert [(r.status, r.error_kind, r.attempt, r.stop_reason) for r in rig.rows] == [
        ("error", "truncated", 1, "max_tokens"),
        ("error", "truncated", 2, "max_tokens"),
    ]


async def test_max_tokens_then_success_uses_the_second_attempt(rig: Rig) -> None:
    rig.api.queue(ok("structured_truncated"), ok("structured_ok"))
    result = await run_structured(rig, max_tokens=64_000, effort="low")
    assert result.data == ANSWER
    assert result.attempts == 2
    second = rig.api.requests[1]
    assert second["max_tokens"] == 64_000  # never above the cap, never below the original
    assert second["output_config"]["effort"] == "low"  # already at the bottom of the ladder


async def test_invalid_json_then_a_successful_repair_attempt(rig: Rig) -> None:
    rig.api.queue(ok("structured_bad_schema", "req_bad"), ok("structured_ok", "req_good"))
    progress: list[int] = []
    result = await run_structured(rig, max_tokens=10_000, effort="medium", on_progress=progress.append)

    assert result.data == ANSWER
    assert result.attempts == 2
    assert result.stop_reason == "end_turn"
    # the result's usage is everything paid for: both attempts
    assert result.usage.input_tokens == 500 + 1200
    assert result.usage.output_tokens == 14 + 18
    assert result.usage.cost_micro_usd == sum(r.cost_micro_usd for r in rig.rows)
    assert result.usage.request_id == "req_good"

    first, second = rig.api.requests
    assert "IMPORTANT" not in json.dumps(first)
    note = second["messages"][0]["content"][-1]["text"]
    assert "$.score: expected integer, got str" in note
    assert second["max_tokens"] == 15_000
    assert second["output_config"]["effort"] == "low"
    assert [(r.status, r.error_kind, r.attempt, r.request_id) for r in rig.rows] == [
        ("error", "invalid_output", 1, "req_bad"),
        ("ok", None, 2, "req_good"),
    ]
    assert len(rig.clock.sleeps) == 1
    # the character count never goes backwards across attempts
    assert progress == sorted(progress)
    assert progress[-1] == len('{"name": "Ada", "score": "seven"}') + 27


async def test_no_json_twice_raises_invalid_output_with_issues(rig: Rig) -> None:
    rig.api.queue(ok("structured_no_json"), ok("structured_no_json"))
    with pytest.raises(ClaudeInvalidOutput) as caught:
        await run_structured(rig)
    assert caught.value.issues == ("no JSON object in the response",)
    assert len(rig.api.requests) == 2


async def test_haiku_second_attempt_still_sends_no_effort(rig: Rig) -> None:
    rig.api.queue(ok("structured_truncated"), ok("structured_haiku_ok"))
    result = await run_structured(rig, model=HAIKU, effort="high", ctx=READER_CTX)
    assert result.attempts == 2
    assert all("effort" not in body["output_config"] for body in rig.api.requests)


# ───────────────────────────── structured: the emit-tool fallback ─────────────────────────────


async def test_output_config_rejected_falls_back_to_the_emit_tool_and_is_remembered(rig: Rig) -> None:
    rig.api.queue(
        error(
            400, "invalid_request_error", "output_config.format: json_schema is not supported by this model"
        ),
        ok("structured_emit", "req_emit1"),
        ok("structured_emit", "req_emit2"),
    )
    first = await run_structured(rig, system=[TextBlock("rules", cache=True)])
    assert first.data == ANSWER
    assert first.attempts == 1  # a rejected request shape is not a content attempt

    rejected, resent = rig.api.requests
    assert "format" in rejected["output_config"]
    assert "tools" not in rejected
    assert "format" not in resent["output_config"]
    assert resent["output_config"] == {"effort": "medium"}
    assert resent["tools"] == [
        {
            "name": EMIT_TOOL,
            "description": "Return the structured result.",
            "strict": True,
            "input_schema": SCHEMA,
        }
    ]
    assert "tool_choice" not in resent  # forcing a tool is a 400 on the newest models
    assert "`emit` tool" in resent["messages"][0]["content"][-1]["text"]
    assert resent["system"] == [{"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}]
    assert [(r.status, r.error_kind) for r in rig.rows] == [("error", "bad_request"), ("ok", None)]

    # remembered: the next call goes straight to the emit tool, with no rejected round trip
    second = await run_structured(rig)
    assert second.data == ANSWER
    assert len(rig.api.requests) == 3
    assert "format" not in rig.api.requests[2]["output_config"]
    assert rig.api.requests[2]["tools"][0]["name"] == EMIT_TOOL


async def test_the_emit_fallback_is_remembered_per_model(rig: Rig) -> None:
    rig.api.queue(
        error(400, "invalid_request_error", "this model does not support structured outputs"),
        ok("structured_emit"),
        ok("structured_haiku_ok"),
    )
    await run_structured(rig, model=SONNET)
    await run_structured(rig, model=HAIKU, effort=None, ctx=READER_CTX)
    assert "tools" not in rig.api.requests[2]  # Haiku still gets output_config.format
    assert "format" in rig.api.requests[2]["output_config"]


async def test_an_unrelated_400_is_a_bad_request_and_is_not_remembered(rig: Rig) -> None:
    rig.api.queue(
        error(400, "invalid_request_error", "messages: text content blocks must be non-empty"),
        ok("structured_ok"),
    )
    with pytest.raises(ClaudeBadRequest) as caught:
        await run_structured(rig)
    assert caught.value.status == 400
    assert len(rig.api.requests) == 1
    assert rig.rows[0].error_kind == "bad_request"

    await run_structured(rig)
    assert "format" in rig.api.requests[1]["output_config"]  # not switched to the emit tool


async def test_the_emit_tool_answer_is_validated_like_any_other(rig: Rig) -> None:
    rig.api.queue(
        error(400, "invalid_request_error", "output_config is not supported"),
        ok("structured_bad_schema"),
        ok("structured_emit"),
    )
    result = await run_structured(rig)
    # the first emit answer was text that failed the schema; the repair attempt called the tool
    assert result.attempts == 2
    assert result.data == ANSWER
    assert "tools" in rig.api.requests[2]


# ───────────────────────────── structured: errors, usage rows, robustness ─────────────────────────────


async def test_missing_api_key_is_not_configured_and_makes_no_request(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(anthropic_api_key=None)
    with pytest.raises(ClaudeNotConfigured):
        await run_structured(rig)
    assert rig.api.requests == []
    assert rig.rows == []


async def test_auth_error_is_mapped_and_recorded(rig: Rig) -> None:
    rig.api.queue(error(401, "authentication_error", "invalid x-api-key", request_id="req_401"))
    with pytest.raises(ClaudeAuthError) as caught:
        await run_structured(rig)
    assert caught.value.status == 401
    assert caught.value.request_id == "req_401"
    assert len(rig.api.requests) == 1  # a 401 is not retried by the SDK
    (row,) = rig.rows
    assert (row.status, row.error_kind, row.request_id) == ("error", "auth", "req_401")
    assert (row.input_tokens, row.output_tokens, row.cost_micro_usd) == (0, 0, 0)


@pytest.mark.parametrize(
    ("status", "error_type", "message", "kind"),
    [
        (401, "authentication_error", "invalid x-api-key", "auth"),
        (402, "billing_error", "Your credit balance is too low", "auth"),
        (403, "permission_error", "key may not use this model", "auth"),
        (404, "not_found_error", "model: claude-nope", "bad_request"),
    ],
)
async def test_operator_failures_are_generic_for_the_visitor_and_specific_in_the_log(
    rig: Rig, recorded_log: RecordingLog, status: int, error_type: str, message: str, kind: str
) -> None:
    """#17: the public message is one sentence; the real reason is logged where the provider's answer is at hand."""
    rig.api.queue(error(status, error_type, message, request_id="req_op"))
    with pytest.raises(ClaudeError) as caught:
        await run_structured(rig)
    assert caught.value.user_message == UNAVAILABLE_MESSAGE
    assert caught.value.kind == kind and caught.value.status == status

    (logged,) = recorded_log.named("claude_unavailable")
    assert logged["status"] == status
    assert logged["kind"] == kind
    assert logged["detail"] == message
    assert logged["anthropic_request_id"] == "req_op"


async def test_chat_operator_failures_are_logged_too(rig: Rig, recorded_log: RecordingLog) -> None:
    rig.api.queue(error(402, "billing_error", "Your credit balance is too low"))
    with pytest.raises(ClaudeAuthError) as caught:
        await run_chat(rig)
    assert caught.value.user_message == UNAVAILABLE_MESSAGE
    (logged,) = recorded_log.named("claude_unavailable")
    assert (logged["status"], logged["detail"]) == (402, "Your credit balance is too low")


@pytest.mark.parametrize(
    ("status", "error_type", "expected"),
    [(529, "overloaded_error", ClaudeOverloaded), (400, "invalid_request_error", ClaudeBadRequest)],
)
async def test_ordinary_failures_are_not_reported_as_operator_problems(
    rig: Rig, recorded_log: RecordingLog, status: int, error_type: str, expected: type[ClaudeError]
) -> None:
    rig.api.queue(error(status, error_type, "nope", headers={"retry-after-ms": "1"}), repeat_last=True)
    with pytest.raises(expected):
        await run_structured(rig)
    assert recorded_log.named("claude_unavailable") == []


async def test_overloaded_is_mapped_after_the_sdk_retries_and_is_one_row(rig: Rig) -> None:
    rig.api.queue(
        error(529, "overloaded_error", "Overloaded", headers={"retry-after-ms": "1"}),
        repeat_last=True,
    )
    with pytest.raises(ClaudeOverloaded) as caught:
        await run_structured(rig)
    assert caught.value.retryable is True
    assert len(rig.api.requests) == 3  # the SDK's own two retries, invisible to the usage ledger
    assert [(r.status, r.error_kind) for r in rig.rows] == [("error", "overloaded")]


@pytest.mark.parametrize(
    ("raised", "error_class", "kind"),
    [
        (httpx2.ReadError("connection reset"), ClaudeConnectionError, "connection"),
        (httpx2.ReadTimeout("no data for 90 s"), ClaudeTimeout, "timeout"),
    ],
)
async def test_connection_loss_mid_stream_is_mapped_with_partial_usage(
    rig: Rig, raised: Exception, error_class: type[ClaudeError], kind: str
) -> None:
    rig.api.queue(streaming("structured_ok", 7, then_raise=raised))
    with pytest.raises(error_class):
        await run_structured(rig)
    (row,) = rig.rows
    assert (row.status, row.error_kind) == ("error", kind)
    assert row.input_tokens == 1200  # message_start was seen
    assert row.output_tokens == len('{"name": "Ada",') // 4
    assert row.cost_micro_usd == cost(SONNET, input_tokens=1200, output_tokens=3)


async def test_an_error_event_in_the_stream_is_mapped_by_its_type(rig: Rig) -> None:
    rig.api.queue(ok("midstream_overloaded"))
    with pytest.raises(ClaudeOverloaded):
        await run_structured(rig)
    (row,) = rig.rows
    assert (row.status, row.error_kind) == ("error", "overloaded")
    assert row.input_tokens == 900
    assert row.output_tokens == len('{"name": "Ad') // 4


async def test_a_failing_usage_sink_never_breaks_the_call(
    make_rig: Callable[..., Rig], recorded_log: RecordingLog
) -> None:
    class BrokenSink:
        async def record(self, rec: LlmCallRecord) -> None:
            raise RuntimeError("database is down")

    rig = make_rig(sink=BrokenSink())
    rig.api.queue(ok("structured_ok"))
    result = await run_structured(rig)
    assert result.data == ANSWER
    (failure,) = recorded_log.named("llm_usage_record_failed")
    assert failure["error"] == "RuntimeError"
    assert failure["purpose"] == "synth"


async def test_a_raising_progress_callback_is_ignored(rig: Rig) -> None:
    rig.api.queue(ok("structured_ok"))
    calls: list[int] = []

    def flaky(chars: int) -> None:
        calls.append(chars)
        raise RuntimeError("ui went away")

    result = await run_structured(rig, on_progress=flaky)
    assert result.data == ANSWER
    assert calls == [15]  # disabled after the first failure


async def test_cancelling_a_structured_call_records_a_cancelled_row(rig: Rig) -> None:
    reached = asyncio.Event()
    rig.api.queue(streaming("structured_ok", 7, reached=reached))
    task = asyncio.ensure_future(run_structured(rig))
    await asyncio.wait_for(reached.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    (row,) = rig.rows
    assert row.status == "cancelled"
    assert row.stop_reason is None
    assert row.input_tokens == 1200  # as of message_start
    assert row.output_tokens == 3  # 15 characters generated so far, divided by 4
    assert row.cost_micro_usd == cost(SONNET, input_tokens=1200, output_tokens=3)
    assert row.attempt == 1
    assert row.request_id == "req_stream1"


# ───────────────────────────── cache hygiene ─────────────────────────────


async def test_cache_unexpected_miss_is_logged_when_a_big_prefix_neither_wrote_nor_read(
    rig: Rig, recorded_log: RecordingLog
) -> None:
    rig.api.queue(ok("structured_uncached"))
    await run_structured(rig, system=[TextBlock("x" * 4000, cache=True)])  # ~1000 tokens > Sonnet's 512
    (miss,) = recorded_log.named("cache_unexpected_miss")
    assert miss["model"] == SONNET
    assert miss["est_prefix_tokens"] == 1000
    assert miss["min_cache_tokens"] == 512


@pytest.mark.parametrize(
    ("fixture", "system"),
    [
        ("structured_uncached", [TextBlock("x" * 400, cache=True)]),  # prefix below the minimum
        ("structured_cached", [TextBlock("x" * 4000, cache=True)]),  # the cache was used
        ("structured_uncached", [TextBlock("x" * 4000)]),  # no breakpoint was sent
    ],
)
async def test_no_cache_miss_log_when_nothing_was_expected(
    rig: Rig, recorded_log: RecordingLog, fixture: str, system: list[TextBlock]
) -> None:
    rig.api.queue(ok(fixture))
    await run_structured(rig, system=system)
    assert recorded_log.named("cache_unexpected_miss") == []


async def test_no_cache_miss_log_for_haiku_below_its_4096_token_minimum(
    rig: Rig, recorded_log: RecordingLog
) -> None:
    rig.api.queue(ok("structured_haiku_ok"))
    await run_structured(rig, model=HAIKU, system=[TextBlock("x" * 4000, cache=True)], effort=None)
    assert recorded_log.named("cache_unexpected_miss") == []


# ───────────────────────────── chat ─────────────────────────────


async def test_chat_streams_text_maps_tool_names_and_builds_the_request(rig: Rig) -> None:
    rig.api.queue(ok("chat_search", "req_chat1"))
    original = copy.deepcopy(HELLO)
    events = await run_chat(
        rig,
        system=[TextBlock("rules"), TextBlock("profile", cache=True), TextBlock("origin")],
        messages=HELLO,
    )

    expected_text = "Let me check.\n\nHere is what I found."
    assert events[:-1] == [
        TextDelta("Let me"),
        TextDelta(" check."),
        ToolUse("WebSearch", "junior react jobs bangalore"),
        ToolUse(
            "WebFetch", LONG_URL_DETAIL
        ),  # clipped to 200 characters; the code_execution call is not shown
        TextDelta("\n\n"),  # a text block after a tool call is set apart
        TextDelta("Here is what I found."),
    ]
    end = events[-1]
    assert isinstance(end, TurnEnd)
    assert end.text == expected_text
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == expected_text
    assert end.stop_reason == "end_turn"
    assert (end.usage.input_tokens, end.usage.cache_write_tokens, end.usage.output_tokens) == (500, 12000, 90)
    assert end.usage.web_searches == 1
    assert end.usage.cost_micro_usd == cost(
        SONNET, input_tokens=500, output_tokens=90, cache_write_tokens=12000, web_searches=1
    )
    assert sum(isinstance(e, TurnEnd) for e in events) == 1

    (body,) = rig.api.requests
    assert body["model"] == SONNET
    assert body["stream"] is True
    assert body["tools"] == [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5, "max_content_tokens": 20000},
    ]
    assert body["system"] == [
        {"type": "text", "text": "rules"},
        {"type": "text", "text": "profile", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "origin"},
    ]
    assert body["cache_control"] == {"type": "ephemeral"}  # the automatic breakpoint on the conversation tail
    assert body["output_config"] == {"effort": "medium"}
    assert "tool_choice" not in body
    assert body["messages"] == original
    assert HELLO == original  # the caller's messages are not mutated

    (row,) = rig.rows
    assert (row.purpose, row.status, row.stop_reason) == ("chat", "ok", "end_turn")
    assert row.web_searches == 1
    assert row.cost_micro_usd == end.usage.cost_micro_usd
    assert (row.profile_id, row.session_id) == ("prof-1", "sess-1")
    assert row.request_id == "req_chat1"


async def test_chat_tool_choice_none_keeps_every_tool_declared(rig: Rig) -> None:
    rig.api.queue(ok("chat_plain"), ok("chat_plain"))
    await run_chat(rig, tools="web")
    await run_chat(rig, tools="none", cache_tail=False, effort=None)
    with_tools, without = rig.api.requests

    assert without["tool_choice"] == {"type": "none"}
    assert without["tools"] == with_tools["tools"] == [dict(t) for t in WEB_TOOLS]  # identical cache prefix
    assert without["system"] == with_tools["system"]
    assert "cache_control" not in without
    assert "output_config" not in without


async def test_web_fetch_is_capped_in_the_content_it_adds_to_the_context(rig: Rig) -> None:
    """#5: one fetched page must not be able to add an unbounded number of input tokens to a turn."""
    assert WEB_FETCH_MAX_CONTENT_TOKENS == 20_000
    fetch = next(t for t in WEB_TOOLS if t["name"] == "web_fetch")
    assert fetch["max_content_tokens"] == WEB_FETCH_MAX_CONTENT_TOKENS
    assert "max_content_tokens" not in next(t for t in WEB_TOOLS if t["name"] == "web_search")

    rig.api.queue(ok("chat_plain"), ok("chat_plain"))
    await run_chat(rig, tools="web")
    await run_chat(rig, tools="none")  # withheld tools stay declared, and with the same cap
    for body in rig.api.requests:
        sent_fetch = next(t for t in body["tools"] if t["name"] == "web_fetch")
        assert sent_fetch["max_content_tokens"] == 20_000
        assert sent_fetch["max_uses"] == fetch["max_uses"]


async def test_chat_events_carry_timing(rig: Rig) -> None:
    rig.api.queue(timed("chat_plain", rig.clock, first_chunk_events=2, before_first=0.25, after=0.75))
    events = await run_chat(rig)
    assert [e for e in events if isinstance(e, TextDelta)] == [TextDelta("Hello "), TextDelta("there.")]
    (row,) = rig.rows
    assert (row.ttft_ms, row.latency_ms) == (250, 1000)


async def test_chat_effort_is_not_sent_to_a_model_without_effort(rig: Rig) -> None:
    rig.api.queue(ok("chat_plain"))
    await run_chat(rig, model=HAIKU, effort="high")
    assert "output_config" not in rig.api.requests[0]


async def test_chat_pause_turn_resends_with_the_assistant_turn_and_sums_usage(rig: Rig) -> None:
    rig.api.queue(ok("chat_pause", "req_p1"), ok("chat_resume", "req_p2"))
    events = await run_chat(rig)

    assert events[:-1] == [
        TextDelta("Looking that up."),
        ToolUse("WebFetch", "https://example.com/page"),
        TextDelta("\n\n"),
        TextDelta("The page says hello."),
    ]
    end = events[-1]
    assert isinstance(end, TurnEnd)
    assert end.text == "Looking that up.\n\nThe page says hello."
    assert end.stop_reason == "end_turn"
    assert sum(isinstance(e, TurnEnd) for e in events) == 1
    assert (end.usage.input_tokens, end.usage.output_tokens, end.usage.cache_read_tokens) == (700, 65, 24500)
    assert end.usage.request_id == "req_p2"
    assert end.usage.cost_micro_usd == sum(r.cost_micro_usd for r in rig.rows)

    first, second = rig.api.requests
    assert first["messages"] == HELLO
    assert second["messages"][0] == HELLO[0]
    assistant = second["messages"][1]
    assert assistant["role"] == "assistant"
    blocks = assistant["content"]
    assert (blocks[0]["type"], blocks[0]["text"]) == ("text", "Looking that up.")
    assert (blocks[1]["type"], blocks[1]["name"], blocks[1]["id"]) == (
        "server_tool_use",
        "web_fetch",
        "srvtoolu_01Pause",
    )
    assert blocks[1]["input"] == {"url": "https://example.com/page"}
    assert second["tools"] == first["tools"]
    assert second["system"] == first["system"]

    assert [(r.status, r.stop_reason, r.request_id) for r in rig.rows] == [
        ("ok", "pause_turn", "req_p1"),
        ("ok", "end_turn", "req_p2"),
    ]  # one row per API request


async def test_chat_pause_turn_budget_ends_the_turn_when_some_text_arrived(rig: Rig) -> None:
    rig.api.queue(ok("chat_pause"), repeat_last=True)
    events = await run_chat(rig)
    end = events[-1]
    assert isinstance(end, TurnEnd)
    assert end.stop_reason == "pause_turn"
    assert end.text.startswith("Looking that up.")
    assert len(rig.api.requests) == CHAT_MAX_PAUSE_CONTINUATIONS + 1
    assert len(rig.rows) == CHAT_MAX_PAUSE_CONTINUATIONS + 1
    assert end.usage.output_tokens == 40 * (CHAT_MAX_PAUSE_CONTINUATIONS + 1)


async def test_chat_pause_turn_budget_without_any_text_is_a_retryable_error(rig: Rig) -> None:
    rig.api.queue(ok("chat_pause_silent"), repeat_last=True)
    with pytest.raises(ClaudeError) as caught:
        await run_chat(rig)
    assert caught.value.kind == "server"
    assert caught.value.retryable is True
    assert len(rig.api.requests) == CHAT_MAX_PAUSE_CONTINUATIONS + 1


async def test_chat_max_tokens_ends_the_turn_with_its_text(rig: Rig) -> None:
    rig.api.queue(ok("chat_max_tokens"))
    events = await run_chat(rig)
    end = events[-1]
    assert isinstance(end, TurnEnd)
    assert (end.text, end.stop_reason) == ("Partial answ", "max_tokens")
    assert rig.rows[0].stop_reason == "max_tokens"


async def test_chat_refusal_raises_with_its_category(rig: Rig) -> None:
    rig.api.queue(ok("chat_refusal", "req_cr"))
    with pytest.raises(ClaudeRefusal) as caught:
        await run_chat(rig)
    assert caught.value.category == "cyber"
    assert caught.value.request_id == "req_cr"
    (row,) = rig.rows
    assert (row.status, row.error_kind, row.stop_reason) == ("error", "refusal", "refusal")


async def test_chat_without_a_key_raises_when_iterated(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(anthropic_api_key=None)
    with pytest.raises(ClaudeNotConfigured):
        await run_chat(rig)
    assert rig.api.requests == []


async def test_chat_error_event_after_some_text_raises_and_records_partial_usage(rig: Rig) -> None:
    rig.api.queue(ok("midstream_overloaded"))
    seen: list[ChatStreamEvent] = []
    with pytest.raises(ClaudeOverloaded):
        async for event in rig.client.stream_chat(
            model=SONNET,
            system=[TextBlock("rules")],
            messages=HELLO,
            tools="web",
            max_tokens=8000,
            effort=None,
            ctx=CHAT_CTX,
        ):
            seen.append(event)
    assert seen == [TextDelta('{"name": "Ad')]  # what arrived before the failure was delivered
    (row,) = rig.rows
    assert (row.status, row.error_kind, row.input_tokens) == ("error", "overloaded", 900)


async def test_chat_transport_error_is_mapped_and_recorded(rig: Rig) -> None:
    rig.api.queue(error(401, "authentication_error", "invalid x-api-key"))
    with pytest.raises(ClaudeAuthError):
        await run_chat(rig)
    assert [(r.status, r.error_kind) for r in rig.rows] == [("error", "auth")]


async def test_chat_cancellation_records_a_cancelled_row_and_propagates(rig: Rig) -> None:
    reached = asyncio.Event()
    rig.api.queue(streaming("chat_plain", 3, reached=reached))  # message_start, block start, "Hello "
    seen: list[ChatStreamEvent] = []

    async def consume() -> None:
        async for event in rig.client.stream_chat(
            model=SONNET,
            system=[TextBlock("rules", cache=True)],
            messages=HELLO,
            tools="web",
            max_tokens=8000,
            effort=None,
            ctx=CHAT_CTX,
        ):
            seen.append(event)

    task = asyncio.ensure_future(consume())
    await asyncio.wait_for(reached.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert seen == [TextDelta("Hello ")]
    (row,) = rig.rows
    assert row.status == "cancelled"
    assert (row.input_tokens, row.cache_read_tokens) == (500, 12000)  # as of message_start
    assert row.output_tokens == len("Hello ") // 4
    assert row.cost_micro_usd == cost(SONNET, input_tokens=500, output_tokens=1, cache_read_tokens=12000)
    assert row.stop_reason is None


async def test_closing_the_chat_stream_early_records_a_cancelled_row(rig: Rig) -> None:
    rig.api.queue(streaming("chat_plain", 3))
    stream = rig.client.stream_chat(
        model=SONNET,
        system=[TextBlock("rules")],
        messages=HELLO,
        tools="web",
        max_tokens=8000,
        effort=None,
        ctx=CHAT_CTX,
    )
    async for event in stream:
        assert event == TextDelta("Hello ")
        break
    await stream.aclose()  # type: ignore[attr-defined]
    (row,) = rig.rows
    assert row.status == "cancelled"
    assert row.input_tokens == 500


async def test_finishing_the_chat_stream_normally_writes_no_cancelled_row(rig: Rig) -> None:
    rig.api.queue(ok("chat_plain"))
    stream = rig.client.stream_chat(
        model=SONNET,
        system=[TextBlock("rules")],
        messages=HELLO,
        tools="web",
        max_tokens=8000,
        effort=None,
        ctx=CHAT_CTX,
    )
    async for event in stream:
        if isinstance(event, TurnEnd):
            break
    await stream.aclose()  # type: ignore[attr-defined]
    assert [r.status for r in rig.rows] == ["ok"]


# ───────────────────────────── chat: cache breakpoints ─────────────────────────────


async def test_chat_cache_control_rejection_falls_back_to_a_marker_on_the_last_block(rig: Rig) -> None:
    rig.api.queue(
        error(400, "invalid_request_error", "cache_control: Extra inputs are not permitted"),
        ok("chat_plain"),
        ok("chat_plain"),
    )
    events = await run_chat(rig)
    assert isinstance(events[-1], TurnEnd)
    rejected, resent = rig.api.requests
    assert rejected["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in resent
    assert resent["messages"] == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
        }
    ]
    assert [(r.status, r.error_kind) for r in rig.rows] == [("error", "bad_request"), ("ok", None)]

    await run_chat(rig, messages=[{"role": "user", "content": [{"type": "text", "text": "again"}]}])
    third = rig.api.requests[2]
    assert "cache_control" not in third  # remembered: no top-level marker any more
    assert third["messages"][0]["content"][-1]["cache_control"] == {"type": "ephemeral"}


async def test_chat_cache_ttl_setting_applies_to_the_system_marker_and_the_tail(
    make_rig: Callable[..., Rig],
) -> None:
    rig = make_rig(chat_cache_ttl="1h")
    rig.api.queue(ok("chat_plain"))
    await run_chat(rig)
    body = rig.api.requests[0]
    assert body["system"][1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert body["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


async def test_chat_tail_marker_is_skipped_when_four_explicit_breakpoints_are_already_used(rig: Rig) -> None:
    rig.api.queue(ok("chat_plain"))
    system = [TextBlock(f"block {i}", cache=True) for i in range(4)]
    await run_chat(rig, system=system)
    body = rig.api.requests[0]
    assert sum("cache_control" in block for block in body["system"]) == 4
    assert "cache_control" not in body


async def test_chat_cache_miss_is_logged_for_a_big_uncached_profile_block(
    rig: Rig, recorded_log: RecordingLog
) -> None:
    rig.api.queue(ok("structured_uncached"))
    await run_chat(rig, system=[TextBlock("rules"), TextBlock("p" * 8000, cache=True)])
    (miss,) = recorded_log.named("cache_unexpected_miss")
    assert miss["est_prefix_tokens"] == (len("rules") + 8000) // 4


async def test_chat_cache_read_is_not_a_miss(rig: Rig, recorded_log: RecordingLog) -> None:
    rig.api.queue(ok("chat_plain"))
    await run_chat(rig, system=[TextBlock("rules"), TextBlock("p" * 8000, cache=True)])
    assert recorded_log.named("cache_unexpected_miss") == []


# ───────────────────────────── lifecycle and helpers ─────────────────────────────


async def test_the_sdk_client_is_created_lazily_once_and_closed_by_aclose(rig: Rig) -> None:
    assert rig.client._client is None
    rig.api.queue(ok("structured_ok"), ok("structured_ok"))
    await run_structured(rig)
    sdk_client = rig.client._client
    assert sdk_client is not None
    await run_structured(rig)
    assert rig.client._client is sdk_client
    await rig.client.aclose()
    assert rig.client._client is None
    await rig.client.aclose()  # closing twice is fine


def test_the_sdk_client_gets_the_documented_retries_and_timeouts(
    make_settings: Callable[..., Settings],
) -> None:
    client = AnthropicClaudeClient(
        settings=make_settings(anthropic_api_key="sk-ant-test"), usage_sink=MemoryUsageSink()
    )
    sdk = client._get_client()
    assert sdk.max_retries == 2
    assert sdk.timeout == httpx2.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0)
    assert sdk.api_key == "sk-ant-test"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('  {"a": 1}\n', {"a": 1}),
        ('```json\n{"a": {"b": [1, 2]}}\n```', {"a": {"b": [1, 2]}}),
        ('Here you go: {"a": 1} Hope {this} helps.', {"a": 1}),
        ('{not json} then {"a": 1}', {"a": 1}),
        ('[{"a": 1}]', {"a": 1}),
        ("", None),
        ("no braces at all", None),
        ('{"a": 1', None),
        ("[1, 2, 3]", None),
    ],
)
def test_parse_json_object(text: str, expected: dict[str, Any] | None) -> None:
    assert parse_json_object(text) == expected


def test_each_model_gets_the_web_tool_versions_it_accepts() -> None:
    from tayyari.llm.anthropic_client import WEB_TOOLS_BASIC, web_tools_for
    from tayyari.llm.models import spec_for

    assert web_tools_for(spec_for("claude-haiku-5-5")) == WEB_TOOLS
    assert web_tools_for(spec_for("claude-sonnet-5-5")) == WEB_TOOLS
    assert web_tools_for(spec_for("claude-haiku-4-5")) == WEB_TOOLS_BASIC
    assert [t["type"] for t in WEB_TOOLS_BASIC] == ["web_search_20250305", "web_fetch_20250910"]
