"""The ``speech`` rate-limit route: one per-minute window per address for the pieces of answers and the spoken lines, with its own
sentence, no hour or day window, no account counter and no whole-service counter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

import pytest

from tayyari.core.errors import RateLimited
from tayyari.core.settings import GuardSettings
from tayyari.guard.ratelimit import SUBNET_LIMIT_FACTOR, RateLimiter, Route, limit_message
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

H = "aaaa111122223333"
H2 = "bbbb111122223333"
NOON = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


def make(**guard: object) -> tuple[RateLimiter, FakeRepositories, FakeClock]:
    clock = FakeClock(NOON)
    fakes = FakeRepositories(clock)
    return RateLimiter(fakes.limits, GuardSettings(**guard), clock), fakes, clock


def test_speech_is_a_known_route_and_the_default_is_forty_a_minute() -> None:
    assert "speech" in get_args(Route)
    assert GuardSettings().speech_per_minute == 40


def test_the_sentence_names_speech_and_how_long_to_wait() -> None:
    assert limit_message("speech", "min", 12) == "Too many speech requests at once. Please wait 12 seconds."


async def test_the_forty_first_request_in_a_minute_is_refused_with_a_retry_after() -> None:
    limiter, _, clock = make()
    clock.advance(20)  # 12:00:20, so the minute ends in 40 seconds
    for _ in range(40):
        await limiter.check("speech", H, memory=False)
    with pytest.raises(RateLimited) as caught:
        await limiter.check("speech", H, memory=False)
    assert caught.value.status == 429
    assert caught.value.user_message == "Too many speech requests at once. Please wait 40 seconds."
    assert caught.value.retry_after_s == pytest.approx(40)
    assert "speech:min" in (caught.value.detail or "")
    clock.advance(40)
    await limiter.check("speech", H, memory=False)  # the next minute starts again


async def test_another_address_has_its_own_count() -> None:
    limiter, _, _ = make(speech_per_minute=1)
    await limiter.check("speech", H, memory=False)
    with pytest.raises(RateLimited):
        await limiter.check("speech", H, memory=False)
    await limiter.check("speech", H2, memory=False)


async def test_only_the_minute_window_is_counted_and_no_whole_service_counter() -> None:
    limiter, fakes, _ = make(speech_per_minute=5)
    for _ in range(3):
        await limiter.check("speech", H, memory=False)
        await limiter.check("speech", "u:abc", memory=False, globally=False)
    assert {bucket for bucket, _ in fakes.limits.hits} == {f"speech:min:{H}", "speech:min:u:abc"}


async def test_a_limit_of_zero_is_not_enforced() -> None:
    limiter, fakes, _ = make(speech_per_minute=0)
    for _ in range(100):
        await limiter.check("speech", H, memory=False)
        limiter.shed("speech", H)
    assert fakes.limits.hits == {}


async def test_an_ipv6_neighbourhood_is_counted_at_four_times_the_address() -> None:
    limiter, _, _ = make(speech_per_minute=1)
    await limiter.check("speech", H, "net48", memory=False)
    for i in range(SUBNET_LIMIT_FACTOR - 1):
        await limiter.check("speech", f"household{i:07d}", "net48", memory=False)
    with pytest.raises(RateLimited) as caught:
        await limiter.check("speech", "household-last-0", "net48", memory=False)
    assert "speech:min:48" in (caught.value.detail or "")


async def test_the_in_memory_shed_turns_hammering_away_without_the_database() -> None:
    limiter, fakes, _ = make(speech_per_minute=2)  # a bucket of four
    for _ in range(4):
        limiter.shed("speech", H)
    with pytest.raises(RateLimited) as caught:
        limiter.shed("speech", H)
    assert caught.value.user_message.startswith("Too many speech requests at once.")
    assert fakes.limits.hits == {}
    limiter.shed("speech", H2)


async def test_speech_does_not_touch_the_other_routes_buckets() -> None:
    limiter, _, _ = make(speech_per_minute=1, chat_per_minute=1)
    await limiter.check("speech", H, memory=False)
    await limiter.check("chat", H, memory=False)  # a full speech minute leaves chat alone
    with pytest.raises(RateLimited):
        await limiter.check("speech", H, memory=False)
