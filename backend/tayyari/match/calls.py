"""How the match makes a model call: one place for the purpose, the empty thinking and the failure policy.

Every call goes through the `ClaudeClient` Protocol with a ``match.*`` purpose (the client records it with its
cost). The purposes are not in the client's "wants thinking" table, so Haiku 5.5 is sent ``thinking: disabled``
and Haiku 4.5 sends no thinking parameter: these are extraction-type calls.

A call that FAILS (an overloaded API, a refusal, a timeout) returns None from `ask_or_none` and the stage degrades
to the honest default (unknown, uncertain, the fact's own wording); only the daily spend ceiling always propagates.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, Final

from tayyari.core.logging import get_logger
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import ClaudeError, SpendCeilingReached
from tayyari.llm.types import CallContext, Purpose, TextBlock

log = get_logger(__name__)

CALL_TIMEOUT_S: Final = 90.0

_TIMEOUT_S: ContextVar[float] = ContextVar("match_call_timeout_s", default=CALL_TIMEOUT_S)


@contextmanager
def call_timeout(seconds: float | None) -> Iterator[None]:
    """Inside the block, a degrading call (`ask_or_none`) waits at most ``seconds`` (None: `CALL_TIMEOUT_S`).

    The service sets it from ``MatchSettings.call_timeout_s`` around each of its pipeline methods; the tasks those
    methods start inherit it, so the stages need not pass it down.
    """
    token = _TIMEOUT_S.set(seconds if seconds is not None and seconds > 0 else CALL_TIMEOUT_S)
    try:
        yield
    finally:
        _TIMEOUT_S.reset(token)


async def ask(
    claude: ClaudeClient,
    *,
    model: str,
    purpose: Purpose,
    system: str,
    user: str,
    schema: dict[str, Any],
    max_tokens: int,
    call_ctx: CallContext,
) -> dict[str, Any]:
    """One structured call with ``purpose``; errors propagate."""
    result = await claude.structured(
        model=model,
        system=[TextBlock(system)],
        user=[TextBlock(user)],
        schema=schema,
        max_tokens=max_tokens,
        effort=None,
        ctx=replace(call_ctx, purpose=purpose),
    )
    return result.data


async def ask_or_none(
    claude: ClaudeClient,
    *,
    model: str,
    purpose: Purpose,
    system: str,
    user: str,
    schema: dict[str, Any],
    max_tokens: int,
    call_ctx: CallContext,
    timeout_s: float | None = None,
) -> dict[str, Any] | None:
    """`ask`, but a failed or late call gives None (logged). The spend ceiling still propagates.

    ``timeout_s`` defaults to the one `call_timeout` set around the pipeline stage (else `CALL_TIMEOUT_S`).
    """
    try:
        async with asyncio.timeout(timeout_s if timeout_s is not None else _TIMEOUT_S.get()):
            return await ask(
                claude,
                model=model,
                purpose=purpose,
                system=system,
                user=user,
                schema=schema,
                max_tokens=max_tokens,
                call_ctx=call_ctx,
            )
    except SpendCeilingReached:
        raise
    except (ClaudeError, TimeoutError) as exc:
        log.warning("match_call_failed", purpose=purpose, error=type(exc).__name__)
        return None


async def gather_all[T](*calls: Awaitable[T]) -> list[T]:
    """The results of ``calls`` run together, in order; the first error cancels the rest and is raised."""
    tasks = [asyncio.ensure_future(c) for c in calls]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def gather2[A, B](first: Awaitable[A], second: Awaitable[B]) -> tuple[A, B]:
    """Two awaitables of different types run together; the first error cancels the other and is raised."""
    one = asyncio.ensure_future(first)
    two = asyncio.ensure_future(second)
    try:
        return await asyncio.gather(one, two)
    except BaseException:
        one.cancel()
        two.cancel()
        await asyncio.gather(one, two, return_exceptions=True)
        raise


__all__ = ["CALL_TIMEOUT_S", "ask", "ask_or_none", "call_timeout", "gather2", "gather_all"]
