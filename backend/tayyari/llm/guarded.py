"""`GuardedClaudeClient`: a `ClaudeClient` that asks the spend gate before every call.

The gate is any ``async (purpose) -> None`` that raises `SpendCeilingReached` (a 503 `AppError`) once the
day's ceiling is hit; the guard unit passes ``SpendGuard.admit_call``. The wrapper is the only place that
knows about the guard, so the real client stays unaware of it and the CLI (``smoke``, ``make-sample``) simply
builds the unguarded one.

Every Claude call goes through it, decision fallbacks included. For `stream_chat` the gate runs when the
stream is first iterated (before any request is made), so a tripped ceiling surfaces as the first
``__anext__`` raising `SpendCeilingReached`, never as a half-started stream.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any

from .claude import ClaudeClient
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

SpendGate = Callable[[str], Awaitable[None]]
"""Called with the call's purpose before it is made; raises `SpendCeilingReached` to stop it."""


class GuardedClaudeClient(ClaudeClient):
    """Wraps ``inner`` so that ``spend_gate(purpose)`` runs, and may refuse, before each Claude call."""

    def __init__(self, inner: ClaudeClient, spend_gate: SpendGate) -> None:
        self._inner = inner
        self._gate = spend_gate

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
        await self._gate(ctx.purpose)
        return await self._inner.structured(
            model=model,
            system=system,
            user=user,
            schema=schema,
            max_tokens=max_tokens,
            effort=effort,
            pdf=pdf,
            on_progress=on_progress,
            ctx=ctx,
        )

    async def stream_chat(
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
        await self._gate(ctx.purpose)
        events = self._inner.stream_chat(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            effort=effort,
            cache_tail=cache_tail,
            ctx=ctx,
        )
        try:
            async for event in events:
                yield event
        finally:
            # Closing the outer generator (client gone, early break) must close the inner stream too, so
            # it can write its "cancelled" usage row.
            aclose = getattr(events, "aclose", None)
            if aclose is not None:
                await aclose()

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
        await self._gate(ctx.purpose)
        return await self._inner.search_web(
            model=model,
            system=system,
            user=user,
            max_searches=max_searches,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
            user_location=user_location,
            max_tokens=max_tokens,
            effort=effort,
            ctx=ctx,
        )

    async def aclose(self) -> None:
        """Close the wrapped client when it has something to close."""
        close = getattr(self._inner, "aclose", None)
        if close is not None:
            await close()


__all__ = ["GuardedClaudeClient", "SpendGate"]
