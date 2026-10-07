"""Circuit breaker transitions, driven by `FakeClock` (no sleeping), and the clock itself."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tayyari.core.clock import Clock
from tayyari.llm.breaker import CircuitBreaker
from tests.fakes.clock import FakeClock


def make(
    failures: int = 5, window_s: float = 30.0, open_for_s: float = 60.0
) -> tuple[CircuitBreaker, FakeClock]:
    clock = FakeClock()
    return CircuitBreaker(failures=failures, window_s=window_s, open_for_s=open_for_s, clock=clock), clock


def state_of(breaker: CircuitBreaker) -> str:
    """The state as a plain str (keeps mypy from narrowing the literal after earlier asserts)."""
    return str(breaker.state)


def trip(breaker: CircuitBreaker, clock: FakeClock, n: int = 5, gap_s: float = 1.0) -> None:
    for _ in range(n):
        breaker.record_failure()
        clock.advance(gap_s)


def test_defaults_match_the_design() -> None:
    breaker, clock = make()
    assert state_of(breaker) == "closed"
    assert breaker.allow()
    trip(breaker, clock, 4)
    assert state_of(breaker) == "closed"  # failures=5
    breaker.record_failure()
    assert state_of(breaker) == "open"
    clock.advance(59.0)
    assert state_of(breaker) == "open"  # open_for_s=60
    clock.advance(1.0)
    assert state_of(breaker) == "half_open"


def test_closed_circuit_allows_every_call() -> None:
    breaker, _ = make()
    assert all(breaker.allow() for _ in range(100))


def test_the_threshold_failure_opens_the_circuit() -> None:
    breaker, clock = make(failures=3)
    trip(breaker, clock, 2)
    assert state_of(breaker) == "closed" and breaker.allow()
    breaker.record_failure()
    assert state_of(breaker) == "open"
    assert not breaker.allow()


def test_failures_outside_the_window_do_not_count() -> None:
    breaker, clock = make(failures=3, window_s=30.0)
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(31.0)  # both are now stale
    assert breaker.recent_failures == 0
    breaker.record_failure()
    assert state_of(breaker) == "closed"
    assert breaker.recent_failures == 1


def test_a_failure_exactly_one_window_old_is_dropped() -> None:
    breaker, clock = make(failures=2, window_s=10.0)
    breaker.record_failure()
    clock.advance(10.0)
    breaker.record_failure()
    assert state_of(breaker) == "closed"


def test_failures_inside_the_window_add_up_across_time() -> None:
    breaker, clock = make(failures=3, window_s=30.0)
    breaker.record_failure()
    clock.advance(14.0)
    breaker.record_failure()
    clock.advance(14.0)  # the first is 28 s old: still inside
    breaker.record_failure()
    assert state_of(breaker) == "open"


def test_a_success_clears_the_failure_count() -> None:
    breaker, _ = make(failures=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    assert breaker.recent_failures == 0
    breaker.record_failure()
    breaker.record_failure()
    assert state_of(breaker) == "closed"  # only failures with nothing good in between add up
    breaker.record_failure()
    assert state_of(breaker) == "open"


def test_open_circuit_rejects_until_the_cool_down_ends() -> None:
    breaker, clock = make(failures=1, open_for_s=60.0)
    breaker.record_failure()
    assert not breaker.allow()
    assert breaker.retry_in_s == 60.0
    clock.advance(30.0)
    assert not breaker.allow()
    assert breaker.retry_in_s == 30.0
    clock.advance(29.9)
    assert not breaker.allow()


def test_half_open_lets_exactly_one_trial_through() -> None:
    breaker, clock = make(failures=1, open_for_s=60.0)
    breaker.record_failure()
    clock.advance(60.0)
    assert state_of(breaker) == "half_open"
    assert breaker.retry_in_s == 0.0
    assert breaker.allow() is True  # the trial
    assert breaker.allow() is False
    assert breaker.allow() is False
    assert state_of(breaker) == "half_open"


def test_reading_the_state_does_not_use_up_the_trial() -> None:
    breaker, clock = make(failures=1, open_for_s=10.0)
    breaker.record_failure()
    clock.advance(10.0)
    for _ in range(5):
        assert state_of(breaker) == "half_open"
    assert breaker.allow() is True


def test_a_successful_trial_closes_the_circuit() -> None:
    breaker, clock = make(failures=2, open_for_s=60.0)
    trip(breaker, clock, 2, gap_s=0.0)
    clock.advance(60.0)
    assert breaker.allow()
    breaker.record_success()
    assert state_of(breaker) == "closed"
    assert breaker.allow() and breaker.allow()
    assert breaker.recent_failures == 0
    breaker.record_failure()  # a fresh count: one failure does not reopen it
    assert state_of(breaker) == "closed"


def test_a_failed_trial_reopens_for_a_full_cool_down() -> None:
    breaker, clock = make(failures=1, open_for_s=60.0)
    breaker.record_failure()
    clock.advance(60.0)
    assert breaker.allow()
    clock.advance(5.0)
    breaker.record_failure()
    assert state_of(breaker) == "open"
    assert not breaker.allow()
    clock.advance(59.0)
    assert state_of(breaker) == "open"  # measured from the failed trial, not from the first opening
    clock.advance(1.0)
    assert state_of(breaker) == "half_open"
    assert breaker.allow()


def test_a_trial_that_never_reports_is_given_up_after_one_cool_down() -> None:
    breaker, clock = make(failures=1, open_for_s=60.0)
    breaker.record_failure()
    clock.advance(60.0)
    assert breaker.allow()  # claimed, then the caller was cancelled and never reported
    assert not breaker.allow()
    clock.advance(59.0)
    assert not breaker.allow()
    clock.advance(1.0)
    assert breaker.allow()  # a new trial


def test_stragglers_from_before_the_circuit_opened_are_ignored() -> None:
    breaker, clock = make(failures=2, open_for_s=60.0)
    trip(breaker, clock, 2, gap_s=0.0)
    clock.advance(10.0)
    breaker.record_failure()  # an in-flight call that failed late: must not extend the cool-down
    breaker.record_success()  # ... and a late success must not close the circuit
    assert state_of(breaker) == "open"
    clock.advance(50.0)
    assert state_of(breaker) == "half_open"


def test_reset_closes_the_circuit() -> None:
    breaker, _ = make(failures=1)
    breaker.record_failure()
    assert state_of(breaker) == "open"
    breaker.reset()
    assert state_of(breaker) == "closed" and breaker.allow()


def test_full_cycle_closed_open_half_open_closed() -> None:
    breaker, clock = make(failures=5, window_s=30.0, open_for_s=60.0)
    seen = [breaker.state]
    for _ in range(5):
        breaker.record_failure()
        clock.advance(2.0)
    seen.append(breaker.state)
    clock.advance(60.0)
    seen.append(breaker.state)
    assert breaker.allow()
    breaker.record_success()
    seen.append(breaker.state)
    assert seen == ["closed", "open", "half_open", "closed"]


@pytest.mark.parametrize(
    ("failures", "window_s", "open_for_s"), [(0, 30.0, 60.0), (5, 0.0, 60.0), (5, 30.0, -1.0)]
)
def test_invalid_settings_are_rejected(failures: int, window_s: float, open_for_s: float) -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(failures=failures, window_s=window_s, open_for_s=open_for_s, clock=FakeClock())


def test_it_works_without_an_injected_clock() -> None:
    breaker = CircuitBreaker(failures=1)
    assert breaker.allow()
    breaker.record_failure()
    assert not breaker.allow()


# ───────────────────────── FakeClock ─────────────────────────


def test_fake_clock_satisfies_the_clock_protocol() -> None:
    assert isinstance(FakeClock(), Clock)


def test_fake_clock_advances_wall_and_monotonic_time_together() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC), monotonic_start=10.0)
    clock.advance(90.5)
    assert clock.monotonic() == 100.5
    assert clock.now() == datetime(2026, 1, 1, 0, 1, 30, 500_000, tzinfo=UTC)
    with pytest.raises(ValueError):
        clock.advance(-1)


def test_fake_clock_needs_an_aware_start() -> None:
    with pytest.raises(ValueError):
        FakeClock(datetime(2026, 1, 1))


async def test_fake_clock_sleep_advances_time_instantly_and_records_it() -> None:
    clock = FakeClock()
    before, wall = clock.monotonic(), clock.now()
    await clock.sleep(2.5)
    await clock.sleep(0)
    await clock.sleep(-1)
    await clock.sleep(0.5)
    assert clock.monotonic() - before == 3.0
    assert (clock.now() - wall).total_seconds() == 3.0
    assert clock.sleeps == [2.5, 0.5]
    assert clock.total_slept == 3.0
