"""``exempt=True`` on `RateLimiter.shed` and `check` (an account on the unlimited list): it counts nothing anywhere, and a
request that is not exempt in the very same setup is still limited."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tayyari.core.errors import RateLimited
from tayyari.core.settings import GuardSettings
from tayyari.guard.ratelimit import GLOBAL_MATCH_BUCKET, RateLimiter, Route
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

H = "aaaa111122223333"
H48 = "cccc111122223333"
NOON = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)

TINY = {
    "analyze_per_minute": 1,
    "analyze_per_hour": 1,
    "analyze_per_day": 1,
    "analyze_global_per_hour": 1,
    "chat_per_minute": 1,
    "chat_per_hour": 1,
    "chat_per_day": 1,
    "chat_global_per_hour": 1,
    "portfolio_per_minute": 1,
    "portfolio_per_hour": 1,
    "portfolio_per_day": 1,
    "portfolio_global_per_hour": 1,
    "match_per_minute": 1,
    "match_per_hour": 1,
    "match_per_day": 1,
    "match_global_per_hour": 1,
    "anon_match_per_day": 1,
    "jobsearch_per_minute": 1,
    "jobsearch_per_hour": 1,
    "jobsearch_per_day": 1,
    "jobsearch_global_per_hour": 1,
    "anon_jobsearch_per_day": 1,
    "interview_per_minute": 1,
    "interview_per_hour": 1,
    "interview_per_day": 1,
    "interview_global_per_hour": 1,
    "interview_again_per_day": 1,
    "speech_per_minute": 1,
    "prepare_per_minute": 1,
    "prepare_per_hour": 1,
    "prepare_per_day": 1,
    "prepare_global_per_hour": 1,
}
"""Every window of every route allows one request."""

ROUTES: list[Route] = [
    "analyze",
    "chat",
    "portfolio",
    "match",
    "match_anon",
    "jobsearch",
    "jobsearch_anon",
    "interview",
    "interview_again",
    "speech",
    "prepare",
]


def make() -> tuple[RateLimiter, FakeRepositories]:
    clock = FakeClock(NOON)
    repos = FakeRepositories(clock)
    return RateLimiter(repos.limits, GuardSettings(**TINY), clock, db_timeout_s=0.5), repos


@pytest.mark.parametrize("route", ROUTES)
async def test_an_exempt_caller_is_never_limited_on_any_route(route: Route) -> None:
    limiter, _ = make()
    for _ in range(12):
        limiter.shed(route, H, H48, exempt=True)
        await limiter.check(route, H, H48, exempt=True)
        await limiter.check(route, f"u:{H}", memory=False, globally=False, exempt=True)


@pytest.mark.parametrize("route", ROUTES)
async def test_the_same_setup_still_limits_everyone_else(route: Route) -> None:
    limiter, _ = make()
    await limiter.check(route, H, H48)
    with pytest.raises(RateLimited):
        await limiter.check(route, H, H48)


@pytest.mark.parametrize("route", ROUTES)
async def test_shed_alone_still_turns_hammering_away_for_everyone_else(route: Route) -> None:
    limiter, _ = make()
    with pytest.raises(RateLimited):
        for _ in range(20):
            limiter.shed(route, H)


async def test_an_exempt_call_takes_no_token_and_leaves_no_counter() -> None:
    limiter, repos = make()
    for _ in range(50):
        limiter.shed("match", H, H48, exempt=True)
        await limiter.check("match", H, H48, exempt=True)
    assert repos.limits.hits == {}  # nothing written to the shared counters, the whole-service one included

    # nor was a token taken from the bucket: a normal caller still has its whole allowance
    limiter.shed("match", H, H48)
    await limiter.check("match", H, H48, memory=False)
    assert repos.limits.hits and max(repos.limits.hits.values()) == 1
    assert any(bucket == GLOBAL_MATCH_BUCKET for bucket, _ in repos.limits.hits)


async def test_exempt_calls_do_not_use_up_the_whole_service_counter() -> None:
    limiter, _ = make()
    for _ in range(5):
        await limiter.check("match", H, exempt=True)
    await limiter.check("match", "someone-else")  # the one request the whole service allows this hour
    with pytest.raises(RateLimited):
        await limiter.check("match", "another")


async def test_exemption_is_per_call_not_per_caller() -> None:
    limiter, _ = make()
    await limiter.check("chat", H, exempt=True)
    await limiter.check("chat", H, exempt=True)
    await limiter.check("chat", H)  # the first counted one
    with pytest.raises(RateLimited):
        await limiter.check("chat", H)
