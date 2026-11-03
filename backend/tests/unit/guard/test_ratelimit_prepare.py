"""The rate-limit rule of Prepare: ``prepare`` (a plan written, or a headline rewritten: per address, per account, and across
the service per hour), its defaults, its sentences, and the two spend estimates."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

import pytest

from tayyari.core.errors import RateLimited
from tayyari.core.settings import GuardSettings
from tayyari.guard.ratelimit import (
    GLOBAL_PREPARE_BUCKET,
    SUBNET_LIMIT_FACTOR,
    RateLimiter,
    Route,
    limit_message,
)
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

H = "aaaa111122223333"
H2 = "bbbb111122223333"
NOON = datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC)


def make(**guard: object) -> tuple[RateLimiter, FakeRepositories, FakeClock]:
    clock = FakeClock(NOON)
    fakes = FakeRepositories(clock)
    return RateLimiter(fakes.limits, GuardSettings(**guard), clock), fakes, clock


async def refused(limiter: RateLimiter, subject: str | None = H, ip_48: str | None = None) -> RateLimited:
    with pytest.raises(RateLimited) as info:
        await limiter.check("prepare", subject, ip_48, memory=False)
    return info.value


def test_the_defaults() -> None:
    guard = GuardSettings()
    assert (guard.prepare_per_minute, guard.prepare_per_hour, guard.prepare_per_day) == (2, 10, 30)
    assert guard.prepare_global_per_hour == 120
    assert (guard.est_prepare_usd, guard.est_prepare_headline_usd) == (0.06, 0.02)


def test_the_route_is_known_to_the_limiter() -> None:
    assert "prepare" in get_args(Route)
    assert GLOBAL_PREPARE_BUCKET == "prepare:global:hour"


async def test_the_minute_window_says_a_plan_is_on_its_way() -> None:
    limiter, _, clock = make(prepare_per_minute=1, prepare_per_hour=0, prepare_per_day=0)
    clock.advance(20)
    await limiter.check("prepare", H, memory=False)
    err = await refused(limiter)
    assert err.status == 429
    assert err.user_message == "A plan is already on its way. Please wait 40 seconds and try again."
    assert err.retry_after_s == pytest.approx(40) and "prepare:min" in (err.detail or "")


async def test_the_hour_and_day_windows_have_their_own_sentences() -> None:
    limiter, _, clock = make(prepare_per_minute=0, prepare_per_hour=2, prepare_per_day=3)
    for _ in range(2):
        await limiter.check("prepare", H, memory=False)
    err = await refused(limiter)
    assert err.user_message == "You've reached the hourly limit for plans. Please try again in about an hour."
    clock.advance(3600)
    await limiter.check("prepare", H, memory=False)
    clock.advance(3600)
    err = await refused(limiter)
    assert err.user_message.startswith("You've reached today's limit for plans. It resets in ")
    assert "prepare:day" in (err.detail or "")


async def test_an_account_counts_beside_the_address_and_the_whole_service_once() -> None:
    limiter, fakes, _ = make(
        prepare_per_minute=0, prepare_per_hour=0, prepare_per_day=2, prepare_global_per_hour=120
    )
    for _ in range(2):
        await limiter.check("prepare", H, memory=False)
        await limiter.check("prepare", "u:abc", memory=False, globally=False)
    hits = fakes.limits.hits
    day, hour = NOON.replace(hour=0), NOON.replace(minute=0)
    assert hits[(f"prepare:day:{H}", day)] == 2 and hits[("prepare:day:u:abc", day)] == 2
    assert hits[(GLOBAL_PREPARE_BUCKET, hour)] == 2
    with pytest.raises(RateLimited):
        await limiter.check("prepare", "u:abc", memory=False, globally=False)  # from any address
    await limiter.check("prepare", H2, memory=False)  # another address has its own count


async def test_the_whole_service_backstop_is_hourly() -> None:
    limiter, _, clock = make(prepare_global_per_hour=2)
    for i in range(2):
        await limiter.check("prepare", f"client{i:011d}", memory=False)
    err = await refused(limiter, "someone-else-0000")
    assert err.user_message == "Leafout is very busy right now. Please try again in about an hour."
    clock.advance(3600)
    await limiter.check("prepare", "someone-else-0000", memory=False)


async def test_a_window_of_zero_is_not_enforced_and_exempt_counts_nothing() -> None:
    limiter, fakes, _ = make(
        prepare_per_minute=0, prepare_per_hour=0, prepare_per_day=0, prepare_global_per_hour=0
    )
    for _ in range(30):
        await limiter.check("prepare", H, memory=False)
    assert fakes.limits.hits == {}
    limiter, fakes, _ = make(prepare_per_minute=1, prepare_per_hour=1, prepare_per_day=1)
    for _ in range(5):
        limiter.shed("prepare", H, exempt=True)
        await limiter.check("prepare", H, memory=False, exempt=True)
    assert fakes.limits.hits == {}


async def test_an_ipv6_neighbourhood_is_counted_at_four_times_the_address() -> None:
    limiter, _, _ = make(prepare_per_minute=0, prepare_per_hour=0, prepare_per_day=1)
    await limiter.check("prepare", H, "net48", memory=False)
    for i in range(SUBNET_LIMIT_FACTOR - 1):
        await limiter.check("prepare", f"household{i:07d}", "net48", memory=False)
    with pytest.raises(RateLimited) as info:
        await limiter.check("prepare", "household-last-0", "net48", memory=False)
    assert "prepare:day:48" in (info.value.detail or "")


async def test_the_in_memory_shed_uses_the_minute_window() -> None:
    limiter, fakes, _ = make()  # two a minute: a bucket of four
    for _ in range(4):
        limiter.shed("prepare", H)
    with pytest.raises(RateLimited) as info:
        limiter.shed("prepare", H)
    assert info.value.user_message.startswith("A plan is already on its way.")
    assert fakes.limits.hits == {}


@pytest.mark.parametrize(
    ("window", "text"),
    [
        ("min", "A plan is already on its way. Please wait about 2 minutes and try again."),
        ("hour", "You've reached the hourly limit for plans. Please try again in about 2 minutes."),
        ("day", "You've reached today's limit for plans. It resets in about 2 minutes."),
        ("global", "Leafout is very busy right now. Please try again in about 2 minutes."),
    ],
)
def test_the_sentences_for_a_person_who_hit_a_limit(window: str, text: str) -> None:
    assert limit_message("prepare", window, 120) == text
    assert "%" not in text and "$" not in text
