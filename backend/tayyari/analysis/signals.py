"""Signal chips: streamed one at a time (about 150 ms apart) and deduped across every source.

A reader's quick (deterministic) signals and its model-found signals both end up here. `loose_key` makes
``"REST & GraphQL"`` and ``"rest-graphql"`` the same chip, so the cloud on the analysis screen stays clean
however many sources mention the same thing. The first-read lines from Jev go through the same dedupe set.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Sequence

from tayyari.contract import SourceKind, ev_signal
from tayyari.core.text import loose_key

from .normalise import SignalItem
from .runctx import RunContext

SIGNAL_PACE_S = 0.15


class SignalStream:
    """Paced, deduped `signal` events for one analysis."""

    def __init__(self, run: RunContext) -> None:
        self._run = run
        self._seen: set[str] = set()
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        """Every batch spawned so far (finished ones included)."""
        return tuple(self._tasks)

    async def emit(self, items: Sequence[SignalItem], kind: SourceKind) -> None:
        """Send each new chip, pausing `SIGNAL_PACE_S` (scaled) after every one that is sent."""
        for it in items:
            key = loose_key(it["label"])
            if not key or key in self._seen:
                continue
            self._seen.add(key)
            self._run.send(ev_signal(it["label"], it["detail"], kind))
            await self._run.pace(SIGNAL_PACE_S)

    def spawn(
        self,
        items: Sequence[SignalItem],
        kind: SourceKind,
        after: Awaitable[None] | None = None,
    ) -> asyncio.Task[None]:
        """Stream ``items`` in the background, once ``after`` (an earlier batch of the same source) has finished."""

        async def go() -> None:
            if after is not None:
                with contextlib.suppress(Exception):
                    await after
            await self.emit(items, kind)

        task = asyncio.create_task(go())
        self._tasks.append(task)
        return task

    async def settle(self) -> None:
        """Wait until every spawned batch has been sent."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def cancel(self) -> None:
        """Stop every spawned batch and wait until they are gone."""
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


__all__ = ["SIGNAL_PACE_S", "SignalStream"]
