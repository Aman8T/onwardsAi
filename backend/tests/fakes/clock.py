"""A manual clock for tests: time only moves when the test (or a `sleep`) moves it.

`FakeClock` implements `tayyari.core.clock.Clock`. `advance()` moves both the wall clock and the monotonic
clock. `sleep()` does not wait: it records the request, advances time by that amount at once and yields to
the event loop once, so code that paces itself with `await clock.sleep(...)` finishes instantly and a test can
still read how long it "slept". With several concurrent sleepers time simply keeps adding up; it is not a
timer wheel, so do not rely on the wake-up order of tasks that sleep different amounts.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

_DEFAULT_START = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, start: datetime | None = None, *, monotonic_start: float = 1_000.0) -> None:
        begin = start if start is not None else _DEFAULT_START
        if begin.tzinfo is None:
            raise ValueError("FakeClock needs a timezone-aware start")
        self._now = begin.astimezone(UTC)
        self._mono = monotonic_start
        self.sleeps: list[float] = []
        """Every positive duration passed to `sleep`, in call order."""

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        """Move both clocks forward."""
        if seconds < 0:
            raise ValueError("time cannot go backwards")
        self._now += timedelta(seconds=seconds)
        self._mono += seconds

    async def sleep(self, seconds: float) -> None:
        """Advance time by ``seconds`` without waiting (a zero or negative request only yields)."""
        if seconds > 0:
            self.sleeps.append(seconds)
            self.advance(seconds)
        await asyncio.sleep(0)

    @property
    def total_slept(self) -> float:
        return sum(self.sleeps)


__all__ = ["FakeClock"]
