"""The rate-limit rules of the practice interview: ``interview`` (starting one: per address, per account, and across the
service per hour) and ``interview_again`` (asking one question of a finished interview again: one daily window)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

import pytest

from tayyari.core.errors import RateLimited
from tayyari.core.settings import GuardSettings
from tayyari.guard.ratelimit import (
    GLOBAL_INTERVIEW_BUCKET,
    SUBNET_LIMIT_FACTOR,
    RateLimiter,
    Route,
    limit_message,
)
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

H = "aaaa111122223333"
H2 = "bbbb111122223333"
NOON = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
FREE = "You've used today's practice interviews. They're free while we test them. More in "


def make(**guard: object) -> tuple[RateLimiter, FakeRepositories, FakeClock]:
    clock = FakeClock(NOON)
    fakes = FakeRepositories(clock)
    return RateLimiter(fakes.limits, GuardSettings(**guard), clock), fakes, clock


async def refused(
    limiter: RateLimiter, route: Route, subject: str | None = H, ip_48: str | None = None
) -> RateLimited:
    with pytest.raises(RateLimited) as info:
        await limiter.check(route, subject, ip_48, memory=False)
    return info.value


def test_the_defaults_are_the_three_a_day_the_product_promises() -> None:
    guard = GuardSettings()
    assert (guard.interview_per_minute, guard.interview_per_hour, guard.interview_per_day) == (2, 3, 3)
    assert guard.interview_global_per_hour == 60 and guard.interview_again_per_day == 20
    assert (guard.est_interview_usd, guard.est_interview_turn_usd, guard.est_interview_end_usd) == (
        0.03,
        0.005,
        0.02,
    )


def test_both_routes_are_known_to_the_limiter() -> None:
    assert {"interview", "interview_again"} <= set(get_args(Route))
    assert GLOBAL_INTERVIEW_BUCKET == "interview:global:hour"


# ───────────────────────── starting an interview ─────────────────────────


async def test_the_minute_window_says_an_interview_is_already_starting() -> None:
    limiter, _, clock = make(interview_per_minute=1, interview_per_hour=0, interview_per_day=0)
    clock.advance(20)  # 12:00:20, so the minute ends in 40 s
    await limiter.check("interview", H, memory=False)
    err = await refused(limiter, "interview")
    assert err.status == 429
    assert (
        err.user_message == "A practice interview is already starting. Please wait 40 seconds and try again."
    )
    assert err.retry_after_s == pytest.approx(40)
    assert "interview:min" in (err.detail or "")


async def test_the_hour_and_day_windows_say_they_are_free_while_testing() -> None:
    limiter, _, clock = make(interview_per_minute=0, interview_per_hour=2, interview_per_day=3)
    for _ in range(2):
        await limiter.check("interview", H, memory=False)
    err = await refused(limiter, "interview")
    assert err.user_message == FREE + "about an hour."
    assert "interview:hour" in (err.detail or "")
    clock.advance(3600)
    await limiter.check("interview", H, memory=False)  # the third of the day
    clock.advance(3600)
    err = await refused(limiter, "interview")
    assert err.user_message.startswith(FREE + "about ") and "interview:day" in (err.detail or "")
    assert err.retry_after_s is not None and 0 < err.retry_after_s <= 86_400


async def test_the_shortest_window_answers_first_and_a_rejected_burst_does_not_eat_the_longer_budgets() -> (
    None
):
    limiter, fakes, _ = make(interview_per_minute=1, interview_per_hour=5, interview_per_day=5)
    await limiter.check("interview", H, memory=False)
    for _ in range(3):
        await refused(limiter, "interview")
    hits = fakes.limits.hits
    assert hits[(f"interview:min:{H}", NOON)] == 4  # the refused three were counted in the minute...
    assert hits[(f"interview:hour:{H}", NOON)] == 1  # ...and nowhere else
    assert hits[(f"interview:day:{H}", NOON.replace(hour=0))] == 1


async def test_another_address_has_its_own_count() -> None:
    limiter, _, _ = make(interview_per_day=1, interview_per_minute=0, interview_per_hour=0)
    await limiter.check("interview", H, memory=False)
    await refused(limiter, "interview")
    await limiter.check("interview", H2, memory=False)


async def test_an_account_subject_counts_beside_the_address_and_the_whole_service_once() -> None:
    limiter, fakes, _ = make(
        interview_per_minute=0, interview_per_hour=0, interview_per_day=2, interview_global_per_hour=60
    )
    for _ in range(2):
        await limiter.check("interview", H, memory=False)
        await limiter.check("interview", "u:abc", memory=False, globally=False)
    hits = fakes.limits.hits
    day, hour = NOON.replace(hour=0), NOON.replace(minute=0)
    assert hits[(f"interview:day:{H}", day)] == 2
    assert hits[("interview:day:u:abc", day)] == 2
    assert hits[(GLOBAL_INTERVIEW_BUCKET, hour)] == 2  # one per start, not one per subject
    err = await refused(
        limiter, "interview", "u:abc"
    )  # the account is over its two even from a fresh address
    assert err.user_message.startswith(FREE)


async def test_one_account_is_limited_whatever_address_it_uses() -> None:
    limiter, _, _ = make(interview_per_minute=0, interview_per_hour=0, interview_per_day=1)
    for ip in (H, H2):
        await limiter.check("interview", ip, memory=False)
        if ip == H:
            await limiter.check("interview", "u:abc", memory=False, globally=False)
    with pytest.raises(RateLimited):
        await limiter.check("interview", "u:abc", memory=False, globally=False)


async def test_the_whole_service_backstop_is_hourly_and_says_tayyari_is_busy() -> None:
    limiter, _, clock = make(interview_global_per_hour=2)
    for i in range(2):
        await limiter.check("interview", f"client{i:011d}", memory=False)
    err = await refused(limiter, "interview", "someone-else-0000")
    assert err.user_message == "Tayyari is very busy right now. Please try again in about an hour."
    clock.advance(3600)
    await limiter.check("interview", "someone-else-0000", memory=False)


async def test_a_window_of_zero_is_not_enforced() -> None:
    limiter, fakes, _ = make(
        interview_per_minute=0, interview_per_hour=0, interview_per_day=0, interview_global_per_hour=0
    )
    for _ in range(30):
        await limiter.check("interview", H, memory=False)
    assert fakes.limits.hits == {}


async def test_an_ipv6_neighbourhood_is_counted_at_four_times_the_address() -> None:
    limiter, _, _ = make(interview_per_minute=0, interview_per_hour=0, interview_per_day=1)
    await limiter.check("interview", H, "net48", memory=False)
    for i in range(SUBNET_LIMIT_FACTOR - 1):  # three more households in the same /48
        await limiter.check("interview", f"household{i:07d}", "net48", memory=False)
    with pytest.raises(RateLimited) as info:
        await limiter.check("interview", "household-last-0", "net48", memory=False)
    assert "interview:day:48" in (info.value.detail or "")


async def test_the_in_memory_shed_uses_the_minute_window_and_never_the_database() -> None:
    limiter, fakes, _ = make()  # two a minute: a bucket of four
    for _ in range(4):
        limiter.shed("interview", H)
    with pytest.raises(RateLimited) as info:
        limiter.shed("interview", H)
    assert info.value.user_message.startswith("A practice interview is already starting.")
    assert fakes.limits.hits == {}
    limiter.shed("interview", H2)  # another address has its own


# ───────────────────────── asking one question again ─────────────────────────


async def test_ask_again_has_one_daily_window_and_its_own_sentence() -> None:
    limiter, fakes, clock = make(interview_again_per_day=2, interview_global_per_hour=1)
    for _ in range(2):
        await limiter.check("interview_again", H, memory=False)
    err = await refused(limiter, "interview_again")
    assert err.user_message.startswith("That's enough repeats for today. More in about ")
    assert err.retry_after_s is not None and 0 < err.retry_after_s <= 86_400
    assert "interview_again:day" in (err.detail or "")
    buckets = {bucket for bucket, _ in fakes.limits.hits}
    assert buckets == {f"interview_again:day:{H}"}  # no minute, no hour, and no whole-service counter
    await limiter.check("interview_again", H2, memory=False)
    clock.advance(86_400)
    await limiter.check("interview_again", H, memory=False)  # the next day starts again


async def test_ask_again_counts_the_account_and_the_neighbourhood_too() -> None:
    limiter, _, _ = make(interview_again_per_day=1)
    await limiter.check("interview_again", "u:abc", memory=False, globally=False)
    with pytest.raises(RateLimited):
        await limiter.check("interview_again", "u:abc", memory=False, globally=False)
    await limiter.check("interview_again", H, "net48", memory=False)
    for i in range(SUBNET_LIMIT_FACTOR - 1):
        await limiter.check("interview_again", f"household{i:07d}", "net48", memory=False)
    with pytest.raises(RateLimited) as info:
        await limiter.check("interview_again", "household-last-0", "net48", memory=False)
    assert "interview_again:day:48" in (info.value.detail or "")


async def test_ask_again_of_zero_is_not_enforced() -> None:
    limiter, fakes, _ = make(interview_again_per_day=0)
    for _ in range(50):
        await limiter.check("interview_again", H, memory=False)
    assert fakes.limits.hits == {}


async def test_a_repeat_does_not_use_up_an_interview() -> None:
    limiter, _, _ = make(
        interview_per_minute=0, interview_per_hour=0, interview_per_day=1, interview_again_per_day=5
    )
    for _ in range(5):
        await limiter.check("interview_again", H, memory=False)
    await limiter.check("interview", H, memory=False)  # the one interview of the day is still there


# ───────────────────────── the sentences ─────────────────────────


@pytest.mark.parametrize(
    ("route", "window", "text"),
    [
        (
            "interview",
            "min",
            "A practice interview is already starting. Please wait about 2 minutes and try again.",
        ),
        ("interview", "hour", FREE + "about 2 minutes."),
        ("interview", "day", FREE + "about 2 minutes."),
        ("interview", "global", "Tayyari is very busy right now. Please try again in about 2 minutes."),
        ("interview_again", "day", "That's enough repeats for today. More in about 2 minutes."),
    ],
)
def test_the_sentences_for_a_person_who_hit_a_limit(route: str, window: str, text: str) -> None:
    assert limit_message(route, window, 120) == text


def test_no_sentence_mentions_a_price_or_a_score() -> None:
    texts = [
        limit_message(route, window, 90)
        for route, window in (
            ("interview", "min"),
            ("interview", "hour"),
            ("interview", "day"),
            ("interview", "global"),
            ("interview_again", "day"),
        )
    ]
    for text in texts:
        assert "{" not in text and "$" not in text and "₹" not in text and "%" not in text
