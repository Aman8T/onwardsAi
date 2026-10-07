from __future__ import annotations

import asyncio
import uuid
from contextlib import AbstractAsyncContextManager
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


# ───────────────────────────── reserve: counting the work in flight ─────────────────────────────


async def test_reserve_is_an_async_context_manager_that_admits_and_releases() -> None:
    guard, _, _ = make(10.0)
    cm = guard.reserve(1.0)
    assert isinstance(cm, AbstractAsyncContextManager)
    async with cm:
        assert guard.reserved_micro == 1 * USD
        assert guard.reservations_in_flight == 1
    assert guard.reserved_micro == 0
    assert guard.reservations_in_flight == 0


async def test_200_concurrent_reservations_only_those_that_fit_are_admitted() -> None:
    guard, _, _ = make(0.0)  # ceiling 20.00, each turn estimated at 1.50
    gate = asyncio.Event()
    decided = asyncio.Event()
    admitted = 0
    refused = 0
    peak_reserved = 0

    def decide() -> None:
        if admitted + refused == 200:
            decided.set()

    async def turn() -> None:
        nonlocal admitted, refused, peak_reserved
        try:
            async with guard.reserve(1.50):
                admitted += 1
                peak_reserved = max(peak_reserved, guard.reserved_micro)
                decide()
                await gate.wait()  # every admitted turn is still running while the others arrive
        except SpendCeilingReached:
            refused += 1
            decide()

    tasks = [asyncio.create_task(turn()) for _ in range(200)]
    await decided.wait()

    # 13 x 1.50 = 19.50 fits under 20.00 (the 13th is admitted at 18.00 + 1.50 < 20); a 14th would reach 21.
    assert admitted == 13
    assert refused == 187
    assert guard.reservations_in_flight == 13
    assert guard.reserved_micro == round(13 * 1.50 * USD)
    assert peak_reserved == round(13 * 1.50 * USD) < guard.ceiling_micro

    gate.set()
    await asyncio.gather(*tasks)
    assert guard.reserved_micro == 0
    assert guard.reservations_in_flight == 0
    async with guard.reserve(1.50):  # the freed headroom is usable again
        assert guard.reserved_micro == round(1.50 * USD)


async def test_without_reserve_the_same_burst_is_admitted_in_full() -> None:
    """The flaw that `reserve` fixes: admit() alone sees only the lagging ledger, so every caller passes."""
    guard, _, _ = make(0.0)
    results = await asyncio.gather(*(guard.admit(1.50) for _ in range(200)), return_exceptions=True)
    assert not any(isinstance(r, Exception) for r in results)


async def test_a_reservation_is_released_when_the_body_raises() -> None:
    guard, _, _ = make(0.0)
    with pytest.raises(RuntimeError, match="boom"):
        async with guard.reserve(5.0):
            assert guard.reserved_micro == 5 * USD
            raise RuntimeError("boom")
    assert guard.reserved_micro == 0
    assert guard.reservations_in_flight == 0


async def test_exceptions_in_many_concurrent_bodies_release_every_reservation() -> None:
    guard, _, _ = make(0.0)

    async def failing(i: int) -> None:
        async with guard.reserve(1.0):
            await asyncio.sleep(0)
            if i % 2:
                raise ValueError(i)

    results = await asyncio.gather(*(failing(i) for i in range(15)), return_exceptions=True)
    assert sum(isinstance(r, ValueError) for r in results) == 7
    assert guard.reserved_micro == 0
    assert guard.reservations_in_flight == 0


async def test_a_reservation_is_released_when_the_task_is_cancelled() -> None:
    guard, _, _ = make(0.0)
    started = asyncio.Event()

    async def turn() -> None:
        async with guard.reserve(4.0):
            started.set()
            await asyncio.sleep(3600)

    task = asyncio.create_task(turn())
    await started.wait()
    assert guard.reserved_micro == 4 * USD
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert guard.reserved_micro == 0
    assert guard.reservations_in_flight == 0


async def test_cancelling_while_the_ledger_is_being_read_holds_nothing() -> None:
    guard, ledger, _ = make(0.0)
    ledger.hang = True

    async def turn() -> None:
        async with guard.reserve(4.0):
            raise AssertionError("must not be admitted")  # pragma: no cover

    task = asyncio.create_task(turn())
    await asyncio.sleep(0.01)  # inside the ledger read
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert guard.reserved_micro == 0
    assert guard.reservations_in_flight == 0


async def test_a_refused_reservation_holds_nothing_and_says_when_to_retry() -> None:
    guard, _, _ = make(19.0)
    with pytest.raises(SpendCeilingReached) as info:
        async with guard.reserve(1.0):  # 19 + 1 reaches the ceiling
            raise AssertionError("must not be admitted")  # pragma: no cover
    assert info.value.status == 503
    assert info.value.retry_after_s == pytest.approx(12 * 3600)
    assert guard.reserved_micro == 0
    assert guard.reservations_in_flight == 0


async def test_reservations_add_to_the_ledger_when_admitting() -> None:
    guard, _, _ = make(10.0)
    async with guard.reserve(8.0):  # 10 + 8 < 20
        with pytest.raises(SpendCeilingReached):
            async with guard.reserve(2.0):  # 10 + 8 + 2 reaches 20
                raise AssertionError("must not be admitted")  # pragma: no cover
        async with guard.reserve(1.99):
            assert guard.reserved_micro == round(9.99 * USD)
    assert guard.reserved_micro == 0
    async with guard.reserve(2.0):  # the first reservation is gone: 10 + 2 is fine
        pass


async def test_release_order_does_not_matter() -> None:
    guard, _, _ = make(0.0)
    a = guard.reserve(3.0)
    b = guard.reserve(4.0)
    await a.__aenter__()
    await b.__aenter__()
    assert guard.reserved_micro == 7 * USD
    await a.__aexit__(None, None, None)
    assert guard.reserved_micro == 4 * USD
    await b.__aexit__(None, None, None)
    assert guard.reserved_micro == 0


async def test_admit_sees_the_reservations_in_flight() -> None:
    guard, _, _ = make(0.0)
    await guard.admit(5.0)  # nothing in flight
    async with guard.reserve(15.0):
        await guard.admit(4.99)  # 15 + 4.99 < 20
        with pytest.raises(SpendCeilingReached):
            await guard.admit(5.0)  # 15 + 5 reaches 20
        assert guard.reserved_micro == 15 * USD  # admit holds nothing of its own
    await guard.admit(5.0)


async def test_admit_logs_the_reservations_too(log: RecordingLog) -> None:
    guard, _, _ = make(0.0)
    async with guard.reserve(15.0):
        with pytest.raises(SpendCeilingReached) as info:
            await guard.admit(6.0)
    (level, event, fields) = log.records[-1]
    assert (level, event) == ("info", "spend_ceiling_reached")
    assert fields["stage"] == "admit"
    assert fields["reserved_micro_usd"] == 15 * USD
    assert "reserved=15000000" in (info.value.detail or "")


async def test_reserve_logs_its_own_stage(log: RecordingLog) -> None:
    guard, _, _ = make(25.0)
    with pytest.raises(SpendCeilingReached):
        async with guard.reserve(1.0):
            pass
    assert log.records[-1][2]["stage"] == "reserve"


async def test_admit_call_sees_the_reservations_in_flight() -> None:
    guard, ledger, clock = make(10.0)  # ceiling 20, hard stop 25
    async with guard.reserve(5.0):  # admitted: 10 + 5 < 20
        await guard.admit_call("reader")  # 10 + 5 < 25
        ledger.micro = 20 * USD  # the run's own readers have been paid for meanwhile
        clock.advance(6)
        # The ledger alone (20) is under the hard stop (25); with the 5.00 still reserved it is not.
        with pytest.raises(SpendCeilingReached) as info:
            await guard.admit_call("synth")
        assert info.value.status == 503
        assert "reserved=5000000" in (info.value.detail or "")
    clock.advance(6)
    await guard.admit_call("synth")  # the reservation is gone: 20 < 25


async def test_admit_call_blocks_once_ledger_and_reservations_reach_ceiling_times_grace() -> None:
    guard, ledger, clock = make(0.0)
    async with guard.reserve(19.0):
        ledger.micro = round(5.99 * USD)
        clock.advance(6)
        await guard.admit_call("reader")  # 5.99 + 19 = 24.99 < 25
        ledger.micro = 6 * USD
        clock.advance(6)
        with pytest.raises(SpendCeilingReached):
            await guard.admit_call("reader")  # 6 + 19 = 25 >= 25


async def test_reserve_fails_closed_when_the_ledger_cannot_be_read(log: RecordingLog) -> None:
    guard, ledger, _ = make(0.0)
    ledger.error = RuntimeError("down")
    with pytest.raises(SpendCeilingReached) as info:
        async with guard.reserve(0.10):
            raise AssertionError("must not be admitted")  # pragma: no cover
    assert info.value.retry_after_s == LEDGER_UNAVAILABLE_RETRY_S
    assert guard.reserved_micro == 0
    assert "spend_guard_failing_closed" in log.events("error")


async def test_a_ceiling_of_zero_refuses_every_reservation() -> None:
    guard, _, _ = make(0.0, ceiling=0.0)
    with pytest.raises(SpendCeilingReached):
        async with guard.reserve(0.0):
            raise AssertionError("must not be admitted")  # pragma: no cover


async def test_a_negative_estimate_reserves_nothing() -> None:
    guard, _, _ = make(19.9)
    async with guard.reserve(-5.0):
        assert guard.reserved_micro == 0
        assert guard.reservations_in_flight == 1


async def test_a_disabled_guard_reserves_nothing_and_never_reads_the_ledger() -> None:
    clock = FakeClock(NOON)
    ledger = Ledger(30 * USD)
    guard = SpendGuard(ledger, GuardSettings(enabled=False, daily_ceiling_usd=1.0), clock)
    async with guard.reserve(50.0):
        assert guard.reserved_micro == 0
        assert guard.reservations_in_flight == 0
    assert ledger.reads == []


async def test_tripped_looks_only_at_the_ledger_not_at_reservations() -> None:
    guard, _, _ = make(0.0)
    await guard.refresh()
    async with guard.reserve(19.0):
        assert guard.tripped() is False


async def test_the_ledger_is_still_read_once_for_a_whole_burst_of_reservations() -> None:
    guard, ledger, _ = make(0.0)
    async with asyncio.TaskGroup() as group:
        for _ in range(50):
            group.create_task(_hold_briefly(guard))
    assert len(ledger.reads) == 1
    assert guard.reserved_micro == 0


async def _hold_briefly(guard: SpendGuard) -> None:
    try:
        async with guard.reserve(0.10):
            await asyncio.sleep(0)
    except SpendCeilingReached:  # pragma: no cover - 50 x 0.10 fits under 20
        pass


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
