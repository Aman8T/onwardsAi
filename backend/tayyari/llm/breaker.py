"""A small circuit breaker for the Jev API: stop calling a dependency that keeps failing, probe it later.

States (one breaker per process, so each replica judges Jev on its own):

    closed     calls go through; failures are counted in a sliding window
    open       too many failures: `allow()` is False until `open_for_s` has passed
    half_open  the cool-down is over: exactly one trial call is allowed; its result decides

Rules:
  - `failures` failures within `window_s` seconds open the circuit. A success clears the failure count,
    so only failures with nothing good in between add up.
  - In half-open the first `allow()` returns True and claims the trial; later calls get False until the
    trial reports. A trial success closes the circuit, a trial failure reopens it for another `open_for_s`.
  - A trial whose result is never reported (cancelled) is given up after `open_for_s`, and the next
    `allow()` may try again.

Time comes from the injected `Clock.monotonic()`, so tests drive every transition without sleeping.
Everything runs on one event loop thread; there is no locking.
"""

from __future__ import annotations

from collections import deque
from typing import Literal

from ..core.clock import Clock, SystemClock

BreakerState = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    def __init__(
        self,
        failures: int = 5,
        window_s: float = 30.0,
        open_for_s: float = 60.0,
        clock: Clock | None = None,
    ) -> None:
        if failures < 1:
            raise ValueError("failures must be at least 1")
        if window_s <= 0 or open_for_s <= 0:
            raise ValueError("window_s and open_for_s must be positive")
        self._threshold = failures
        self._window_s = window_s
        self._open_for_s = open_for_s
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._failures: deque[float] = deque()
        self._state: BreakerState = "closed"
        self._opened_at = 0.0
        self._trial_started_at: float | None = None

    # ── introspection ──

    @property
    def state(self) -> BreakerState:
        """The current state. An open circuit whose cool-down has passed already reads as ``half_open``."""
        self._advance()
        return self._state

    @property
    def recent_failures(self) -> int:
        """Failures currently inside the window (0 when open or half-open)."""
        self._prune(self._clock.monotonic())
        return len(self._failures)

    @property
    def retry_in_s(self) -> float:
        """Seconds until an open circuit lets a trial through (0 when closed or half-open)."""
        if self.state != "open":
            return 0.0
        return max(0.0, self._opened_at + self._open_for_s - self._clock.monotonic())

    # ── use ──

    def allow(self) -> bool:
        """May a call be made right now? In half-open this claims the single trial slot."""
        self._advance()
        if self._state == "closed":
            return True
        if self._state == "open":
            return False
        now = self._clock.monotonic()
        if self._trial_started_at is None or now - self._trial_started_at >= self._open_for_s:
            self._trial_started_at = now
            return True
        return False

    def record_success(self) -> None:
        """A call worked: close the circuit (from half-open) and forget earlier failures.

        A success that arrives while the circuit is open (a call started before it opened) is ignored.
        """
        self._advance()
        if self._state == "open":
            return
        self._failures.clear()
        self._state = "closed"
        self._trial_started_at = None

    def record_failure(self) -> None:
        """A call failed: count it, and open the circuit when the threshold is reached (or the trial failed)."""
        self._advance()
        now = self._clock.monotonic()
        if self._state == "half_open":
            self._open(now)
            return
        if self._state == "open":
            return  # a straggler from before the circuit opened; the cool-down is not extended
        self._failures.append(now)
        self._prune(now)
        if len(self._failures) >= self._threshold:
            self._open(now)

    def reset(self) -> None:
        """Back to closed with no failures (tests and manual recovery)."""
        self._failures.clear()
        self._state = "closed"
        self._trial_started_at = None

    # ── internals ──

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_s
        while self._failures and self._failures[0] <= cutoff:
            self._failures.popleft()

    def _open(self, now: float) -> None:
        self._state = "open"
        self._opened_at = now
        self._trial_started_at = None
        self._failures.clear()

    def _advance(self) -> None:
        if self._state == "open" and self._clock.monotonic() - self._opened_at >= self._open_for_s:
            self._state = "half_open"
            self._trial_started_at = None


__all__ = ["BreakerState", "CircuitBreaker"]
