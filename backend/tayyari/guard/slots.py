"""Concurrency slots for long-lived streams: how many a client and this process may hold open at once.

A chat answer streams for many seconds and may call paid tools, so a rate per minute does not bound the work in
flight: a few clients (or one with many connections) could keep hundreds of turns running at the same time.
`StreamSlots` caps that. It is a pair of in-memory counters, one per client key and one for the process, so it
is per replica by design (the shared database counters in `ratelimit` are the cross-replica layer)::

    slots = StreamSlots(settings.chat_streams_per_ip, settings.chat_streams_per_process, busy_message="...")
    async with slots.acquire(rc.ip_hash):
        ...  # stream the answer

``acquire`` never waits. When the client or the process is at its limit it raises `RateLimited` (HTTP 429,
``Retry-After: 10``) with the ``busy_message`` straight away, so a refused request costs nothing. The slot is
released when the ``async with`` block ends by any route (normal exit, an exception, cancellation). A limit of
0 switches that limit off; a key of ``None`` (an unknown client) shares one anonymous bucket, the strict choice.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from tayyari.core.errors import RateLimited
from tayyari.core.logging import get_logger

log = get_logger(__name__)

BUSY_RETRY_AFTER_S = 10.0
"""``Retry-After`` for a refused stream: long enough for a running answer to make progress, short enough to retry."""


class StreamSlots:
    """See the module docstring. One instance per route, shared by all requests in the process."""

    def __init__(self, per_key: int, per_process: int, *, busy_message: str) -> None:
        if per_key < 0 or per_process < 0:
            raise ValueError("slot limits cannot be negative (0 switches a limit off)")
        self._per_key = per_key
        self._per_process = per_process
        self._busy_message = busy_message
        self._by_key: dict[str | None, int] = {}
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def active(self) -> int:
        """Slots held in this process right now."""
        return self._active

    def active_for(self, key: str | None) -> int:
        """Slots held by one client key (``None`` is the anonymous bucket)."""
        return self._by_key.get(key, 0)

    def acquire(self, key: str | None) -> AbstractAsyncContextManager[None]:
        """Hold one slot for ``key`` until the block exits; raises `RateLimited` at once when none is free."""
        return self._slot(key)

    @asynccontextmanager
    async def _slot(self, key: str | None) -> AsyncIterator[None]:
        async with self._lock:
            self._take(key)
        try:
            yield
        finally:
            # Synchronous on purpose: it cannot be interrupted half way (a second cancel would leak the slot),
            # and with no await in it, it is atomic with respect to the locked section above.
            self._release(key)

    def _take(self, key: str | None) -> None:
        """Count one more stream, or raise `RateLimited`. Runs without awaiting, so it is atomic."""
        held = self._by_key.get(key, 0)
        if self._per_key > 0 and held >= self._per_key:
            self._refuse("client", held, self._per_key)
        if self._per_process > 0 and self._active >= self._per_process:
            self._refuse("process", self._active, self._per_process)
        self._by_key[key] = held + 1
        self._active += 1

    def _refuse(self, scope: str, held: int, limit: int) -> None:
        log.info("stream_slots_full", scope=scope, held=held, limit=limit)
        raise RateLimited(
            self._busy_message,
            retry_after_s=BUSY_RETRY_AFTER_S,
            detail=f"stream slots full scope={scope} held={held} limit={limit}",
        )

    def _release(self, key: str | None) -> None:
        held = self._by_key.get(key, 0)
        if held <= 1:
            self._by_key.pop(key, None)  # no entry left behind for a finished client
        else:
            self._by_key[key] = held - 1
        self._active = max(0, self._active - 1)


__all__ = ["BUSY_RETRY_AFTER_S", "StreamSlots"]
