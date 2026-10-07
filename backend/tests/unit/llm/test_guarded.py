"""`GuardedClaudeClient`: the spend gate runs first, a refusal reaches the caller untouched and the inner client
is never called after one."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import pytest

from tayyari.core.errors import ServiceUnavailable
from tayyari.llm.errors import SpendCeilingReached
from tayyari.llm.guarded import GuardedClaudeClient
from tayyari.llm.types import (
    CallContext,
    ChatStreamEvent,
    Effort,
    MessageParam,
    Purpose,
    StructuredResult,
    TextBlock,
    TextDelta,
    ToolMode,
    TurnEnd,
)
from tests.fakes.claude import ChatCall, FakeClaudeClient, StructuredCall, fake_usage, text_turn

MODEL = "claude-sonnet-5-5"
SCHEMA: dict[str, Any] = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


class Gate:
    """A spend gate that records the purposes it saw (and the order relative to the inner client)."""

    def __init__(self, order: list[str], *, trip: Exception | None = None) -> None:
        self.order = order
        self.purposes: list[str] = []
        self.trip = trip

    async def __call__(self, purpose: str) -> None:
        self.purposes.append(purpose)
        self.order.append(f"gate:{purpose}")
        if self.trip is not None:
            raise self.trip


def build(*, trip: Exception | None = None) -> tuple[GuardedClaudeClient, FakeClaudeClient, Gate, list[str]]:
    order: list[str] = []
    inner = FakeClaudeClient()
    gate = Gate(order, trip=trip)
    return GuardedClaudeClient(inner, gate), inner, gate, order


def ctx(purpose: Purpose = "reader") -> CallContext:
    return CallContext(purpose=purpose, profile_id="p1")


async def call_structured(client: GuardedClaudeClient, purpose: Purpose = "reader") -> StructuredResult:
    return await client.structured(
        model=MODEL,
        system=[TextBlock("sys", cache=True)],
        user=[TextBlock("user")],
        schema=SCHEMA,
        max_tokens=100,
        effort="low",
        pdf=b"%PDF",
        on_progress=None,
        ctx=ctx(purpose),
    )


def chat_stream(client: GuardedClaudeClient, purpose: Purpose = "chat") -> AsyncIterator[ChatStreamEvent]:
    return client.stream_chat(
        model=MODEL,
        system=[TextBlock("sys")],
        messages=[{"role": "user", "content": "hi"}],
        tools="none",
        max_tokens=100,
        effort="medium",
        cache_tail=False,
        ctx=ctx(purpose),
    )


async def test_structured_asks_the_gate_first_and_forwards_everything() -> None:
    client, inner, gate, order = build()

    def inner_answer(call: StructuredCall) -> dict[str, Any]:
        order.append("inner")
        return {"ok": True}

    inner.script_structured("reader", inner_answer)
    result = await call_structured(client)

    assert result.data == {"ok": True}
    assert order == ["gate:reader", "inner"]
    assert gate.purposes == ["reader"]
    (call,) = inner.structured_calls
    assert call.model == MODEL
    assert call.system[0] == TextBlock("sys", cache=True)
    assert call.user == (TextBlock("user"),)
    assert call.schema == SCHEMA
    assert (call.max_tokens, call.effort, call.pdf) == (100, "low", b"%PDF")
    assert call.ctx == ctx()


async def test_a_tripped_gate_stops_structured_before_the_inner_client() -> None:
    trip = SpendCeilingReached(retry_after_s=3600)
    client, inner, _gate, _order = build(trip=trip)
    with pytest.raises(SpendCeilingReached) as caught:
        await call_structured(client, "synth")
    assert caught.value is trip
    assert isinstance(caught.value, ServiceUnavailable)
    assert caught.value.status == 503
    assert caught.value.retry_after_header() == "3600"
    assert inner.calls == []


async def test_the_gate_sees_every_purpose_and_runs_on_every_call() -> None:
    client, inner, gate, _order = build()
    for purpose in ("reader", "synth", "decision_fallback"):
        inner.script_structured(purpose, {"ok": True})
    await call_structured(client, "reader")
    await call_structured(client, "synth")
    await call_structured(client, "decision_fallback")
    assert gate.purposes == ["reader", "synth", "decision_fallback"]


async def test_stream_chat_asks_the_gate_before_streaming_and_passes_events_through() -> None:
    client, inner, gate, order = build()

    def turn(call: ChatCall) -> list[Any]:
        order.append("inner")
        return text_turn("Hello there", chunk_chars=6)

    inner.script_chat(turn)
    events = [event async for event in chat_stream(client)]

    assert order == ["gate:chat", "inner"]
    assert gate.purposes == ["chat"]
    assert events[:-1] == [TextDelta("Hello "), TextDelta("there")]
    end = events[-1]
    assert isinstance(end, TurnEnd)
    assert end.text == "Hello there"
    (call,) = inner.chat_calls
    assert (call.tools, call.cache_tail, call.effort, call.max_tokens) == ("none", False, "medium", 100)
    assert call.messages == ({"role": "user", "content": "hi"},)


async def test_a_tripped_gate_stops_stream_chat_on_the_first_iteration() -> None:
    client, inner, _gate, _order = build(trip=SpendCeilingReached())
    stream = chat_stream(client)  # building the stream asks nothing yet
    assert inner.chat_calls == []
    with pytest.raises(SpendCeilingReached):
        await anext(stream)
    assert inner.chat_calls == []


async def test_any_gate_failure_propagates_rather_than_letting_the_call_through() -> None:
    client, inner, _gate, _order = build(trip=RuntimeError("ledger unreachable"))
    with pytest.raises(RuntimeError):
        await call_structured(client)
    with pytest.raises(RuntimeError):
        await anext(chat_stream(client))
    assert inner.calls == []


async def test_inner_errors_pass_through_unchanged() -> None:
    client, inner, _gate, _order = build()
    boom = ValueError("inner failure")
    inner.script_structured("reader", boom)
    with pytest.raises(ValueError) as caught:
        await call_structured(client)
    assert caught.value is boom


class ClosableInner:
    """An inner client whose chat stream records that it was closed (the real one writes its usage row there)."""

    def __init__(self) -> None:
        self.closed_stream = False
        self.closed_client = False

    async def structured(self, **kwargs: Any) -> StructuredResult:
        return StructuredResult(data={}, usage=fake_usage(MODEL))

    def stream_chat(
        self,
        *,
        model: str,
        system: Sequence[TextBlock],
        messages: Sequence[MessageParam],
        tools: ToolMode,
        max_tokens: int,
        effort: Effort | None,
        cache_tail: bool = True,
        ctx: CallContext,
    ) -> AsyncIterator[ChatStreamEvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[ChatStreamEvent]:
        try:
            yield TextDelta("a")
            yield TextDelta("b")
            yield TurnEnd(text="ab", usage=fake_usage(MODEL))
        finally:
            self.closed_stream = True

    async def aclose(self) -> None:
        self.closed_client = True


async def test_closing_the_guarded_stream_closes_the_inner_stream() -> None:
    inner = ClosableInner()
    client = GuardedClaudeClient(inner, Gate([]))
    stream = chat_stream(client)
    assert await anext(stream) == TextDelta("a")
    assert inner.closed_stream is False
    await stream.aclose()  # type: ignore[attr-defined]
    assert inner.closed_stream is True


async def test_exhausting_the_guarded_stream_closes_the_inner_stream_too() -> None:
    inner = ClosableInner()
    client = GuardedClaudeClient(inner, Gate([]))
    assert len([e async for e in chat_stream(client)]) == 3
    assert inner.closed_stream is True


async def test_aclose_closes_an_inner_client_that_can_be_closed() -> None:
    inner = ClosableInner()
    await GuardedClaudeClient(inner, Gate([])).aclose()
    assert inner.closed_client is True


async def test_aclose_is_a_no_op_for_an_inner_client_without_one() -> None:
    client, _inner, _gate, _order = build()
    await client.aclose()


def test_the_gate_type_is_any_async_callable_of_the_purpose() -> None:
    seen: list[str] = []

    async def plain_function(purpose: str) -> None:
        seen.append(purpose)

    gate: Callable[[str], Any] = plain_function
    client = GuardedClaudeClient(FakeClaudeClient(), plain_function)
    assert client._gate is gate
