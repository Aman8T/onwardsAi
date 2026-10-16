"""The Claude client Protocol every service depends on.

Services (analysis, chat, decision fallbacks) take a `ClaudeClient`, never the Anthropic SDK, so each is
tested with `tests.fakes.claude.FakeClaudeClient`. The real implementation (`llm/anthropic_client.py`) and
the spend-checking wrapper (`llm/guarded.py`) live in other modules.

Contract:
  - Both calls raise `ClaudeError` subclasses (see `llm.errors`), and `SpendCeilingReached` when wrapped
    by the guard. `asyncio.CancelledError` propagates untouched.
  - Every request, including each retry and each `pause_turn` continuation, is reported to the usage sink.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any, Protocol

from .types import (
    CallContext,
    ChatStreamEvent,
    Effort,
    MessageParam,
    StructuredResult,
    TextBlock,
    ToolMode,
    WebSearchRun,
)


class ClaudeClient(Protocol):
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
        """One structured call whose answer matches ``schema`` (JSON Schema).

        ``effort`` is sent only to models that support it (`llm.models.ModelSpec.supports_effort`).
        ``pdf`` attaches a document block ahead of the user text (scanned resumes). ``on_progress`` gets
        the running count of generated characters. Content failures are retried by the client (a second
        attempt with a repair note, lower effort and more tokens); `StructuredResult.attempts` says how
        many it took. Raises `ClaudeInvalidOutput`, `ClaudeTruncated`, `ClaudeRefusal` or a transport error.
        """
        ...

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
        """Stream one assistant turn as `TextDelta` and `ToolUse` events, ending with exactly one `TurnEnd`.

        ``tools="web"`` declares the web search and fetch tools; ``"none"`` keeps them declared but sets
        ``tool_choice={"type": "none"}`` so the cached prefix is unchanged. ``cache_tail`` puts an automatic
        cache breakpoint on the last message block. ``TurnEnd.stop_reason`` can be ``max_tokens`` (the
        caller adds a "cut off" note); a refusal raises `ClaudeRefusal`.
        """
        ...

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
        """One turn in which the model may run up to ``max_searches`` web searches and nothing else.

        Only the web search tool is declared (no fetch), restricted to ``allowed_domains`` or away from
        ``blocked_domains`` (the API takes one of the two, never both). The result is what the tool returned, query by
        query, read from the response blocks, so a caller can use the links without trusting the model's words. A
        ``pause_turn`` is continued a bounded number of times; a ``max_tokens`` stop keeps the searches already run.
        A refusal raises `ClaudeRefusal`.
        """
        ...


__all__ = ["ClaudeClient"]
