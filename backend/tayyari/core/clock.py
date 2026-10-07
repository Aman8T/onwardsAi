"""Time as a dependency: services take a `Clock` so tests control sleeping and "now".

`paced` is how the pipeline spaces out UI events (signals arriving ~150 ms apart, reader ticks, the
animated first-read lines). It multiplies the delay by `Settings.pacing_scale`, which is 0 in tests and
for fast demos.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Wall clock, monotonic clock and an async sleep, behind one injectable seam."""

    def now(self) -> datetime:
        """The current time as a timezone-aware UTC datetime."""
        ...

    def monotonic(self) -> float:
        """Seconds from an arbitrary origin; only differences are meaningful."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Suspend the calling task for ``seconds``."""
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


async def paced(clock: Clock, seconds: float, scale: float = 1.0) -> None:
    """Sleep ``seconds * scale``; a zero (or negative) result does not touch the clock at all."""
    delay = seconds * scale
    if delay > 0:
        await clock.sleep(delay)


__all__ = ["Clock", "SystemClock", "paced"]
