from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest

from tayyari.core.errors import AppError, RateLimited
from tayyari.core.settings import GuardSettings
from tayyari.guard import ratelimit
from tayyari.guard.ratelimit import (
    GLOBAL_ANALYZE_BUCKET,
    InProcessBucket,
    RateLimiter,
    limit_message,
    seconds_to_window_end,
    wait_phrase,
)
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeLimitRepo, FakeRepositories
from tests.unit.api.helpers import RecordingLog

H = "aaaa111122223333"
H2 = "bbbb111122223333"
NOON = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


class Spy:
    """A LimitRepo that counts calls and can be told to fail or hang."""

    def __init__(self, inner: FakeLimitRepo) -> None:
        self.inner = inner
        self.hit_calls = 0
        self.error: Exception | None = None
        self.hang = False

    async def hit(self, bucket: str, window_s: int, now: datetime) -> int:
        self.hit_calls += 1
        if self.hang:
            await asyncio.sleep(3600)
        if self.error is not None:
            raise self.error
        return await self.inner.hit(bucket, window_s, now)

    async def spent_today(self, day: date) -> int:
        return await self.inner.spent_today(day)

    async def purge_hits(self, before: datetime) -> int:
        return await self.inner.purge_hits(before)


def make(clock: FakeClock | None = None, **guard: object) -> tuple[RateLimiter, Spy, FakeClock]:
    clock = clock or FakeClock(NOON)
    spy = Spy(FakeRepositories(clock).limits)
    limiter = RateLimiter(spy, GuardSettings(**guard), clock, db_timeout_s=0.5)
    return limiter, spy, clock


async def refused(limiter: RateLimiter, route: str, ip: str | None = H) -> RateLimited:
    with pytest.raises(RateLimited) as info:
        await limiter.check(route, ip)  # type: ignore[arg-type]
    return info.value


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    recorder = RecordingLog()
    monkeypatch.setattr(ratelimit, "log", recorder)
    return recorder


# ───────────────────────────── per-IP windows ─────────────────────────────


async def test_requests_within_every_limit_pass() -> None:
    limiter, _, _ = make()
    await limiter.check("analyze", H)
    await limiter.check("analyze", H)


async def test_the_request_over_the_minute_limit_is_a_429_with_the_time_to_the_window_end() -> None:
    limiter, _, clock = make()
    clock.advance(20)  # 12:00:20, so the minute window ends in 40 s
    await limiter.check("analyze", H)
    await limiter.check("analyze", H)
    err = await refused(limiter, "analyze")

    assert isinstance(err, AppError)
    assert err.status == 429
    assert err.code == "rate_limited"
    assert err.retry_after_s == pytest.approx(40)
    assert err.retry_after_header() == "40"
    assert err.user_message == "That's a lot of analyses in a row. Please wait 40 seconds and try again."
    assert err.to_body() == {"error": err.user_message}
    assert "analyze:min" in (err.detail or "")  # for the server log; the client never sees it


async def test_the_minute_window_opens_again_at_the_next_minute() -> None:
    limiter, _, clock = make()
    for _ in range(2):
        await limiter.check("analyze", H)
    await refused(limiter, "analyze")
    clock.advance(60)  # 12:01:00 is a new window
    await limiter.check("analyze", H)
    await limiter.check("analyze", H)
    await refused(limiter, "analyze")


async def test_the_hour_window() -> None:
    limiter, _, clock = make(analyze_per_minute=0, analyze_per_hour=3, analyze_per_day=0)
    clock.advance(10 * 60)  # 12:10
    for _ in range(3):
        await limiter.check("analyze", H)
    err = await refused(limiter, "analyze")
    assert err.retry_after_s == pytest.approx(50 * 60)
    assert (
        err.user_message
        == "You've reached the hourly limit for analyses. Please try again in about 50 minutes."
    )
    clock.advance(50 * 60)  # 13:00 starts a new hour
    await limiter.check("analyze", H)


async def test_the_day_window_resets_at_utc_midnight() -> None:
    limiter, _, clock = make(analyze_per_minute=0, analyze_per_hour=0, analyze_per_day=2)
    for _ in range(2):
        await limiter.check("analyze", H)
    err = await refused(limiter, "analyze")
    assert err.retry_after_s == pytest.approx(12 * 3600)
    assert err.user_message == "You've reached today's limit for analyses. It resets in about 12 hours."
    clock.advance(12 * 3600)  # 00:00 UTC next day
    await limiter.check("analyze", H)


async def test_chat_has_its_own_rules_and_texts() -> None:
    limiter, _, _ = make(chat_per_minute=3)
    for _ in range(3):
        await limiter.check("chat", H)
    err = await refused(limiter, "chat")
    assert err.user_message == "You're sending messages quickly. Please wait about a minute and try again."
    await limiter.check("analyze", H)  # analyze counters are separate


async def test_each_client_has_its_own_budget() -> None:
    limiter, _, _ = make()
    for _ in range(2):
        await limiter.check("analyze", H)
    await refused(limiter, "analyze", H)
    await limiter.check("analyze", H2)


async def test_clients_whose_address_is_unknown_share_one_bucket() -> None:
    limiter, spy, _ = make()
    await limiter.check("analyze", None)
    await limiter.check("analyze", None)
    await refused(limiter, "analyze", None)
    assert any(b.startswith("analyze:min:unknown") for b, _ in spy.inner.hits)


async def test_bucket_keys_carry_the_window_so_sizes_never_share_a_counter() -> None:
    limiter, spy, _ = make()
    await limiter.check("analyze", H)
    await limiter.check("chat", H)
    assert {bucket for bucket, _ in spy.inner.hits} == {
        f"analyze:min:{H}",
        f"analyze:hour:{H}",
        f"analyze:day:{H}",
        GLOBAL_ANALYZE_BUCKET,
        f"chat:min:{H}",
        f"chat:hour:{H}",
        f"chat:day:{H}",
    }
    # All three windows start at the same instant (midnight would be the extreme): the counters stay apart.
    window_starts = {key: start for key, start in spy.inner.hits}
    assert window_starts[f"analyze:min:{H}"] == NOON


async def test_checking_stops_at_the_first_window_that_is_exceeded() -> None:
    limiter, spy, _ = make(analyze_per_minute=1, analyze_per_hour=5, analyze_per_day=5)
    await limiter.check("analyze", H)
    await refused(limiter, "analyze")
    counts = {bucket: count for (bucket, _), count in spy.inner.hits.items()}
    assert counts[f"analyze:min:{H}"] == 2  # the rejected request still counts against the window it broke
    assert counts[f"analyze:hour:{H}"] == 1  # but does not eat the longer budgets
    assert counts[f"analyze:day:{H}"] == 1
    assert counts[GLOBAL_ANALYZE_BUCKET] == 1  # nor the global one


async def test_a_zero_limit_means_the_window_is_not_enforced() -> None:
    limiter, spy, _ = make(
        analyze_per_minute=0, analyze_per_hour=0, analyze_per_day=0, analyze_global_per_hour=0
    )
    for _ in range(50):
        await limiter.check("analyze", H)
    assert spy.hit_calls == 0


async def test_switched_off_does_nothing_at_all() -> None:
    limiter, spy, _ = make(enabled=False, analyze_per_minute=1)
    for _ in range(20):
        await limiter.check("analyze", H)
    assert spy.hit_calls == 0


# ───────────────────────────── the global analyze backstop ─────────────────────────────


async def test_the_global_backstop_caps_analyses_across_all_clients() -> None:
    limiter, _, clock = make(analyze_global_per_hour=5)
    for i in range(5):
        await limiter.check("analyze", f"client{i:011d}")
    err = await refused(limiter, "analyze", "someone-else-0000")
    assert err.user_message == "Tayyari is very busy right now. Please try again in about an hour."
    assert err.status == 429
    assert err.retry_after_s == pytest.approx(3600)
    clock.advance(3600)
    await limiter.check("analyze", "someone-else-0000")


async def test_the_global_backstop_does_not_apply_to_chat() -> None:
    limiter, spy, _ = make(analyze_global_per_hour=1)
    for i in range(10):
        await limiter.check("chat", f"client{i:011d}")
    assert GLOBAL_ANALYZE_BUCKET not in {bucket for bucket, _ in spy.inner.hits}


async def test_a_client_over_its_own_limit_does_not_use_up_the_global_budget() -> None:
    limiter, spy, _ = make(analyze_per_minute=1, analyze_global_per_hour=60)
    await limiter.check("analyze", H)
    for _ in range(5):
        await refused(limiter, "analyze")
    assert spy.inner.hits[(GLOBAL_ANALYZE_BUCKET, NOON.replace(minute=0))] == 1


async def test_the_global_backstop_text_wording() -> None:
    assert (
        limit_message("analyze", "global", 1800)
        == "Tayyari is very busy right now. Please try again in about 30 minutes."
    )


# ───────────────────────────── the in-process bucket ─────────────────────────────


async def test_hammering_is_shed_in_memory_without_touching_the_database() -> None:
    limiter, spy, _ = make()  # 2/min: the bucket holds 4 tokens and refills 2 per minute
    outcomes: list[str] = []
    calls_after: list[int] = []
    for _ in range(8):
        try:
            await limiter.check("analyze", H)
            outcomes.append("ok")
        except RateLimited:
            outcomes.append("limited")
        calls_after.append(spy.hit_calls)
    assert outcomes == ["ok", "ok"] + ["limited"] * 6
    # Requests 3 and 4 were refused by the database rule; from the 5th the bucket answers alone.
    assert calls_after[3] == calls_after[4] == calls_after[7]
    assert calls_after[3] > calls_after[1]


async def test_the_memory_answer_is_a_429_with_a_short_retry_after() -> None:
    limiter, _, clock = make()
    for _ in range(4):
        try:
            await limiter.check("analyze", H)
        except RateLimited:
            pass
    err = await refused(limiter, "analyze")
    assert err.status == 429
    assert err.retry_after_s == pytest.approx(30)  # one token every 30 s at 2 per minute
    assert "wait 30 seconds" in err.user_message
    clock.advance(31)
    # A token is back in memory; the database window (12:00) still says no.
    err = await refused(limiter, "analyze")
    assert "analyze:min" in (err.detail or "")


async def test_the_bucket_never_turns_away_a_client_the_database_rules_accept() -> None:
    limiter, _, clock = make()  # 2/min, 6/hour
    for _ in range(3):  # two analyses at the start of three consecutive minutes: within every rule
        await limiter.check("analyze", H)
        await limiter.check("analyze", H)
        clock.advance(60)


async def test_shed_is_memory_only_and_never_touches_the_database() -> None:
    limiter, spy, _ = make()  # a 4-token bucket for analyze
    for _ in range(4):
        limiter.shed("analyze", H)
    with pytest.raises(RateLimited) as info:
        limiter.shed("analyze", H)
    assert info.value.status == 429
    assert spy.hit_calls == 0


async def test_shed_then_check_without_memory_takes_one_token_per_request() -> None:
    limiter, _, _ = make(
        analyze_per_minute=10, analyze_per_hour=0, analyze_per_day=0, analyze_global_per_hour=0
    )
    for _ in range(10):  # the bucket holds 20 tokens: ten requests use ten, not twenty
        limiter.shed("analyze", H)
        await limiter.check("analyze", H, memory=False)
    with pytest.raises(RateLimited):
        await limiter.check("analyze", H, memory=False)  # the eleventh: refused by the shared minute counter
    for _ in range(10):
        limiter.shed("analyze", H)  # ten tokens are still left in memory
    with pytest.raises(RateLimited):
        limiter.shed("analyze", H)


async def test_check_without_memory_still_uses_the_shared_counters_and_fails_open() -> None:
    limiter, spy, _ = make()
    for _ in range(2):
        await limiter.check("analyze", H, memory=False)
    with pytest.raises(RateLimited):
        await limiter.check("analyze", H, memory=False)
    spy.error = RuntimeError("down")
    await limiter.check("analyze", H2, memory=False)  # fails open


def test_shed_does_nothing_when_the_limiter_is_off() -> None:
    limiter, _, _ = make(enabled=False, analyze_per_minute=1)
    for _ in range(20):
        limiter.shed("analyze", H)


def test_in_process_bucket_burst_and_refill() -> None:
    clock = FakeClock()
    bucket = InProcessBucket(clock)
    assert [bucket.take("k", capacity=3, refill_per_s=1.0) for _ in range(3)] == [0.0, 0.0, 0.0]
    assert bucket.take("k", capacity=3, refill_per_s=1.0) == pytest.approx(1.0)
    clock.advance(0.5)
    assert bucket.take("k", capacity=3, refill_per_s=1.0) == pytest.approx(0.5)
    clock.advance(0.5)
    assert bucket.take("k", capacity=3, refill_per_s=1.0) == 0.0
    clock.advance(1000)  # a long idle refills only to capacity
    assert [bucket.take("k", capacity=3, refill_per_s=1.0) for _ in range(4)][-1] > 0


def test_in_process_bucket_keys_are_independent() -> None:
    bucket = InProcessBucket(FakeClock())
    assert bucket.take("a", capacity=1, refill_per_s=0.01) == 0.0
    assert bucket.take("a", capacity=1, refill_per_s=0.01) > 0
    assert bucket.take("b", capacity=1, refill_per_s=0.01) == 0.0


def test_in_process_bucket_forgets_the_least_recently_used_keys() -> None:
    bucket = InProcessBucket(FakeClock(), max_keys=3)
    for key in "abcd":
        bucket.take(key, capacity=1, refill_per_s=0.001)
    assert len(bucket) == 3
    # "a" was evicted, so it starts over with a full bucket; "d" is still empty.
    assert bucket.take("a", capacity=1, refill_per_s=0.001) == 0.0
    assert bucket.take("d", capacity=1, refill_per_s=0.001) > 0


def test_in_process_bucket_with_no_refill_never_recovers() -> None:
    bucket = InProcessBucket(FakeClock())
    assert bucket.take("a", capacity=1, refill_per_s=0.0) == 0.0
    assert bucket.take("a", capacity=1, refill_per_s=0.0) == float("inf")


# ───────────────────────────── when the database fails ─────────────────────────────


async def test_a_repository_error_fails_open_and_is_logged(log: RecordingLog) -> None:
    limiter, spy, _ = make()
    spy.error = RuntimeError("connection reset")
    await limiter.check("analyze", H)  # no exception: the request goes through
    assert log.events("warning") == ["rate_limit_repo_unavailable"]
    (_, _, fields) = log.records[0]
    assert fields["failing_open"] is True
    assert fields["error"] == "RuntimeError"
    assert H not in str(fields)


async def test_after_a_failure_the_database_is_left_alone_for_a_while() -> None:
    limiter, spy, clock = make()
    spy.error = RuntimeError("down")
    for _ in range(3):
        await limiter.check("analyze", H)
    assert spy.hit_calls == 1  # one failed attempt, then back-off (15 s)
    clock.advance(14)
    await limiter.check("analyze", H2)
    assert spy.hit_calls == 1  # still backing off
    clock.advance(2)
    spy.error = None  # the database is back
    await limiter.check("analyze", H2)
    assert spy.hit_calls == 1 + 4  # the failed call, then minute, hour, day and global counters
    await limiter.check("analyze", H2)
    await refused(limiter, "analyze", H2)  # enforcement is back: the third request in the minute is refused


async def test_while_the_database_is_down_the_memory_bucket_still_limits() -> None:
    limiter, spy, _ = make()
    spy.error = RuntimeError("down")
    outcomes = []
    for _ in range(8):
        try:
            await limiter.check("analyze", H)
            outcomes.append("ok")
        except RateLimited:
            outcomes.append("limited")
    assert outcomes == ["ok"] * 4 + ["limited"] * 4  # the 4-token burst, then nothing


async def test_a_slow_database_fails_open_after_the_timeout(log: RecordingLog) -> None:
    clock = FakeClock(NOON)
    spy = Spy(FakeRepositories(clock).limits)
    spy.hang = True
    limiter = RateLimiter(spy, GuardSettings(), clock, db_timeout_s=0.02)
    await asyncio.wait_for(limiter.check("analyze", H), 1)
    assert log.events("warning") == ["rate_limit_repo_unavailable"]
    assert log.records[0][2]["error"] == "TimeoutError"


async def test_cancellation_is_never_swallowed() -> None:
    clock = FakeClock(NOON)
    spy = Spy(FakeRepositories(clock).limits)
    spy.hang = True
    limiter = RateLimiter(spy, GuardSettings(), clock, db_timeout_s=30)
    task = asyncio.create_task(limiter.check("analyze", H))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_concurrent_requests_are_counted_exactly() -> None:
    limiter, spy, _ = make(
        analyze_per_minute=10, analyze_per_hour=0, analyze_per_day=0, analyze_global_per_hour=0
    )

    async def attempt() -> bool:
        try:
            await limiter.check("analyze", H)
        except RateLimited:
            return False
        return True

    results = await asyncio.gather(*(attempt() for _ in range(30)))
    assert results.count(True) == 10
    assert results.count(False) == 20
    assert spy.inner.hits[(f"analyze:min:{H}", NOON)] <= 20  # the bucket (20 tokens) shed the rest


# ───────────────────────────── texts and helpers ─────────────────────────────


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "1 second"),
        (0.2, "1 second"),
        (1, "1 second"),
        (12.3, "13 seconds"),
        (59, "59 seconds"),
        (60, "about a minute"),
        (61, "about 2 minutes"),
        (300, "about 5 minutes"),
        (3599, "about 60 minutes"),
        (3600, "about an hour"),
        (3601, "about 2 hours"),
        (43200, "about 12 hours"),
        (86400, "about 24 hours"),
    ],
)
def test_wait_phrase(seconds: float, text: str) -> None:
    assert wait_phrase(seconds) == text


def test_every_route_and_window_has_a_friendly_message() -> None:
    for route in ("analyze", "chat"):
        for window in ("min", "hour", "day"):
            text = limit_message(route, window, 120)
            assert "{" not in text
            assert "about 2 minutes" in text
    assert "about 2 minutes" in limit_message("analyze", "global", 120)
    assert "about 2 minutes" in limit_message("elsewhere", "min", 120)  # an unknown pair still reads fine


def test_seconds_to_window_end() -> None:
    assert seconds_to_window_end(NOON, 60) == 60
    assert seconds_to_window_end(NOON.replace(second=45), 60) == 15
    assert seconds_to_window_end(NOON, 3600) == 3600
    assert seconds_to_window_end(NOON.replace(minute=59, second=59), 3600) == 1
    assert seconds_to_window_end(NOON, 86_400) == 12 * 3600
    assert seconds_to_window_end(NOON.replace(hour=23, minute=59, second=59), 86_400) == 1
