from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from tayyari.core.clock import Clock, SystemClock, paced


class RecordingClock:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def test_system_clock_satisfies_the_protocol() -> None:
    assert isinstance(SystemClock(), Clock)
    assert isinstance(RecordingClock(), Clock)


def test_now_is_aware_utc_and_current() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert abs((datetime.now(UTC) - now).total_seconds()) < 5


def test_monotonic_never_goes_backwards() -> None:
    clock = SystemClock()
    first = clock.monotonic()
    time.sleep(0.01)
    assert clock.monotonic() >= first


async def test_system_clock_sleeps_for_real() -> None:
    clock = SystemClock()
    started = clock.monotonic()
    await clock.sleep(0.05)
    assert clock.monotonic() - started >= 0.04


async def test_paced_multiplies_by_the_scale() -> None:
    clock = RecordingClock()
    await paced(clock, 0.5, 2.0)
    await paced(clock, 0.5, 0.5)
    await paced(clock, 0.25, 1.0)
    assert clock.sleeps == pytest.approx([1.0, 0.25, 0.25])


async def test_paced_defaults_to_scale_one() -> None:
    clock = RecordingClock()
    await paced(clock, 0.3)
    assert clock.sleeps == [0.3]


@pytest.mark.parametrize(
    ("seconds", "scale"), [(0.5, 0), (0, 1.0), (0, 0), (-1, 1.0), (1.0, -2.0), (0.5, 0.0)]
)
async def test_a_zero_delay_never_touches_the_clock(seconds: float, scale: float) -> None:
    clock = RecordingClock()
    await paced(clock, seconds, scale)
    assert clock.sleeps == []


async def test_paced_with_the_system_clock_and_zero_scale_returns_immediately() -> None:
    started = time.monotonic()
    await paced(SystemClock(), 5.0, 0)
    assert time.monotonic() - started < 0.5
