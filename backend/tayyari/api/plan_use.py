"""One count of a Just Looking feature (roadmap B2, "Where each count is taken"): taken once before a run's spend is
reserved, kept once the run produced something, given back when it did not.

    use = PlanUse(container, user_id, "searches", ref)   ``user_id`` None: no account, or one on the unlimited list
    await use.take()        `PlanService.use`: 402 ``limit_reached`` when the month's count has run out
    use.keep()              the run produced what the person came for; the use stays counted
    await use.give_back()   `PlanService.release`, once, under `asyncio.shield`; never raises

`PlanUse.watch` wraps a stream: the use is kept when ``produced(event)`` is true for one of its events, and given back
when the stream ends (or is cancelled) without one. As with `matching.Charge`, a route also pushes ``give_back`` on its
exit stack, in case the stream never got to run.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from tayyari.billing.plans import CountedFeature
from tayyari.container import Container
from tayyari.core.logging import get_logger

log = get_logger(__name__)

_releases: set[asyncio.Task[None]] = set()
"""Releases in flight, held so a task nobody awaits any more is not collected before it finishes."""


class PlanUse:
    def __init__(self, container: Container, user_id: str | None, feature: CountedFeature, ref: str) -> None:
        self._container = container
        self._user_id = user_id
        self._feature: CountedFeature = feature
        self._ref = ref
        self._taken = False
        self._settled = False

    async def take(self) -> None:
        """Count the use, or raise `PaymentRequired`. Does nothing without an account."""
        if self._user_id is None:
            return
        await self._container.plans.use(self._user_id, self._feature, self._ref)
        self._taken = True

    def keep(self) -> None:
        self._settled = True

    async def give_back(self) -> None:
        """Give the use back unless it was kept or never taken. Once; safe from a cancelled task; never raises."""
        if self._settled or not self._taken or self._user_id is None:
            return
        self._settled = True
        task = asyncio.ensure_future(self._release(self._user_id))
        _releases.add(task)
        task.add_done_callback(_releases.discard)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass  # the release carries on by itself

    async def _release(self, user_id: str) -> None:
        try:
            await self._container.plans.release(user_id, self._feature, self._ref)
        except Exception:
            log.error("plan_release_failed", feature=self._feature, exc_info=True)

    async def watch[T](self, events: AsyncIterator[T], produced: Callable[[T], bool]) -> AsyncIterator[T]:
        """``events`` as they come; the use is kept at the first event for which ``produced`` is true, and given back
        when the stream ends first (finished, failed, or cancelled)."""
        try:
            async for event in events:
                if produced(event):
                    self.keep()
                yield event
        finally:
            await self.give_back()


__all__ = ["PlanUse"]
