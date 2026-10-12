from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest

from tayyari.core.errors import AppError, RateLimited
from tayyari.core.settings import GuardSettings
from tayyari.guard import ratelimit
from tayyari.guard.ratelimit import (
    GLOBAL_ANALYZE_BUCKET,
    GLOBAL_CHAT_BUCKET,
    GLOBAL_MATCH_BUCKET,
    GLOBAL_PORTFOLIO_BUCKET,
    SUBNET_LIMIT_FACTOR,
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


async def refused(
    limiter: RateLimiter, route: str, ip: str | None = H, ip_48: str | None = None
) -> RateLimited:
    with pytest.raises(RateLimited) as info:
        await limiter.check(route, ip, ip_48)  # type: ignore[arg-type]
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
        GLOBAL_CHAT_BUCKET,
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


# ───────────────────────────── the global chat backstop ─────────────────────────────


async def test_the_chat_global_backstop_caps_chat_turns_across_all_clients() -> None:
    limiter, spy, clock = make(chat_global_per_hour=5)
    for i in range(5):
        await limiter.check("chat", f"client{i:011d}")
    err = await refused(limiter, "chat", "someone-else-0000")
    assert err.status == 429
    assert err.code == "rate_limited"
    assert err.user_message == "Tayyari is very busy right now. Please try again in about an hour."
    assert err.retry_after_s == pytest.approx(3600)
    assert err.retry_after_header() == "3600"
    assert "chat:global" in (err.detail or "")
    assert spy.inner.hits[(GLOBAL_CHAT_BUCKET, NOON)] == 6
    clock.advance(3600)  # a new hour has a new counter
    await limiter.check("chat", "someone-else-0000")


async def test_the_chat_global_bucket_is_named_like_the_analyze_one() -> None:
    assert GLOBAL_CHAT_BUCKET == "chat:global:hour"
    limiter, spy, _ = make()
    await limiter.check("chat", H)
    assert (GLOBAL_CHAT_BUCKET, NOON) in spy.inner.hits
    assert spy.inner.hits[(GLOBAL_CHAT_BUCKET, NOON)] == 1


async def test_the_chat_global_backstop_defaults_to_600_an_hour() -> None:
    assert GuardSettings().chat_global_per_hour == 600
    limiter, _, _ = make(chat_per_minute=0, chat_per_hour=0, chat_per_day=0)
    for i in range(600):
        await limiter.check("chat", f"client{i:011d}")
    await refused(limiter, "chat", "one-too-many-000")


async def test_the_chat_global_backstop_does_not_apply_to_analyze() -> None:
    limiter, spy, _ = make(chat_global_per_hour=1)
    for i in range(5):
        await limiter.check("analyze", f"client{i:011d}")
    assert GLOBAL_CHAT_BUCKET not in {bucket for bucket, _ in spy.inner.hits}


async def test_the_two_global_backstops_count_separately() -> None:
    limiter, _, _ = make(chat_global_per_hour=2, analyze_global_per_hour=2)
    for i in range(2):
        await limiter.check("chat", f"chat{i:012d}")
    await refused(limiter, "chat", "chat-extra-00000")
    for i in range(2):  # analyze still has its whole budget
        await limiter.check("analyze", f"analyze{i:09d}")
    await refused(limiter, "analyze", "analyze-extra-00")


async def test_a_zero_chat_global_limit_means_not_enforced() -> None:
    limiter, spy, _ = make(chat_global_per_hour=0, chat_per_minute=0, chat_per_hour=0, chat_per_day=0)
    for i in range(50):
        await limiter.check("chat", f"client{i:011d}")
    assert spy.hit_calls == 0


async def test_a_chat_client_over_its_own_limit_does_not_use_up_the_global_budget() -> None:
    limiter, spy, _ = make(chat_per_minute=1, chat_global_per_hour=10)
    await limiter.check("chat", H)
    for _ in range(5):
        await refused(limiter, "chat")
    assert spy.inner.hits[(GLOBAL_CHAT_BUCKET, NOON.replace(minute=0))] == 1


async def test_the_chat_global_backstop_fails_open_with_the_database() -> None:
    limiter, spy, _ = make(chat_global_per_hour=1)
    spy.error = RuntimeError("down")
    for i in range(3):
        await limiter.check("chat", f"client{i:011d}")


def test_the_chat_global_text_wording() -> None:
    assert (
        limit_message("chat", "global", 1800)
        == "Tayyari is very busy right now. Please try again in about 30 minutes."
    )


# ───────────────────────────── portfolio route ─────────────────────────────


async def test_portfolio_has_its_own_rules_and_texts() -> None:
    limiter, _, _ = make(portfolio_per_minute=2)
    for _ in range(2):
        await limiter.check("portfolio", H)
    err = await refused(limiter, "portfolio")
    assert (
        err.user_message == "You're generating portfolios quickly. Please wait about a minute and try again."
    )
    await limiter.check("analyze", H)  # analyze counters are separate


async def test_the_portfolio_global_backstop_caps_portfolios_across_all_clients() -> None:
    limiter, _, clock = make(portfolio_global_per_hour=3)
    for i in range(3):
        await limiter.check("portfolio", f"client{i:011d}")
    err = await refused(limiter, "portfolio", "someone-else-0000")
    assert err.user_message == "Tayyari is very busy right now. Please try again in about an hour."
    assert err.status == 429
    assert err.retry_after_s == pytest.approx(3600)
    clock.advance(3600)
    await limiter.check("portfolio", "someone-else-0000")


async def test_a_zero_portfolio_limit_means_not_enforced() -> None:
    limiter, spy, _ = make(
        portfolio_per_minute=0, portfolio_per_hour=0, portfolio_per_day=0, portfolio_global_per_hour=0
    )
    for _ in range(50):
        await limiter.check("portfolio", H)
    assert spy.hit_calls == 0


async def test_the_portfolio_global_backstop_does_not_apply_to_other_routes() -> None:
    limiter, spy, _ = make(portfolio_global_per_hour=1)
    for i in range(10):
        await limiter.check("chat", f"client{i:011d}")
    assert GLOBAL_PORTFOLIO_BUCKET not in {bucket for bucket, _ in spy.inner.hits}


async def test_a_portfolio_client_over_its_own_limit_does_not_use_up_the_global_budget() -> None:
    limiter, spy, _ = make(portfolio_per_minute=1, portfolio_global_per_hour=60)
    await limiter.check("portfolio", H)
    for _ in range(5):
        await refused(limiter, "portfolio")
    assert spy.inner.hits[(GLOBAL_PORTFOLIO_BUCKET, NOON.replace(minute=0))] == 1


def test_the_portfolio_global_text_wording() -> None:
    assert (
        limit_message("portfolio", "global", 1800)
        == "Tayyari is very busy right now. Please try again in about 30 minutes."
    )


def test_portfolio_window_messages() -> None:
    assert (
        limit_message("portfolio", "min", 30)
        == "You're generating portfolios quickly. Please wait 30 seconds and try again."
    )
    assert (
        limit_message("portfolio", "hour", 3600)
        == "You've reached the hourly limit for portfolios. Please try again in about an hour."
    )
    assert (
        limit_message("portfolio", "day", 43200)
        == "You've reached today's limit for portfolios. It resets in about 12 hours."
    )


# ───────────────────────────── the IPv6 /48 bucket ─────────────────────────────

H48 = "dddd111122223333"
OTHER_48 = "eeee111122223333"


def slash64(i: int) -> str:
    """The keyed hash of the i-th /64 inside one /48 (any distinct strings do for the limiter)."""
    return f"{i:016x}"


async def test_addresses_hopping_between_slash64s_inside_one_slash48_trip_the_wider_bucket() -> None:
    limiter, _, _ = make()  # 2 a minute per /64, so 8 a minute per /48
    for i in range(2 * SUBNET_LIMIT_FACTOR):
        await limiter.check("analyze", slash64(i), H48)  # every /64 is far under its own limit
    err = await refused(limiter, "analyze", slash64(99), H48)
    assert err.status == 429
    assert err.user_message == "That's a lot of analyses in a row. Please wait about a minute and try again."
    assert err.retry_after_s == pytest.approx(60)
    assert "analyze:min:48" in (err.detail or "")
    assert "count=9 limit=8" in (err.detail or "")


async def test_without_the_slash48_the_same_hopping_goes_unnoticed() -> None:
    limiter, _, _ = make()
    for i in range(30):  # the hole the /48 bucket closes: a fresh /64 for every request
        await limiter.check("analyze", slash64(i))
    await limiter.check("analyze", slash64(100), None)


async def test_the_slash48_limit_is_four_times_the_per_ip_limit_in_every_window() -> None:
    limiter, _, clock = make(
        analyze_per_minute=0, analyze_per_hour=1, analyze_per_day=0, analyze_global_per_hour=0
    )
    for i in range(4):
        await limiter.check("analyze", slash64(i), H48)
    err = await refused(limiter, "analyze", slash64(4), H48)
    assert (
        err.user_message == "You've reached the hourly limit for analyses. Please try again in about an hour."
    )
    assert "analyze:hour:48" in (err.detail or "")
    clock.advance(3600)
    await limiter.check("analyze", slash64(5), H48)

    limiter, _, _ = make(
        analyze_per_minute=0, analyze_per_hour=0, analyze_per_day=2, analyze_global_per_hour=0
    )
    for i in range(8):
        await limiter.check("analyze", slash64(i), H48)
    err = await refused(limiter, "analyze", slash64(8), H48)
    assert "analyze:day:48" in (err.detail or "")
    assert err.user_message == "You've reached today's limit for analyses. It resets in about 12 hours."


async def test_different_slash48s_do_not_share_a_budget() -> None:
    limiter, _, _ = make(analyze_global_per_hour=0)
    for i in range(8):
        await limiter.check("analyze", slash64(i), H48)
    await limiter.check("analyze", slash64(50), OTHER_48)
    await refused(limiter, "analyze", slash64(51), H48)


async def test_the_slash48_is_counted_per_route() -> None:
    limiter, spy, _ = make(chat_per_minute=3, analyze_global_per_hour=0)
    for i in range(8):
        await limiter.check("analyze", slash64(i), H48)
    await limiter.check("chat", slash64(60), H48)  # chat keeps its own /48 counters
    assert f"chat:min:48:{H48}" in {bucket for bucket, _ in spy.inner.hits}
    assert spy.inner.hits[(f"chat:min:48:{H48}", NOON)] == 1
    assert spy.inner.hits[(f"analyze:min:48:{H48}", NOON)] == 8


async def test_the_slash48_buckets_are_named_after_route_window_and_network() -> None:
    limiter, spy, _ = make()
    await limiter.check("analyze", H, H48)
    assert {bucket for bucket, _ in spy.inner.hits} == {
        f"analyze:min:{H}",
        f"analyze:hour:{H}",
        f"analyze:day:{H}",
        f"analyze:min:48:{H48}",
        f"analyze:hour:48:{H48}",
        f"analyze:day:48:{H48}",
        GLOBAL_ANALYZE_BUCKET,
    }


async def test_a_noisy_slash64_does_not_eat_the_slash48_budget() -> None:
    limiter, spy, _ = make(analyze_global_per_hour=0)
    await limiter.check("analyze", H, H48, memory=False)
    await limiter.check("analyze", H, H48, memory=False)
    for _ in range(10):  # refused by its own minute window, so the /48 counters are never reached
        with pytest.raises(RateLimited):
            await limiter.check("analyze", H, H48, memory=False)
    assert spy.inner.hits[(f"analyze:min:48:{H48}", NOON)] == 2
    assert spy.inner.hits[(f"analyze:hour:48:{H48}", NOON.replace(minute=0))] == 2


async def test_a_slash48_rejection_does_not_use_up_the_global_budget() -> None:
    limiter, spy, _ = make(analyze_global_per_hour=60)
    for i in range(8):
        await limiter.check("analyze", slash64(i), H48)
    await refused(limiter, "analyze", slash64(9), H48)
    assert spy.inner.hits[(GLOBAL_ANALYZE_BUCKET, NOON.replace(minute=0))] == 8


async def test_the_slash48_bucket_fails_open_with_the_database() -> None:
    limiter, spy, _ = make(analyze_global_per_hour=0)
    spy.error = RuntimeError("down")
    for i in range(4):  # the memory bucket (4 tokens per /64, 16 per /48) is still in front
        await limiter.check("analyze", slash64(i), H48)


async def test_shed_has_a_slash48_bucket_too_and_never_touches_the_database() -> None:
    limiter, spy, _ = make(chat_per_minute=3)  # 6 tokens per /64, 24 per /48
    for i in range(24):
        limiter.shed("chat", slash64(i), H48)
    with pytest.raises(RateLimited) as info:
        limiter.shed("chat", slash64(24), H48)
    assert info.value.status == 429
    assert "chat:min:48" in (info.value.detail or "")
    assert spy.hit_calls == 0


async def test_shed_without_the_slash48_is_unchanged() -> None:
    limiter, _, _ = make(chat_per_minute=3)
    for i in range(100):
        limiter.shed("chat", slash64(i))  # a new /64 each time: nothing to count them together by
    for _ in range(6):
        limiter.shed("chat", H)
    with pytest.raises(RateLimited):
        limiter.shed("chat", H)


async def test_the_slash48_memory_bucket_is_only_charged_for_requests_the_slash64_bucket_grants() -> None:
    limiter, _, _ = make(chat_per_minute=1)  # 2 tokens per /64, 8 per /48
    for _ in range(2):
        limiter.shed("chat", H, H48)
    for _ in range(20):  # one /64 hammering: refused by its own bucket, the /48 is not drained
        with pytest.raises(RateLimited):
            limiter.shed("chat", H, H48)
    for i in range(6):  # six tokens are left for the neighbours
        limiter.shed("chat", slash64(i), H48)
    with pytest.raises(RateLimited):
        limiter.shed("chat", slash64(7), H48)


async def test_shed_then_check_takes_one_slash48_token_per_request() -> None:
    limiter, _, _ = make(
        chat_per_minute=2, chat_per_hour=0, chat_per_day=0, chat_global_per_hour=0
    )  # 16 /48 tokens in memory, 8 in the shared minute counter
    for i in range(8):
        limiter.shed("chat", slash64(i), H48)
        await limiter.check("chat", slash64(i), H48, memory=False)
    with pytest.raises(RateLimited):
        await limiter.check("chat", slash64(9), H48, memory=False)  # the ninth: the shared /48 counter
    for i in range(8):
        limiter.shed("chat", slash64(20 + i), H48)  # eight memory tokens are still left
    with pytest.raises(RateLimited):
        limiter.shed("chat", slash64(40), H48)


async def test_ip_hash_48_can_be_passed_by_keyword() -> None:
    limiter, spy, _ = make()
    await limiter.check("analyze", H, ip_hash_48=H48, memory=False)
    limiter.shed("analyze", H, ip_hash_48=H48)
    assert (f"analyze:min:48:{H48}", NOON) in spy.inner.hits


async def test_a_missing_slash48_means_only_the_per_client_rules_apply() -> None:
    limiter, spy, _ = make()
    await limiter.check("analyze", H, None)
    assert not any(":48:" in bucket for bucket, _ in spy.inner.hits)


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
    for route in ("analyze", "chat", "portfolio"):
        for window in ("min", "hour", "day"):
            text = limit_message(route, window, 120)
            assert "{" not in text
            assert "about 2 minutes" in text
    assert "about 2 minutes" in limit_message("analyze", "global", 120)
    assert "about 2 minutes" in limit_message("portfolio", "global", 120)
    assert "about 2 minutes" in limit_message("elsewhere", "min", 120)  # an unknown pair still reads fine


def test_seconds_to_window_end() -> None:
    assert seconds_to_window_end(NOON, 60) == 60
    assert seconds_to_window_end(NOON.replace(second=45), 60) == 15
    assert seconds_to_window_end(NOON, 3600) == 3600
    assert seconds_to_window_end(NOON.replace(minute=59, second=59), 3600) == 1
    assert seconds_to_window_end(NOON, 86_400) == 12 * 3600
    assert seconds_to_window_end(NOON.replace(hour=23, minute=59, second=59), 86_400) == 1


# ───────────────────────────── match route ─────────────────────────────


async def test_match_has_its_own_hourly_and_daily_rules_and_texts() -> None:
    limiter, _, clock = make(match_per_hour=2, match_per_day=3)
    for _ in range(2):
        await limiter.check("match", H)
    err = await refused(limiter, "match")
    assert (
        err.user_message == "You've reached the hourly limit for postings. Please try again in about an hour."
    )
    assert err.retry_after_s is not None and 0 < err.retry_after_s <= 3600
    await limiter.check("analyze", H)  # the other routes count on their own
    clock.advance(3600)
    await limiter.check("match", H)  # the third of the day
    clock.advance(3600)
    err = await refused(limiter, "match")
    assert err.user_message.startswith("You've reached today's limit for postings. It resets in about ")


async def test_the_match_minute_window_is_off_unless_set() -> None:
    limiter, _, _ = make(match_per_hour=0, match_per_day=0, match_global_per_hour=0)
    for _ in range(30):
        await limiter.check("match", H)
    limiter, _, _ = make(match_per_minute=1, match_per_hour=0, match_per_day=0, match_global_per_hour=0)
    await limiter.check("match", H)
    err = await refused(limiter, "match")
    assert err.user_message == "You're matching postings quickly. Please wait about a minute and try again."


async def test_the_match_global_backstop_caps_postings_across_all_clients() -> None:
    limiter, _, clock = make(match_global_per_hour=2)
    for i in range(2):
        await limiter.check("match", f"client{i:011d}")
    err = await refused(limiter, "match", "someone-else-0000")
    assert err.user_message == "Tayyari is very busy right now. Please try again in about an hour."
    clock.advance(3600)
    await limiter.check("match", "someone-else-0000")


async def test_a_visitor_with_no_account_has_a_daily_allowance_of_its_own() -> None:
    limiter, spy, clock = make(anon_match_per_day=3, match_global_per_hour=60)
    for _ in range(3):
        await limiter.check("match_anon", H, memory=False)
    err = await refused(limiter, "match_anon")
    assert err.user_message.startswith(
        "That's today's limit for postings without an account. It resets in about "
    )
    assert err.retry_after_s is not None and 0 < err.retry_after_s <= 86_400
    assert "match_anon:day" in (err.detail or "")
    await limiter.check("match_anon", H2)  # another address has its own three
    await limiter.check("match", H)  # and the ordinary match counters are a separate thing
    day = NOON.replace(hour=0)
    assert spy.inner.hits[(f"match_anon:day:{H}", day)] == 4  # the refused fourth was counted too
    assert not any(bucket.startswith("match_anon:global") for bucket, _ in spy.inner.hits)
    clock.advance(86_400)
    await limiter.check("match_anon", H)  # the next day starts again


async def test_the_visitors_allowance_has_no_minute_or_hour_window_and_no_whole_service_counter() -> None:
    limiter, spy, _ = make(anon_match_per_day=2, match_global_per_hour=1)
    for _ in range(2):
        await limiter.check("match_anon", H, memory=False)
    buckets = {bucket for bucket, _ in spy.inner.hits}
    assert buckets == {f"match_anon:day:{H}"}  # one counter: no minute, no hour, no global bucket


async def test_a_visitors_allowance_of_zero_is_not_enforced() -> None:
    limiter, spy, _ = make(anon_match_per_day=0)
    for _ in range(50):
        await limiter.check("match_anon", H, memory=False)
    assert spy.hit_calls == 0  # nothing to count


async def test_the_visitors_allowance_counts_an_ipv6_neighbourhood_at_four_times_the_address() -> None:
    limiter, _, _ = make(anon_match_per_day=1)
    await limiter.check("match_anon", H, "net48", memory=False)
    for i in range(SUBNET_LIMIT_FACTOR - 1):  # three more households in the same /48
        await limiter.check("match_anon", f"household{i:07d}", "net48", memory=False)
    with pytest.raises(RateLimited) as info:
        await limiter.check("match_anon", "household-last-0", "net48", memory=False)
    assert "match_anon:day:48" in (info.value.detail or "")


async def test_an_account_subject_counts_beside_the_address_without_counting_the_whole_service_twice() -> (
    None
):
    limiter, spy, _ = make(match_per_hour=2, match_per_day=0, match_global_per_hour=60)
    for _ in range(2):
        await limiter.check("match", H, memory=False)
        await limiter.check("match", "u:abc", memory=False, globally=False)
    hour = NOON.replace(minute=0)
    assert spy.inner.hits[(GLOBAL_MATCH_BUCKET, hour)] == 2  # one per request, not one per subject
    assert spy.inner.hits[(f"match:hour:{H}", hour)] == 2
    assert spy.inner.hits[("match:hour:u:abc", hour)] == 2
    with pytest.raises(RateLimited):
        await limiter.check("match", "u:abc", memory=False, globally=False)  # the account alone is over
    await limiter.check("match", "u:someone-else", memory=False, globally=False)
