"""Price list, capability table and cost math, `usage_from_message`, the usage sinks, and the Claude fake.

The Protocol-conformance lines at the bottom are checked by `uv run mypy tests/unit/llm tests/fakes`; at
runtime they just construct the fakes.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tayyari.contract.records import LlmCallRecord
from tayyari.llm import models
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import ClaudeOverloaded
from tayyari.llm.models import (
    JEV_PRICES,
    MODEL_SPECS,
    WEB_SEARCH_MICRO_USD,
    RawTokens,
    claude_cost_micro,
    is_known_model,
    jev_cost_micro,
    spec_for,
)
from tayyari.llm.types import CallContext, StructuredResult, TextBlock, TextDelta, ToolUse, TurnEnd, Usage
from tayyari.llm.usage import MemoryUsageSink, NullUsageSink, UsageSink, usage_from_message
from tests.fakes.claude import FakeClaudeClient, fake_usage, text_turn
from tests.fakes.clock import FakeClock

# ───────────────────────── the table ─────────────────────────


@pytest.mark.parametrize(
    ("model_id", "price_in", "price_out", "write_5m", "write_1h", "read"),
    [
        ("claude-haiku-4-5", 1.0, 5.0, 1.25, 2.0, 0.10),
        ("claude-sonnet-5-5", 2.0, 10.0, 2.5, 4.0, 0.20),
        ("claude-opus-5-5", 4.0, 20.0, 5.0, 8.0, 0.20),
        ("claude-fable-5-1", 10.0, 50.0, 12.5, 20.0, 0.25),
    ],
)
def test_price_columns(
    model_id: str, price_in: float, price_out: float, write_5m: float, write_1h: float, read: float
) -> None:
    spec = MODEL_SPECS[model_id]
    assert (spec.price_in, spec.price_out) == (price_in, price_out)
    assert (spec.cache_write_5m, spec.cache_write_1h, spec.cache_read) == (write_5m, write_1h, read)
    assert spec.id == model_id


def test_capabilities_follow_the_docs() -> None:
    haiku, sonnet = MODEL_SPECS["claude-haiku-4-5"], MODEL_SPECS["claude-sonnet-5-5"]
    assert haiku.supports_effort is False  # effort is rejected on Haiku 4.5: never send it
    assert haiku.thinking == "budget"
    assert haiku.min_cache_tokens == 4096  # reader prompts (~1k tokens) never cache
    assert sonnet.supports_effort is True
    assert sonnet.thinking == "adaptive"
    assert sonnet.min_cache_tokens == 512
    assert all(spec.structured_output for spec in MODEL_SPECS.values())
    for model_id in ("claude-opus-5-5", "claude-fable-5-1"):
        assert MODEL_SPECS[model_id].supports_effort
        assert MODEL_SPECS[model_id].thinking == "adaptive"
        assert MODEL_SPECS[model_id].min_cache_tokens == 512


def test_cache_prices_are_consistent_multiples_of_input_price() -> None:
    for spec in MODEL_SPECS.values():
        assert spec.cache_write_5m == pytest.approx(1.25 * spec.price_in)
        assert spec.cache_write_1h == pytest.approx(2 * spec.price_in)
        assert spec.cache_read < spec.price_in


def test_the_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        MODEL_SPECS["claude-haiku-4-5"] = MODEL_SPECS["claude-sonnet-5-5"]  # type: ignore[index]
    with pytest.raises(TypeError):
        JEV_PRICES["jev-9"] = 0.0  # type: ignore[index]


# ───────────────────────── cost math ─────────────────────────


@pytest.mark.parametrize(
    ("model_id", "tokens", "expected"),
    [
        # tokens x USD/MTok is already micro-USD
        ("claude-haiku-4-5", RawTokens(input_tokens=1000, output_tokens=500), 1000 * 1 + 500 * 5),
        ("claude-sonnet-5-5", RawTokens(input_tokens=2000, output_tokens=1000), 2000 * 2 + 1000 * 10),
        ("claude-opus-5-5", RawTokens(input_tokens=1000, output_tokens=1000), 1000 * 4 + 1000 * 20),
        ("claude-fable-5-1", RawTokens(input_tokens=1000, output_tokens=1000), 1000 * 10 + 1000 * 50),
    ],
)
def test_plain_input_and_output_cost(model_id: str, tokens: RawTokens, expected: int) -> None:
    assert claude_cost_micro(MODEL_SPECS[model_id], tokens) == expected


def test_cache_write_defaults_to_the_five_minute_rate() -> None:
    sonnet = MODEL_SPECS["claude-sonnet-5-5"]
    writes = RawTokens(cache_write_tokens=20_000)
    assert claude_cost_micro(sonnet, writes) == 50_000  # 20k x $2.50/MTok = $0.05
    assert claude_cost_micro(sonnet, writes, cache_ttl="1h") == 80_000  # 20k x $4/MTok
    haiku = MODEL_SPECS["claude-haiku-4-5"]
    assert claude_cost_micro(haiku, RawTokens(cache_write_tokens=10_000)) == 12_500
    assert claude_cost_micro(haiku, RawTokens(cache_write_tokens=10_000), cache_ttl="1h") == 20_000


def test_a_mixed_cache_write_is_split_between_the_two_rates() -> None:
    sonnet = MODEL_SPECS["claude-sonnet-5-5"]
    writes = RawTokens(cache_write_tokens=10_000)
    assert claude_cost_micro(sonnet, writes, cache_1h_tokens=4_000) == 6_000 * 2.5 + 4_000 * 4
    assert claude_cost_micro(sonnet, writes, cache_1h_tokens=0) == 25_000
    assert claude_cost_micro(sonnet, writes, cache_1h_tokens=99_999) == 40_000  # clamped to the writes


def test_cache_reads_are_a_tenth_of_input() -> None:
    assert claude_cost_micro(MODEL_SPECS["claude-sonnet-5-5"], RawTokens(cache_read_tokens=20_000)) == 4_000
    assert claude_cost_micro(MODEL_SPECS["claude-haiku-4-5"], RawTokens(cache_read_tokens=10_000)) == 1_000
    assert claude_cost_micro(MODEL_SPECS["claude-fable-5-1"], RawTokens(cache_read_tokens=4_000)) == 1_000


def test_web_searches_cost_one_cent_each() -> None:
    assert WEB_SEARCH_MICRO_USD == 10_000
    sonnet = MODEL_SPECS["claude-sonnet-5-5"]
    assert claude_cost_micro(sonnet, RawTokens(web_searches=3)) == 30_000
    assert claude_cost_micro(sonnet, RawTokens(input_tokens=1000, web_searches=1)) == 2_000 + 10_000


def test_a_full_chat_turn_adds_every_component() -> None:
    usage = Usage(
        model="claude-sonnet-5-5",
        input_tokens=500,
        output_tokens=800,
        cache_write_tokens=2_000,
        cache_read_tokens=18_000,
        web_searches=2,
    )
    expected = 500 * 2 + 800 * 10 + 2_000 * 2.5 + 18_000 * 0.20 + 2 * 10_000
    assert claude_cost_micro(spec_for("claude-sonnet-5-5"), usage) == round(expected)


def test_cost_rounds_half_up_once_and_never_drifts() -> None:
    haiku = MODEL_SPECS["claude-haiku-4-5"]
    # 0.1 micro-USD per cached token: 3 tokens = 0.3 -> 0, 5 tokens = 0.5 -> 1, 15 tokens = 1.5 -> 2
    assert claude_cost_micro(haiku, RawTokens(cache_read_tokens=3)) == 0
    assert claude_cost_micro(haiku, RawTokens(cache_read_tokens=5)) == 1
    assert claude_cost_micro(haiku, RawTokens(cache_read_tokens=15)) == 2
    # components are summed exactly and rounded once: 1.25 (one 5m write) + 0.3 (three reads) = 1.55 -> 2,
    # where rounding each part first would give 1 + 0 = 1
    assert claude_cost_micro(haiku, RawTokens(cache_write_tokens=1, cache_read_tokens=3)) == 2
    sonnet = MODEL_SPECS["claude-sonnet-5-5"]
    assert (
        claude_cost_micro(sonnet, RawTokens(cache_read_tokens=2, cache_write_tokens=1)) == 3
    )  # 0.4 + 2.5 = 2.9


def test_negative_counters_count_as_zero() -> None:
    sonnet = MODEL_SPECS["claude-sonnet-5-5"]
    assert claude_cost_micro(sonnet, RawTokens(input_tokens=-5, output_tokens=-5, web_searches=-1)) == 0


def test_no_tokens_cost_nothing() -> None:
    for spec in MODEL_SPECS.values():
        assert claude_cost_micro(spec, RawTokens()) == 0


def test_a_usage_object_can_be_priced_directly() -> None:
    usage = Usage(model="claude-haiku-4-5", input_tokens=1000, output_tokens=1000)
    assert claude_cost_micro(spec_for(usage.model), usage) == 6_000


# ───────────────────────── model id resolution ─────────────────────────


@pytest.mark.parametrize(
    ("model_id", "canonical"),
    [
        ("claude-haiku-4-5", "claude-haiku-4-5"),
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("claude-haiku-4-5@20251001", "claude-haiku-4-5"),
        ("claude-haiku-4-5-latest", "claude-haiku-4-5"),
        ("CLAUDE-HAIKU-4-5-20251001", "claude-haiku-4-5"),
        ("  claude-sonnet-5-5  ", "claude-sonnet-5-5"),
        ("claude-sonnet-5-5-20260301", "claude-sonnet-5-5"),
        ("anthropic.claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5"),
        ("us.anthropic.claude-sonnet-5-5-v1:0", "claude-sonnet-5-5"),
        ("claude-opus-5-5-20260401", "claude-opus-5-5"),
        ("claude-fable-5-1", "claude-fable-5-1"),
    ],
)
def test_dated_ids_and_aliases_resolve_by_prefix(model_id: str, canonical: str) -> None:
    assert spec_for(model_id) is MODEL_SPECS[canonical]
    assert is_known_model(model_id)


@pytest.fixture
def warnings_logged(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture `models.log.warning` calls and reset the warn-once memory."""
    logged: list[dict[str, Any]] = []

    class Recorder:
        def warning(self, event: str, **fields: Any) -> None:
            logged.append({"event": event, **fields})

    monkeypatch.setattr(models, "log", Recorder())
    monkeypatch.setattr(models, "_warned", set())
    return logged


@pytest.mark.parametrize(
    "model_id", ["claude-mystery-9", "gpt-4o", "claude-sonnet-5-50", "claude-opus-5", "claude-haiku-4", ""]
)
def test_unknown_ids_are_priced_at_the_maximum(model_id: str, warnings_logged: list[dict[str, Any]]) -> None:
    spec = spec_for(model_id)
    assert not is_known_model(model_id)
    assert spec.id == model_id  # the spec reports the id that was asked for
    for column in (
        "price_in",
        "price_out",
        "cache_write_5m",
        "cache_write_1h",
        "cache_read",
        "min_cache_tokens",
    ):
        assert getattr(spec, column) == max(getattr(s, column) for s in MODEL_SPECS.values())
    # never undercounts: no known model costs more
    tokens = RawTokens(1000, 1000, 1000, 1000, 1)
    assert claude_cost_micro(spec, tokens) >= max(claude_cost_micro(s, tokens) for s in MODEL_SPECS.values())
    assert len(warnings_logged) == 1
    assert warnings_logged[0]["event"] == "unknown_model_priced_at_maximum"


def test_unknown_ids_get_only_capabilities_that_cannot_fail(warnings_logged: list[dict[str, Any]]) -> None:
    spec = spec_for("claude-mystery-9")
    assert spec.supports_effort is False
    assert spec.thinking == "none"


def test_an_unknown_id_is_warned_about_once(warnings_logged: list[dict[str, Any]]) -> None:
    for _ in range(3):
        spec_for("claude-mystery-9")
    spec_for("claude-other-9")
    assert [w["model"] for w in warnings_logged] == ["claude-mystery-9", "claude-other-9"]


def test_known_ids_never_warn(warnings_logged: list[dict[str, Any]]) -> None:
    spec_for("claude-haiku-4-5-20251001")
    assert warnings_logged == []


# ───────────────────────── Jev prices ─────────────────────────


def test_jev_pricing_is_input_only() -> None:
    assert JEV_PRICES == {"jev-1.13.0": 0.042}
    assert jev_cost_micro("jev-1.13.0", 1_000_000) == 42_000  # $0.042 per million input tokens
    assert jev_cost_micro("jev-1.13.0", 10_000) == 420
    assert jev_cost_micro("jev-1.13.0", 0) == 0


def test_jev_cost_rounds_half_up() -> None:
    assert jev_cost_micro("jev-1.13.0", 11) == 0  # 0.462
    assert jev_cost_micro("jev-1.13.0", 12) == 1  # 0.504
    assert jev_cost_micro("jev-1.13.0", -100) == 0


@pytest.mark.parametrize("model", ["jev-latest", "jev-1.14.0", "JEV-9", ""])
def test_unknown_jev_models_cost_the_maximum_known(model: str) -> None:
    assert jev_cost_micro(model, 1_000_000) == 42_000


# ───────────────────────── usage_from_message ─────────────────────────


def _message(**usage: Any) -> Any:
    return SimpleNamespace(usage=SimpleNamespace(**usage), _request_id="req_abc123")


def test_usage_reads_every_counter_and_prices_it() -> None:
    message = _message(
        input_tokens=500,
        output_tokens=800,
        cache_creation_input_tokens=2_000,
        cache_read_input_tokens=18_000,
        server_tool_use=SimpleNamespace(web_search_requests=2, web_fetch_requests=5),
    )
    usage = usage_from_message(message, "claude-sonnet-5-5")
    assert usage == Usage(
        model="claude-sonnet-5-5",
        input_tokens=500,
        output_tokens=800,
        cache_write_tokens=2_000,
        cache_read_tokens=18_000,
        web_searches=2,  # web fetch is free and not counted
        request_id="req_abc123",
        cost_micro_usd=37_600,  # 1_000 + 8_000 + 5_000 + 3_600 + 2 x 10_000
    )
    assert usage.prompt_tokens == 20_500


def test_usage_tolerates_missing_and_null_fields() -> None:
    usage = usage_from_message(_message(input_tokens=10, output_tokens=20), "claude-haiku-4-5")
    assert (usage.cache_write_tokens, usage.cache_read_tokens, usage.web_searches) == (0, 0, 0)
    assert usage.cost_micro_usd == 10 * 1 + 20 * 5
    nulls = _message(
        input_tokens=10,
        output_tokens=20,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
        server_tool_use=None,
        cache_creation=None,
    )
    assert usage_from_message(nulls, "claude-haiku-4-5") == usage


def test_usage_tolerates_no_usage_and_no_request_id() -> None:
    usage = usage_from_message(SimpleNamespace(), "claude-haiku-4-5")
    assert usage == Usage(model="claude-haiku-4-5")


def test_usage_reads_plain_dicts_from_recorded_responses() -> None:
    payload = {
        "request_id": "req_rec",
        "usage": {"input_tokens": 7, "output_tokens": 3, "server_tool_use": {"web_search_requests": 1}},
    }
    usage = usage_from_message(payload, "claude-sonnet-5-5")
    assert (usage.input_tokens, usage.output_tokens, usage.web_searches, usage.request_id) == (
        7,
        3,
        1,
        "req_rec",
    )


def test_usage_prices_the_one_hour_part_of_a_cache_write_at_the_one_hour_rate() -> None:
    message = _message(
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=10_000,
        cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=4_000, ephemeral_5m_input_tokens=6_000),
    )
    usage = usage_from_message(message, "claude-sonnet-5-5")
    assert usage.cache_write_tokens == 10_000
    assert usage.cost_micro_usd == 6_000 * 2.5 + 4_000 * 4


def test_usage_ignores_garbage_counters() -> None:
    usage = usage_from_message(
        _message(input_tokens="12", output_tokens=True, cache_read_input_tokens=-4), "claude-haiku-4-5"
    )
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens) == (0, 0, 0)


def test_usage_prices_dated_model_ids_and_unknown_ones_at_the_maximum(
    warnings_logged: list[dict[str, Any]],
) -> None:
    message = _message(input_tokens=1000, output_tokens=1000)
    assert usage_from_message(message, "claude-haiku-4-5-20251001").cost_micro_usd == 6_000
    assert usage_from_message(message, "claude-new-1").cost_micro_usd == 1000 * 10 + 1000 * 50


def test_usage_addition_sums_one_models_turns() -> None:
    first = Usage("claude-sonnet-5-5", 10, 20, 30, 40, 1, "req_1", 100)
    second = Usage("claude-sonnet-5-5", 1, 2, 3, 4, 2, "req_2", 50)
    assert first + second == Usage("claude-sonnet-5-5", 11, 22, 33, 44, 3, "req_2", 150)
    with pytest.raises(ValueError):
        first + Usage("claude-haiku-4-5")


# ───────────────────────── usage sinks ─────────────────────────


def _record(purpose: str = "reader", cost: int = 100) -> LlmCallRecord:
    return LlmCallRecord(
        id=uuid4(),
        created_at=datetime(2026, 10, 7, tzinfo=UTC),
        purpose=purpose,
        model="claude-haiku-4-5",
        status="ok",
        cost_micro_usd=cost,
    )


async def test_memory_sink_keeps_records_in_order() -> None:
    sink = MemoryUsageSink()
    first, second = _record("reader", 100), _record("synth", 250)
    await sink.record(first)
    await sink.record(second)
    assert sink.records == [first, second]
    assert sink.total_cost_micro_usd == 350
    assert sink.of_purpose("synth") == [second]


async def test_null_sink_accepts_and_drops() -> None:
    await NullUsageSink().record(_record())  # nothing to observe: it must simply not fail


# ───────────────────────── FakeClaudeClient ─────────────────────────

CTX_READER = CallContext(purpose="reader", request_id="rid")
CTX_SYNTH = CallContext(purpose="synth")
CTX_CHAT = CallContext(purpose="chat", session_id="s1")
SCHEMA: dict[str, Any] = {"type": "object"}


async def _structured(
    client: ClaudeClient, ctx: CallContext, user: str = "hi", **kw: Any
) -> StructuredResult:
    return await client.structured(
        model=kw.get("model", "claude-haiku-4-5"),
        system=[TextBlock("sys", cache=True)],
        user=[TextBlock(user)],
        schema=SCHEMA,
        max_tokens=1000,
        effort=None,
        on_progress=kw.get("on_progress"),
        ctx=ctx,
    )


async def _chat(client: ClaudeClient, tools: str = "web") -> list[Any]:
    stream = client.stream_chat(
        model="claude-sonnet-5-5",
        system=[TextBlock("rules")],
        messages=[{"role": "user", "content": "hello"}],
        tools="web" if tools == "web" else "none",
        max_tokens=2000,
        effort="medium",
        ctx=CTX_CHAT,
    )
    return [event async for event in stream]


def test_the_fakes_satisfy_the_protocols() -> None:
    client: ClaudeClient = FakeClaudeClient()
    sink: UsageSink = MemoryUsageSink()
    assert client is not None and sink is not None


async def test_fake_claude_wraps_dicts_in_a_priced_result_and_reports_progress() -> None:
    claude = FakeClaudeClient().script_structured("reader", {"skills": ["python"]})
    progress: list[int] = []
    result = await _structured(claude, CTX_READER, on_progress=progress.append)
    assert result.data == {"skills": ["python"]}
    assert result.attempts == 1 and result.stop_reason == "end_turn"
    assert result.usage.model == "claude-haiku-4-5"
    assert result.usage.cost_micro_usd == claude_cost_micro(
        spec_for("claude-haiku-4-5"), RawTokens(100, result.usage.output_tokens)
    )
    assert progress and progress[0] > 0


async def test_fake_claude_records_every_call_with_its_arguments() -> None:
    claude = FakeClaudeClient()
    claude.script_structured("reader", {"a": 1}).script_structured("synth", {"b": 2})
    await _structured(claude, CTX_READER, "first")
    await _structured(claude, CTX_SYNTH, "second", model="claude-sonnet-5-5")
    assert [c.ctx.purpose for c in claude.structured_calls] == ["reader", "synth"]
    first, second = claude.structured_calls
    assert first.user_text == "first" and first.system[0].cache is True
    assert (first.model, first.max_tokens, first.effort, first.schema) == (
        "claude-haiku-4-5",
        1000,
        None,
        SCHEMA,
    )
    assert second.model == "claude-sonnet-5-5"
    assert claude.calls == [first, second]
    assert claude.calls_for("synth") == [second]
    assert claude.pending() == 0


async def test_fake_claude_queue_order_when_predicates_and_repeat() -> None:
    claude = FakeClaudeClient()
    claude.script_structured("reader", {"which": "github"}, when=lambda c: "github" in c.user_text)
    claude.script_structured("reader", {"which": "first"}, {"which": "second"})
    claude.script_structured("reader", lambda call: {"which": "fallback", "n": len(call.user)}, repeat=True)
    assert (await _structured(claude, CTX_READER, "plain")).data == {"which": "first"}
    assert (await _structured(claude, CTX_READER, "github repo")).data == {"which": "github"}
    assert (await _structured(claude, CTX_READER, "plain")).data == {"which": "second"}
    for _ in range(3):  # a repeat entry never runs out
        assert (await _structured(claude, CTX_READER)).data == {"which": "fallback", "n": 1}
    assert claude.pending() == 0


async def test_fake_claude_raises_scripted_errors_and_passes_results_through() -> None:
    fixed = StructuredResult({"x": 1}, fake_usage("claude-haiku-4-5", output_tokens=7), "end_turn", 2)
    claude = FakeClaudeClient().script_structured("reader", ClaudeOverloaded(), fixed)
    with pytest.raises(ClaudeOverloaded):
        await _structured(claude, CTX_READER)
    assert await _structured(claude, CTX_READER) is fixed
    assert len(claude.structured_calls) == 2  # failed calls are recorded too


async def test_fake_claude_supports_async_callables() -> None:
    async def slow(call: Any) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"purpose": call.purpose}

    claude = FakeClaudeClient().script_structured("reader", slow)
    assert (await _structured(claude, CTX_READER)).data == {"purpose": "reader"}


async def test_fake_claude_fails_loudly_when_nothing_is_scripted() -> None:
    claude = FakeClaudeClient().script_structured("reader", {"a": 1})
    await _structured(claude, CTX_READER)
    with pytest.raises(AssertionError, match="reader"):
        await _structured(claude, CTX_READER)
    with pytest.raises(AssertionError, match="synth"):
        await _structured(claude, CTX_SYNTH)
    with pytest.raises(AssertionError, match="chat"):
        claude.stream_chat(
            model="m", system=[], messages=[], tools="none", max_tokens=1, effort=None, ctx=CTX_CHAT
        )
    assert len(claude.structured_calls) == 3 and len(claude.chat_calls) == 1


async def test_fake_claude_latency_runs_on_the_injected_clock() -> None:
    clock = FakeClock()
    claude = FakeClaudeClient(clock=clock, latency_s=2.5).script_structured("reader", {"a": 1})
    started = clock.monotonic()
    await _structured(claude, CTX_READER)
    assert clock.monotonic() - started == 2.5


async def test_fake_claude_streams_scripted_chat_turns() -> None:
    claude = FakeClaudeClient().script_chat(
        text_turn("Hello there", chunk_chars=5, tools=[ToolUse("WebSearch", "jobs in Pune")])
    )
    events = await _chat(claude)
    assert events[0] == ToolUse("WebSearch", "jobs in Pune")
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hello", " ther", "e"]
    end = events[-1]
    assert isinstance(end, TurnEnd) and end.text == "Hello there" and end.stop_reason == "end_turn"
    assert end.usage.web_searches == 1
    (call,) = claude.chat_calls
    assert (call.tools, call.effort, call.cache_tail, call.ctx.session_id) == ("web", "medium", True, "s1")
    assert call.messages == ({"role": "user", "content": "hello"},)
    assert call.system_text == "rules"


async def test_fake_claude_chat_can_fail_mid_stream_or_up_front() -> None:
    claude = FakeClaudeClient()
    claude.script_chat([TextDelta("partial"), ClaudeOverloaded()], ClaudeOverloaded())
    seen: list[Any] = []
    with pytest.raises(ClaudeOverloaded):
        async for event in claude.stream_chat(
            model="m", system=[], messages=[], tools="none", max_tokens=1, effort=None, ctx=CTX_CHAT
        ):
            seen.append(event)
    assert seen == [TextDelta("partial")]
    with pytest.raises(ClaudeOverloaded):
        await _chat(claude)


async def test_fake_claude_chat_turns_can_be_callables() -> None:
    claude = FakeClaudeClient().script_chat(lambda call: text_turn(f"tools={call.tools}"), repeat=True)
    assert (await _chat(claude, "none"))[-1].text == "tools=none"
    assert (await _chat(claude, "web"))[-1].text == "tools=web"
