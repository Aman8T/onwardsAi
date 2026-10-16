"""A scripted `ClaudeClient` for tests: no network, every call recorded.

Script answers per purpose (``reader``, ``synth``, ``chat`` ...) before the code under test runs::

    claude = FakeClaudeClient()
    claude.script_structured("reader", {"skills": []})                      # one answer, consumed once
    claude.script_structured("reader", ClaudeOverloaded(), when=lambda c: "github" in c.user_text)
    claude.script_structured("synth", lambda call: {"nodes": call.max_tokens}, repeat=True)
    claude.script_chat(text_turn("Hello", tools=[ToolUse("WebSearch", "jobs")]))
    ...
    assert claude.structured_calls[0].ctx.purpose == "reader"

An entry is picked by order, or by its ``when`` predicate. ``repeat=True`` keeps an entry for every later
call (readers run many times). A call with nothing scripted raises `AssertionError`, so a test never
passes by accident on an unexpected request.

Structured entries: a `StructuredResult`, a dict (wrapped in one with a made-up `Usage` priced from the
model table), an exception (raised), or a callable taking the `StructuredCall` and returning one of those.
Chat entries: a list of events and exceptions (an exception is raised at that point of the stream, after
the events before it), or a callable returning such a list. `text_turn` builds a complete list.
Search entries (`script_search`): a `WebSearchRun`, an exception, or a callable taking the `SearchWebCall`; `search_run`
builds a run from ``{query: [(url, title), ...]}``.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tayyari.core.clock import Clock
from tayyari.llm.models import RawTokens, claude_cost_micro, spec_for
from tayyari.llm.types import (
    CallContext,
    ChatStreamEvent,
    Effort,
    MessageParam,
    Purpose,
    SearchCall,
    SearchHit,
    StructuredResult,
    TextBlock,
    TextDelta,
    ToolMode,
    ToolUse,
    TurnEnd,
    Usage,
    WebSearchRun,
)


def fake_usage(
    model: str,
    *,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    web_searches: int = 0,
    request_id: str | None = "req_fake",
) -> Usage:
    """A `Usage` whose cost is computed from the real price table, so cost assertions stay honest."""
    tokens = RawTokens(input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, web_searches)
    return Usage(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_read_tokens=cache_read_tokens,
        web_searches=web_searches,
        request_id=request_id,
        cost_micro_usd=claude_cost_micro(spec_for(model), tokens),
    )


def text_turn(
    text: str,
    *,
    model: str = "claude-sonnet-5-5",
    tools: Sequence[ToolUse] = (),
    chunk_chars: int | None = None,
    usage: Usage | None = None,
    stop_reason: str = "end_turn",
) -> list[ChatStreamEvent]:
    """A complete chat stream: the tool events, the text in chunks, then one `TurnEnd` with the whole text."""
    events: list[ChatStreamEvent] = list(tools)
    if text:
        step = chunk_chars if chunk_chars and chunk_chars > 0 else len(text)
        events.extend(TextDelta(text[i : i + step]) for i in range(0, len(text), step))
    events.append(
        TurnEnd(
            text=text,
            usage=usage if usage is not None else fake_usage(model, web_searches=len(tools)),
            stop_reason=stop_reason,
        )
    )
    return events


def search_run(
    results: Mapping[str, Sequence[tuple[str, str]]],
    *,
    model: str = "claude-haiku-5-5",
    errors: Mapping[str, str] | None = None,
    stop_reason: str = "end_turn",
    text: str = "DONE",
) -> WebSearchRun:
    """A finished search turn: one search per key of ``results`` (``[(url, title), ...]``), then one per key of
    ``errors`` (``{query: error_code}``), with a usage that pays for every search."""
    searches = [
        SearchCall(query=query, hits=tuple(SearchHit(url=url, title=title) for url, title in hits))
        for query, hits in results.items()
    ]
    searches.extend(SearchCall(query=query, error=code) for query, code in (errors or {}).items())
    return WebSearchRun(
        searches=tuple(searches),
        usage=fake_usage(model, input_tokens=8_000, output_tokens=60, web_searches=len(searches)),
        stop_reason=stop_reason,
        text=text,
    )


@dataclass(frozen=True)
class StructuredCall:
    """The arguments of one `structured()` call."""

    model: str
    system: tuple[TextBlock, ...]
    user: tuple[TextBlock, ...]
    schema: dict[str, Any]
    max_tokens: int
    effort: Effort | None
    pdf: bytes | None
    on_progress: Callable[[int], None] | None
    ctx: CallContext

    @property
    def purpose(self) -> Purpose:
        return self.ctx.purpose

    @property
    def system_text(self) -> str:
        return "\n\n".join(b.text for b in self.system)

    @property
    def user_text(self) -> str:
        return "\n\n".join(b.text for b in self.user)


@dataclass(frozen=True)
class ChatCall:
    """The arguments of one `stream_chat()` call."""

    model: str
    system: tuple[TextBlock, ...]
    messages: tuple[MessageParam, ...]
    tools: ToolMode
    max_tokens: int
    effort: Effort | None
    cache_tail: bool
    ctx: CallContext

    @property
    def system_text(self) -> str:
        return "\n\n".join(b.text for b in self.system)


@dataclass(frozen=True)
class SearchWebCall:
    """The arguments of one `search_web()` call."""

    model: str
    system: tuple[TextBlock, ...]
    user: tuple[TextBlock, ...]
    max_searches: int
    allowed_domains: tuple[str, ...]
    blocked_domains: tuple[str, ...]
    user_location: Mapping[str, str] | None
    max_tokens: int
    effort: Effort | None
    ctx: CallContext

    @property
    def system_text(self) -> str:
        return "\n\n".join(b.text for b in self.system)

    @property
    def user_text(self) -> str:
        return "\n\n".join(b.text for b in self.user)


StructuredItem = StructuredResult | Mapping[str, Any] | BaseException
StructuredScript = StructuredItem | Callable[[StructuredCall], Any]
ChatItem = ChatStreamEvent | BaseException
ChatScript = Sequence[ChatItem] | BaseException | Callable[[ChatCall], Any]


@dataclass
class _Entry[CallT]:
    item: Any
    when: Callable[[CallT], bool] | None
    repeat: bool


@dataclass
class _Queues:
    structured: dict[str, list[_Entry[StructuredCall]]] = field(default_factory=dict)
    chat: list[_Entry[ChatCall]] = field(default_factory=list)
    search: list[_Entry[SearchWebCall]] = field(default_factory=list)


def _take[CallT](entries: list[_Entry[CallT]], call: CallT) -> _Entry[CallT] | None:
    for entry in entries:
        if entry.when is None or entry.when(call):
            if not entry.repeat:
                entries.remove(entry)
            return entry
    return None


class FakeClaudeClient:
    """Scripted stand-in for `tayyari.llm.claude.ClaudeClient`."""

    def __init__(self, *, clock: Clock | None = None, latency_s: float = 0.0) -> None:
        self._clock = clock
        self._latency_s = latency_s
        self._queues = _Queues()
        self.structured_calls: list[StructuredCall] = []
        self.chat_calls: list[ChatCall] = []
        self.search_calls: list[SearchWebCall] = []
        self.calls: list[StructuredCall | ChatCall | SearchWebCall] = []
        """Both kinds, in the order they arrived."""

    # ── scripting ──

    def script_structured(
        self,
        purpose: Purpose,
        *items: StructuredScript,
        when: Callable[[StructuredCall], bool] | None = None,
        repeat: bool = False,
    ) -> FakeClaudeClient:
        """Queue answers for ``purpose``. With ``repeat=True`` the (last) item answers every later call too."""
        queue = self._queues.structured.setdefault(purpose, [])
        for index, item in enumerate(items):
            queue.append(_Entry(item, when, repeat and index == len(items) - 1))
        return self

    def script_chat(
        self,
        *turns: ChatScript,
        when: Callable[[ChatCall], bool] | None = None,
        repeat: bool = False,
    ) -> FakeClaudeClient:
        """Queue chat turns (event lists, an exception, or a callable)."""
        for index, turn in enumerate(turns):
            self._queues.chat.append(_Entry(turn, when, repeat and index == len(turns) - 1))
        return self

    def script_search(
        self,
        *runs: WebSearchRun | BaseException | Callable[[SearchWebCall], Any],
        when: Callable[[SearchWebCall], bool] | None = None,
        repeat: bool = False,
    ) -> FakeClaudeClient:
        """Queue search turns (a `WebSearchRun`, an exception, or a callable taking the call)."""
        for index, run in enumerate(runs):
            self._queues.search.append(_Entry(run, when, repeat and index == len(runs) - 1))
        return self

    def pending(self) -> int:
        """Scripted entries that have not been used and are not ``repeat`` entries."""
        structured = sum(1 for q in self._queues.structured.values() for e in q if not e.repeat)
        searches = sum(1 for e in self._queues.search if not e.repeat)
        return structured + searches + sum(1 for e in self._queues.chat if not e.repeat)

    def calls_for(self, purpose: Purpose) -> list[StructuredCall]:
        return [c for c in self.structured_calls if c.ctx.purpose == purpose]

    # ── ClaudeClient ──

    async def structured(
        self,
        *,
        model: str,
        system: Sequence[TextBlock],
        user: Sequence[TextBlock],
        schema: dict[str, Any],
        max_tokens: int,
        effort: Effort | None,
        pdf: bytes | None = None,
        on_progress: Callable[[int], None] | None = None,
        ctx: CallContext,
    ) -> StructuredResult:
        call = StructuredCall(
            model, tuple(system), tuple(user), schema, max_tokens, effort, pdf, on_progress, ctx
        )
        self.structured_calls.append(call)
        self.calls.append(call)
        entry = _take(self._queues.structured.get(ctx.purpose, []), call)
        if entry is None:
            raise AssertionError(
                f"FakeClaudeClient: no structured answer scripted for purpose {ctx.purpose!r}"
            )
        if self._latency_s and self._clock is not None:
            await self._clock.sleep(self._latency_s)
        item: Any = entry.item
        if callable(item):
            item = item(call)
            if inspect.isawaitable(item):
                item = await item
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, StructuredResult):
            return item
        data = dict(item)
        if on_progress is not None:
            on_progress(len(json.dumps(data)))
        return StructuredResult(
            data=data,
            usage=fake_usage(model, output_tokens=max(1, len(json.dumps(data)) // 4)),
            stop_reason="end_turn",
            attempts=1,
        )

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
        call = ChatCall(model, tuple(system), tuple(messages), tools, max_tokens, effort, cache_tail, ctx)
        self.chat_calls.append(call)
        self.calls.append(call)
        entry = _take(self._queues.chat, call)
        if entry is None:
            raise AssertionError("FakeClaudeClient: no chat turn scripted")
        return self._play(entry.item, call)

    async def search_web(
        self,
        *,
        model: str,
        system: Sequence[TextBlock],
        user: Sequence[TextBlock],
        max_searches: int,
        allowed_domains: Sequence[str] = (),
        blocked_domains: Sequence[str] = (),
        user_location: Mapping[str, str] | None = None,
        max_tokens: int,
        effort: Effort | None,
        ctx: CallContext,
    ) -> WebSearchRun:
        call = SearchWebCall(
            model,
            tuple(system),
            tuple(user),
            max_searches,
            tuple(allowed_domains),
            tuple(blocked_domains),
            user_location,
            max_tokens,
            effort,
            ctx,
        )
        self.search_calls.append(call)
        self.calls.append(call)
        entry = _take(self._queues.search, call)
        if entry is None:
            raise AssertionError("FakeClaudeClient: no search turn scripted")
        if self._latency_s and self._clock is not None:
            await self._clock.sleep(self._latency_s)
        item: Any = entry.item
        if callable(item):
            item = item(call)
            if inspect.isawaitable(item):
                item = await item
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, WebSearchRun), item
        return item

    async def _play(self, script: Any, call: ChatCall) -> AsyncIterator[ChatStreamEvent]:
        if self._latency_s and self._clock is not None:
            await self._clock.sleep(self._latency_s)
        item = script(call) if callable(script) else script
        if inspect.isawaitable(item):
            item = await item
        if isinstance(item, BaseException):
            raise item
        for event in item:
            if isinstance(event, BaseException):
                raise event
            yield event


__all__ = [
    "ChatCall",
    "FakeClaudeClient",
    "SearchWebCall",
    "StructuredCall",
    "fake_usage",
    "search_run",
    "text_turn",
]
