from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from tayyari.contract.records import LlmCallRecord
from tayyari.core.errors import AppError, ServiceUnavailable
from tayyari.core.settings import GuardSettings
from tayyari.guard import spend
from tayyari.guard.spend import (
    LEDGER_UNAVAILABLE_RETRY_S,
    SpendGuard,
    seconds_to_utc_midnight,
)
from tayyari.llm.errors import SPEND_CEILING_MESSAGE, SpendCeilingReached
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories
from tests.unit.api.helpers import RecordingLog

NOON = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
USD = 1_000_000


class Ledger:
    """A LimitRepo whose ledger the test sets directly; counts reads and can fail or hang."""

    def __init__(self, micro: int = 0) -> None:
        self.micro = micro
        self.reads: list[date] = []
        self.error: Exception | None = None
        self.hang = False

    async def spent_today(self, day: date) -> int:
        self.reads.append(day)
        if self.hang:
            await asyncio.sleep(3600)
        if self.error is not None:
            raise self.error
        await asyncio.sleep(0)
        return self.micro

    async def hit(self, bucket: str, window_s: int, now: datetime) -> int:  # pragma: no cover - unused here
        raise NotImplementedError

    async def purge_hits(self, before: datetime) -> int:  # pragma: no cover - unused here
        raise NotImplementedError


def make(
    micro_usd: float = 0.0, *, ceiling: float = 20.0, **kwargs: object
) -> tuple[SpendGuard, Ledger, FakeClock]:
    clock = FakeClock(NOON)
    ledger = Ledger(round(micro_usd * USD))
    settings = GuardSettings(daily_ceiling_usd=ceiling)
    return SpendGuard(ledger, settings, clock, db_timeout_s=0.5, **kwargs), ledger, clock  # type: ignore[arg-type]


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    recorder = RecordingLog()
    monkeypatch.setattr(spend, "log", recorder)
    return recorder


# ───────────────────────────── admit: before a request ─────────────────────────────


async def test_admit_passes_while_spend_plus_estimate_is_under_the_ceiling() -> None:
    guard, _, _ = make(10.0)
    await guard.admit(0.40)
    await guard.admit(0.0)


async def test_admit_refuses_when_spend_plus_estimate_reaches_the_ceiling() -> None:
    guard, _, _ = make(19.70)
    await guard.admit(0.29)  # 19.99 < 20
    with pytest.raises(SpendCeilingReached) as info:
        await guard.admit(0.30)  # 20.00 >= 20
    err = info.value
    assert isinstance(err, ServiceUnavailable)
    assert isinstance(err, AppError)
    assert err.status == 503
    assert err.code == "spend_ceiling_reached"
    assert err.user_message == SPEND_CEILING_MESSAGE
    assert "usage limit" in err.user_message
    assert "sample profile still works" in err.user_message


async def test_the_boundary_is_inclusive() -> None:
    guard, _, _ = make(19.60)
    with pytest.raises(SpendCeilingReached):
        await guard.admit(0.40)  # 20.00 exactly
    guard2, _, _ = make(19.599999)
    await guard2.admit(0.40)


async def test_retry_after_is_the_time_to_the_next_utc_midnight() -> None:
    guard, _, clock = make(25.0)
    with pytest.raises(SpendCeilingReached) as info:
        await guard.admit(0.4)
    assert info.value.retry_after_s == pytest.approx(12 * 3600)
    assert info.value.retry_after_header() == "43200"
    clock.advance(3600 + 30)  # 13:00:30
    guard._cache = None  # force a re-read at the new time
    with pytest.raises(SpendCeilingReached) as info2:
        await guard.admit(0.4)
    assert info2.value.retry_after_s == pytest.approx(11 * 3600 - 30)


async def test_already_over_the_ceiling_refuses_even_a_free_request() -> None:
    guard, _, _ = make(20.0)
    with pytest.raises(SpendCeilingReached):
        await guard.admit(0.0)


async def test_a_negative_estimate_counts_as_zero() -> None:
    guard, _, _ = make(19.9)
    await guard.admit(-5.0)


async def test_a_ceiling_of_zero_switches_ai_off() -> None:
    guard, ledger, _ = make(0.0, ceiling=0.0)
    with pytest.raises(SpendCeilingReached):
        await guard.admit(0.0)
    with pytest.raises(SpendCeilingReached):
        await guard.admit_call("chat")
    assert guard.tripped() is True
    assert ledger.reads  # admit still read the ledger; tripped() on its own needs no ledger


async def test_admit_logs_the_numbers_not_the_message(log: RecordingLog) -> None:
    guard, _, _ = make(25.0)
    with pytest.raises(SpendCeilingReached):
        await guard.admit(0.4)
    (level, event, fields) = log.records[-1]
    assert (level, event) == ("info", "spend_ceiling_reached")
    assert fields["stage"] == "admit"
    assert fields["spent_micro_usd"] == 25 * USD


# ───────────────────────────── admit_call: before each model call ─────────────────────────────


async def test_admit_call_allows_calls_up_to_the_grace_ceiling() -> None:
    guard, ledger, clock = make(20.0)  # at the ceiling: a run in flight may finish
    await guard.admit_call("synth")
    ledger.micro = round(24.99 * USD)
    clock.advance(6)
    await guard.admit_call("synth")


async def test_admit_call_refuses_at_ceiling_times_grace() -> None:
    guard, ledger, clock = make(25.0)  # 20 x 1.25
    with pytest.raises(SpendCeilingReached) as info:
        await guard.admit_call("reader")
    assert info.value.retry_after_s == pytest.approx(12 * 3600)
    assert guard.hard_ceiling_micro == 25 * USD
    ledger.micro = 30 * USD
    clock.advance(6)
    with pytest.raises(SpendCeilingReached):
        await guard.admit_call("synth")


async def test_the_grace_factor_comes_from_the_settings() -> None:
    clock = FakeClock(NOON)
    ledger = Ledger(30 * USD)
    guard = SpendGuard(ledger, GuardSettings(daily_ceiling_usd=20.0, ceiling_grace=2.0), clock)
    await guard.admit_call("chat")  # 30 < 40
    ledger.micro = 40 * USD
    clock.advance(6)
    with pytest.raises(SpendCeilingReached):
        await guard.admit_call("chat")


async def test_admit_call_names_the_purpose_in_the_log(log: RecordingLog) -> None:
    guard, _, _ = make(30.0)
    with pytest.raises(SpendCeilingReached):
        await guard.admit_call("decision_fallback")
    assert log.records[-1][2]["purpose"] == "decision_fallback"
    assert log.records[-1][2]["stage"] == "call"


# ───────────────────────────── the 5 second cache ─────────────────────────────


async def test_the_ledger_is_read_once_per_five_seconds() -> None:
    guard, ledger, clock = make(1.0)
    for _ in range(10):
        await guard.admit(0.1)
        await guard.admit_call("chat")
    assert len(ledger.reads) == 1
    clock.advance(4.9)
    await guard.admit(0.1)
    assert len(ledger.reads) == 1
    clock.advance(0.1)  # 5.0 s: stale
    await guard.admit(0.1)
    assert len(ledger.reads) == 2


async def test_a_change_in_the_ledger_is_seen_after_the_ttl() -> None:
    guard, ledger, clock = make(1.0)
    await guard.admit(0.1)
    ledger.micro = 50 * USD
    await guard.admit(0.1)  # still the cached 1.0
    clock.advance(5)
    with pytest.raises(SpendCeilingReached):
        await guard.admit(0.1)


async def test_the_ttl_is_configurable() -> None:
    guard, ledger, clock = make(1.0, cache_ttl_s=60.0)
    await guard.admit(0.1)
    clock.advance(59)
    await guard.admit(0.1)
    assert len(ledger.reads) == 1
    clock.advance(1)
    await guard.admit(0.1)
    assert len(ledger.reads) == 2


async def test_a_new_utc_day_invalidates_the_cache_at_once() -> None:
    guard, ledger, clock = make(25.0, cache_ttl_s=10**9)  # a TTL that never expires: only the date can matter
    with pytest.raises(SpendCeilingReached):
        await guard.admit(0.4)
    ledger.micro = 0  # a new day starts with an empty ledger row
    clock.advance(12 * 3600 + 1)  # 00:00:01 the next day
    await guard.admit(0.4)
    assert ledger.reads == [date(2026, 10, 7), date(2026, 10, 8)]


async def test_concurrent_callers_share_one_ledger_read() -> None:
    guard, ledger, _ = make(1.0)
    await asyncio.gather(
        *(guard.admit(0.1) for _ in range(25)), *(guard.admit_call("chat") for _ in range(25))
    )
    assert len(ledger.reads) == 1


async def test_refresh_reads_now_and_does_not_hide_the_old_value_meanwhile() -> None:
    guard, ledger, _ = make(1.0)
    await guard.admit(0.1)
    ledger.micro = 30 * USD
    await guard.refresh()
    assert len(ledger.reads) == 2
    with pytest.raises(SpendCeilingReached):
        await guard.admit(0.1)


# ───────────────────────────── when the ledger cannot be read ─────────────────────────────


async def test_admit_fails_closed_and_says_so_in_the_log(log: RecordingLog) -> None:
    guard, ledger, _ = make()
    ledger.error = RuntimeError("connection refused")
    with pytest.raises(SpendCeilingReached) as info:
        await guard.admit(0.4)
    assert info.value.retry_after_s == LEDGER_UNAVAILABLE_RETRY_S
    assert info.value.status == 503
    assert info.value.detail == "spend ledger unavailable"
    assert "spend_ledger_unavailable" in log.events("error")
    assert "spend_guard_failing_closed" in log.events("error")
    assert "connection refused" not in str(log.records)  # the error type is logged, not its text


async def test_admit_call_fails_open(log: RecordingLog) -> None:
    guard, ledger, _ = make()
    ledger.error = RuntimeError("connection refused")
    await guard.admit_call("synth")  # no exception
    assert "spend_guard_failing_open" in log.events("warning")


async def test_a_failed_read_is_not_retried_for_two_seconds() -> None:
    guard, ledger, clock = make()
    ledger.error = RuntimeError("down")
    for _ in range(5):
        with pytest.raises(SpendCeilingReached):
            await guard.admit(0.4)
        await guard.admit_call("chat")
    assert len(ledger.reads) == 1
    clock.advance(2.1)
    ledger.error = None
    await guard.admit(0.4)  # the ledger is back
    assert len(ledger.reads) == 2


async def test_a_slow_ledger_counts_as_unavailable() -> None:
    clock = FakeClock(NOON)
    ledger = Ledger()
    ledger.hang = True
    guard = SpendGuard(ledger, GuardSettings(), clock, db_timeout_s=0.02)
    with pytest.raises(SpendCeilingReached):
        await asyncio.wait_for(guard.admit(0.4), 1)
    clock.advance(3)
    await asyncio.wait_for(guard.admit_call("chat"), 1)  # fail open


async def test_cancellation_is_not_swallowed() -> None:
    clock = FakeClock(NOON)
    ledger = Ledger()
    ledger.hang = True
    guard = SpendGuard(ledger, GuardSettings(), clock, db_timeout_s=30)
    task = asyncio.create_task(guard.admit(0.4))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # the lock was released: another caller is not stuck behind the cancelled one
    ledger.hang = False
    await asyncio.wait_for(guard.admit(0.4), 1)


# ───────────────────────────── tripped(): the synchronous check ─────────────────────────────


def test_tripped_outside_an_event_loop_is_false_and_harmless() -> None:
    guard, ledger, _ = make(99.0)
    assert guard.tripped() is False
    assert ledger.reads == []


async def test_tripped_starts_a_background_refresh_and_then_reports_the_ledger() -> None:
    guard, ledger, _ = make(25.0)
    assert guard.tripped() is False  # nothing known yet: counts as not tripped
    for _ in range(10):
        await asyncio.sleep(0)
    assert len(ledger.reads) == 1
    assert guard.tripped() is True
    assert len(ledger.reads) == 1  # the cache is fresh: no new read


async def test_tripped_follows_admit_without_extra_reads() -> None:
    guard, ledger, _ = make(25.0)
    with pytest.raises(SpendCeilingReached):
        await guard.admit(0.1)
    assert guard.tripped() is True
    assert len(ledger.reads) == 1


async def test_tripped_uses_the_ceiling_not_the_grace_level() -> None:
    guard, _, _ = make(21.0)  # over 20, under 25
    await guard.refresh()
    assert guard.tripped() is True
    await guard.admit_call("chat")  # calls in flight are still allowed


async def test_tripped_is_false_below_the_ceiling() -> None:
    guard, _, _ = make(19.99)
    await guard.refresh()
    assert guard.tripped() is False


async def test_a_stale_reading_still_answers_and_refreshes_in_the_background() -> None:
    guard, ledger, clock = make(25.0)
    await guard.refresh()
    ledger.micro = 0
    clock.advance(10)  # the reading is stale
    assert guard.tripped() is True  # the last known verdict, immediately
    for _ in range(10):
        await asyncio.sleep(0)
    assert guard.tripped() is False  # the refresh landed
    assert len(ledger.reads) == 2


async def test_only_one_background_refresh_runs_at_a_time() -> None:
    guard, ledger, _ = make(1.0)
    for _ in range(20):
        guard.tripped()
    for _ in range(10):
        await asyncio.sleep(0)
    assert len(ledger.reads) == 1


async def test_a_new_day_makes_a_tripped_reading_not_tripped() -> None:
    guard, ledger, clock = make(25.0, cache_ttl_s=10**9)
    await guard.refresh()
    assert guard.tripped() is True
    ledger.micro = 0
    clock.advance(12 * 3600)  # midnight
    assert guard.tripped() is False  # yesterday's spend does not count today
    for _ in range(10):
        await asyncio.sleep(0)
    assert ledger.reads[-1] == date(2026, 10, 8)


async def test_tripped_when_the_ledger_is_unreadable_is_not_tripped() -> None:
    guard, ledger, _ = make()
    ledger.error = RuntimeError("down")
    assert guard.tripped() is False
    for _ in range(10):
        await asyncio.sleep(0)
    assert guard.tripped() is False


async def test_aclose_cancels_a_pending_refresh() -> None:
    guard, ledger, _ = make()
    ledger.hang = True
    assert guard.tripped() is False
    await asyncio.sleep(0.01)
    assert guard._refresh_task is not None
    await guard.aclose()
    assert guard._refresh_task is None
    await guard.aclose()  # idempotent


# ───────────────────────────── switched off ─────────────────────────────


async def test_a_disabled_guard_never_looks_at_the_ledger() -> None:
    clock = FakeClock(NOON)
    ledger = Ledger(10_000 * USD)
    guard = SpendGuard(ledger, GuardSettings(enabled=False, daily_ceiling_usd=1.0), clock)
    await guard.admit(5.0)
    await guard.admit_call("chat")
    assert guard.tripped() is False
    assert ledger.reads == []


# ───────────────────────────── with the real fakes ─────────────────────────────


async def test_the_ledger_the_repositories_keep_is_what_the_guard_reads() -> None:
    clock = FakeClock(NOON)
    repos = FakeRepositories(clock)
    guard = SpendGuard(repos.limits, GuardSettings(daily_ceiling_usd=1.0), clock)
    await guard.admit(0.4)

    def call(cost_usd: float, when: datetime) -> LlmCallRecord:
        return LlmCallRecord(
            id=uuid.uuid4(),
            created_at=when,
            purpose="chat",
            model="claude-sonnet-5-5",
            status="ok",
            cost_micro_usd=round(cost_usd * USD),
        )

    await repos.llm_calls.record(call(0.30, NOON))
    await repos.llm_calls.record(call(0.30, NOON))
    await repos.llm_calls.record(call(5.00, NOON - timedelta(days=1)))  # yesterday does not count
    clock.advance(6)  # past the cache
    await guard.admit(0.1)  # 0.60 + 0.10 < 1.0
    with pytest.raises(SpendCeilingReached):
        await guard.admit(0.4)  # 0.60 + 0.40 >= 1.0
    await guard.admit_call("chat")  # but a call in flight (limit 1.25) is fine
    await repos.llm_calls.record(call(0.70, NOON))
    clock.advance(6)
    with pytest.raises(SpendCeilingReached):
        await guard.admit_call("chat")  # 1.30 >= 1.25


# ───────────────────────────── helpers ─────────────────────────────


@pytest.mark.parametrize(
    ("now", "seconds"),
    [
        (NOON, 12 * 3600),
        (datetime(2026, 10, 7, 0, 0, 0, tzinfo=UTC), 24 * 3600),
        (datetime(2026, 10, 7, 23, 59, 59, tzinfo=UTC), 1),
        (datetime(2026, 10, 7, 23, 59, 59, 600_000, tzinfo=UTC), 1),  # never below one second
        (datetime(2026, 12, 31, 18, 0, 0, tzinfo=UTC), 6 * 3600),  # across a year end
        (datetime(2028, 2, 28, 12, 0, 0, tzinfo=UTC), 12 * 3600),  # before a leap day
    ],
)
def test_seconds_to_utc_midnight(now: datetime, seconds: float) -> None:
    assert seconds_to_utc_midnight(now) == pytest.approx(seconds)


def test_seconds_to_utc_midnight_converts_other_time_zones() -> None:
    plus_two = datetime(2026, 10, 8, 1, 0, 0, tzinfo=timezone(timedelta(hours=2)))  # 23:00 UTC on the 7th
    assert seconds_to_utc_midnight(plus_two) == pytest.approx(3600)
