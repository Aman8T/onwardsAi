"""The rate-limit rule of The Leafout Weekly: ``weekly`` (an edition built on demand), per address and per account in an hour
and a day window, across the service per hour, its defaults and its sentences."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

import pytest

from tayyari.core.errors import RateLimited
from tayyari.core.settings import GuardSettings
from tayyari.guard.ratelimit import GLOBAL_WEEKLY_BUCKET, RateLimiter, Route, limit_message
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

H = "aaaa111122223333"
NOON = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)


def make(**guard: object) -> tuple[RateLimiter, FakeRepositories, FakeClock]:
    clock = FakeClock(NOON)
    fakes = FakeRepositories(clock)
    return RateLimiter(fakes.limits, GuardSettings(**guard), clock), fakes, clock


def test_the_defaults_and_the_route() -> None:
    guard = GuardSettings()
    assert (guard.weekly_preview_per_hour, guard.weekly_preview_per_day) == (3, 6)
    assert guard.weekly_global_per_hour == 120 and guard.est_weekly_usd == 0.06
    assert "weekly" in get_args(Route) and GLOBAL_WEEKLY_BUCKET == "weekly:global:hour"


async def test_the_hour_window_then_the_day_window() -> None:
    limiter, _, clock = make(weekly_preview_per_hour=1, weekly_preview_per_day=2)
    await limiter.check("weekly", H, memory=False)
    with pytest.raises(RateLimited) as hour:
        await limiter.check("weekly", H, memory=False)
    assert hour.value.user_message == (
        "You've asked for a lot of editions this hour. Please try again in about an hour."
    )
    clock.advance(3600)
    await limiter.check("weekly", H, memory=False)
    clock.advance(3600)
    with pytest.raises(RateLimited) as day:
        await limiter.check("weekly", H, memory=False)
    assert day.value.user_message.startswith("You've reached today's limit for editions. It resets in ")


async def test_an_account_counts_beside_the_address_and_the_service_once() -> None:
    limiter, fakes, _ = make(weekly_preview_per_hour=0, weekly_preview_per_day=2)
    for _ in range(2):
        await limiter.check("weekly", H, memory=False)
        await limiter.check("weekly", "u:abc", memory=False, globally=False)
    assert fakes.limits.hits[("weekly:day:u:abc", NOON.replace(hour=0))] == 2
    assert fakes.limits.hits[(GLOBAL_WEEKLY_BUCKET, NOON.replace(minute=0))] == 2
    with pytest.raises(RateLimited):
        await limiter.check("weekly", "u:abc", memory=False, globally=False)


async def test_the_whole_service_backstop_and_an_exempt_account() -> None:
    limiter, fakes, _ = make(weekly_global_per_hour=1)
    await limiter.check("weekly", "client000000001", memory=False)
    with pytest.raises(RateLimited) as info:
        await limiter.check("weekly", "client000000002", memory=False)
    assert info.value.user_message == "Tayyari is very busy right now. Please try again in about an hour."
    limiter, fakes, _ = make(weekly_preview_per_hour=1, weekly_preview_per_day=1)
    for _ in range(4):
        limiter.shed("weekly", H, exempt=True)
        await limiter.check("weekly", H, memory=False, exempt=True)
    assert fakes.limits.hits == {}


def test_the_sentences() -> None:
    assert (
        limit_message("weekly", "min", 60)
        == "An edition is already being set. Please wait about a minute and try again."
    )
