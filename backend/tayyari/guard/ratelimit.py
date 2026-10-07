"""Per-client rate limits for the two expensive routes, shared across replicas through the database.

How a request is judged (`RateLimiter.check`):

1. A per-process **token bucket** per (route, client) answers first. It is sized from the shortest window
   (twice the limit as burst, refilled at the limit's rate), so it never rejects a client the database rules
   would accept, but it sheds hammering without a database round trip. It is also the only protection left
   while the database is unreachable.
2. Then one atomic counter per fixed window (minute, hour, day) in `LimitRepo.hit`, shortest window first,
   stopping at the first one exceeded, so a rejected burst does not eat the longer budgets. The bucket key
   carries the window (``analyze:min:<ip_hash>``): two window sizes starting at the same instant must not
   share a counter.
3. For ``analyze`` only, a global hourly counter caps the whole service (the cost backstop for a distributed
   crowd that no single address gives away).

`shed(route, ip_hash)` runs step 1 alone, synchronously, so a route can turn hammering away before it reads the
request body; it then calls ``check(..., memory=False)`` after validating, so one request takes one token.

A limit of 0 means "this window is not enforced"; ``GuardSettings.enabled = False`` switches the limiter off.
When the repository errors or is slow, the limiter fails open to the in-memory bucket, logs it, and stops
asking the database for a short while so a database outage does not add a timeout to every request.
Rejections are `core.errors.RateLimited` (HTTP 429 with ``Retry-After`` = seconds until the window ends)
with texts written for the person who hit the limit.
"""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from tayyari.core.clock import Clock
from tayyari.core.errors import RateLimited
from tayyari.core.logging import get_logger
from tayyari.core.settings import GuardSettings
from tayyari.storage.ports import LimitRepo

log = get_logger(__name__)

Route = Literal["analyze", "chat"]
WindowName = Literal["min", "hour", "day"]

UNKNOWN_SUBJECT = "unknown"
"""Clients whose address could not be determined share one bucket (the strict choice)."""
GLOBAL_ANALYZE_BUCKET = "analyze:global:hour"

_BURST_FACTOR = 2.0
_MAX_BUCKETS = 10_000


@dataclass(frozen=True, slots=True)
class _Window:
    name: WindowName
    seconds: int
    limit: int


_WINDOW_SECONDS: dict[WindowName, int] = {"min": 60, "hour": 3600, "day": 86_400}


class InProcessBucket:
    """Token buckets in this process's memory, one per key, least recently used keys evicted first."""

    def __init__(self, clock: Clock, *, max_keys: int = _MAX_BUCKETS) -> None:
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        """key -> (tokens, monotonic time of the last update)."""

    def __len__(self) -> int:
        return len(self._buckets)

    def take(self, key: str, *, capacity: float, refill_per_s: float) -> float:
        """Take one token: 0.0 when granted, otherwise the seconds until a token will be available."""
        now = self._clock.monotonic()
        tokens, last = self._buckets.get(key, (capacity, now))
        tokens = min(capacity, tokens + max(0.0, now - last) * refill_per_s)
        granted = tokens >= 1.0
        if granted:
            tokens -= 1.0
        self._buckets[key] = (tokens, now)
        self._buckets.move_to_end(key)
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
        if granted:
            return 0.0
        return (1.0 - tokens) / refill_per_s if refill_per_s > 0 else math.inf


class _RepoUnavailable(Exception):
    """A counter could not be read (error or timeout); internal to `RateLimiter.check`."""


def wait_phrase(seconds: float) -> str:
    """ "12 seconds", "about 5 minutes", "about an hour": how long to wait, for a person to read."""
    secs = max(1, math.ceil(seconds))
    if secs < 60:
        return f"{secs} second{'s' if secs != 1 else ''}"
    if secs < 3600:
        minutes = math.ceil(secs / 60)
        return "about a minute" if minutes == 1 else f"about {minutes} minutes"
    hours = math.ceil(secs / 3600)
    return "about an hour" if hours == 1 else f"about {hours} hours"


_MESSAGES: dict[tuple[str, str], str] = {
    ("analyze", "min"): "That's a lot of analyses in a row. Please wait {wait} and try again.",
    ("analyze", "hour"): "You've reached the hourly limit for analyses. Please try again in {wait}.",
    ("analyze", "day"): "You've reached today's limit for analyses. It resets in {wait}.",
    ("analyze", "global"): "Tayyari is very busy right now. Please try again in {wait}.",
    ("chat", "min"): "You're sending messages quickly. Please wait {wait} and try again.",
    ("chat", "hour"): "You've reached the hourly limit for chat messages. Please try again in {wait}.",
    ("chat", "day"): "You've reached today's limit for chat messages. It resets in {wait}.",
}


def limit_message(route: str, window: str, retry_after_s: float) -> str:
    """The friendly sentence for a rejection (``window`` is ``min``, ``hour``, ``day`` or ``global``)."""
    template = _MESSAGES.get(
        (route, window), "Too many requests right now. Please wait {wait} and try again."
    )
    return template.format(wait=wait_phrase(retry_after_s))


def seconds_to_window_end(now: datetime, window_s: int) -> float:
    """Seconds until the fixed (epoch-aligned) window containing ``now`` ends, at least 1."""
    stamp = now.timestamp()
    return max(1.0, window_s - (stamp % window_s))


class RateLimiter:
    """See the module docstring. One instance per process, shared by the analyze and chat routes."""

    def __init__(
        self,
        repo: LimitRepo,
        settings: GuardSettings,
        clock: Clock,
        *,
        db_timeout_s: float = 3.0,
        db_backoff_s: float = 15.0,
        max_buckets: int = _MAX_BUCKETS,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._clock = clock
        self._db_timeout_s = db_timeout_s
        self._db_backoff_s = db_backoff_s
        self._bucket = InProcessBucket(clock, max_keys=max_buckets)
        self._db_down_until: float | None = None

    def _windows(self, route: Route) -> list[_Window]:
        s = self._settings
        limits: tuple[tuple[WindowName, int], ...]
        if route == "analyze":
            limits = (("min", s.analyze_per_minute), ("hour", s.analyze_per_hour), ("day", s.analyze_per_day))
        else:
            limits = (("min", s.chat_per_minute), ("hour", s.chat_per_hour), ("day", s.chat_per_day))
        return [_Window(name, _WINDOW_SECONDS[name], limit) for name, limit in limits if limit > 0]

    def _limited(
        self, route: str, window: str, retry_after_s: float, *, count: int | None, limit: int | None
    ) -> RateLimited:
        return RateLimited(
            limit_message(route, window, retry_after_s),
            retry_after_s=retry_after_s,
            detail=f"{route}:{window} count={count} limit={limit}",
        )

    def _take_token(self, route: Route, ip_hash: str | None, windows: list[_Window]) -> None:
        """Take a token from this client's in-memory bucket, or raise `RateLimited`."""
        if not windows:
            return
        shortest = windows[0]
        wait = self._bucket.take(
            f"{route}:{ip_hash or UNKNOWN_SUBJECT}",
            capacity=max(1.0, _BURST_FACTOR * shortest.limit),
            refill_per_s=shortest.limit / shortest.seconds,
        )
        if wait > 0:
            retry = min(wait, float(shortest.seconds))
            log.info("rate_limited", route=route, window=shortest.name, layer="memory", ip_hash=ip_hash)
            raise self._limited(route, shortest.name, retry, count=None, limit=shortest.limit)

    def shed(self, route: Route, ip_hash: str | None) -> None:
        """Only the in-memory bucket, no database: cheap enough to run before the request body is read.

        Raises `RateLimited` when this client is hammering. A route that calls this first and `check` later
        (after validation) passes ``memory=False`` to `check` so one request takes one token, not two.
        """
        if self._settings.enabled:
            self._take_token(route, ip_hash, self._windows(route))

    async def check(self, route: Route, ip_hash: str | None, *, memory: bool = True) -> None:
        """Count this request against every rule, or raise `RateLimited`. Never raises anything else.

        ``memory=False`` skips the in-memory bucket (see `shed`) and applies only the shared counters.
        """
        settings = self._settings
        if not settings.enabled:
            return
        subject = ip_hash or UNKNOWN_SUBJECT
        windows = self._windows(route)

        if memory:
            self._take_token(route, ip_hash, windows)

        if self._db_down_until is not None:
            if self._clock.monotonic() < self._db_down_until:
                return
            self._db_down_until = None

        now = self._clock.now()
        try:
            for window in windows:
                count = await self._hit(f"{route}:{window.name}:{subject}", window.seconds, now)
                if count > window.limit:
                    retry = seconds_to_window_end(now, window.seconds)
                    log.info(
                        "rate_limited",
                        route=route,
                        window=window.name,
                        count=count,
                        limit=window.limit,
                        ip_hash=ip_hash,
                    )
                    raise self._limited(route, window.name, retry, count=count, limit=window.limit)
            if route == "analyze" and settings.analyze_global_per_hour > 0:
                count = await self._hit(GLOBAL_ANALYZE_BUCKET, 3600, now)
                if count > settings.analyze_global_per_hour:
                    retry = seconds_to_window_end(now, 3600)
                    log.warning(
                        "rate_limited_global",
                        route=route,
                        count=count,
                        limit=settings.analyze_global_per_hour,
                    )
                    raise self._limited(
                        route, "global", retry, count=count, limit=settings.analyze_global_per_hour
                    )
        except _RepoUnavailable:
            return

    async def _hit(self, bucket: str, window_s: int, now: datetime) -> int:
        try:
            async with asyncio.timeout(self._db_timeout_s):
                return await self._repo.hit(bucket, window_s, now)
        except Exception as exc:  # the database is down or slow: fail open to the in-memory bucket
            self._db_down_until = self._clock.monotonic() + self._db_backoff_s
            log.warning(
                "rate_limit_repo_unavailable",
                bucket=bucket.rsplit(":", 1)[0],
                error=type(exc).__name__,
                failing_open=True,
            )
            raise _RepoUnavailable from exc


__all__ = [
    "GLOBAL_ANALYZE_BUCKET",
    "UNKNOWN_SUBJECT",
    "InProcessBucket",
    "RateLimiter",
    "Route",
    "limit_message",
    "seconds_to_window_end",
    "wait_phrase",
]
