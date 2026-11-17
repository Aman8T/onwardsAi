"""`PlanUse`: taken once, kept or given back once, nothing for a visitor, and a release that fails never raises."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from tayyari.api.plan_use import PlanUse
from tayyari.core.errors import PaymentRequired


class FakePlans:
    def __init__(self, *, refuse: bool = False, release_fails: bool = False) -> None:
        self.used: list[tuple[str, str, str]] = []
        self.released: list[tuple[str, str, str]] = []
        self._refuse = refuse
        self._release_fails = release_fails

    async def use(self, user_id: str, feature: str, ref: str) -> None:
        if self._refuse:
            raise PaymentRequired("limit_reached", feature=feature, limit=1)
        self.used.append((user_id, feature, ref))

    async def release(self, user_id: str, feature: str, ref: str) -> bool:
        if self._release_fails:
            raise RuntimeError("database down")
        self.released.append((user_id, feature, ref))
        return True


class FakeContainer:
    def __init__(self, plans: FakePlans) -> None:
        self.plans = plans


def make(plans: FakePlans, user_id: str | None = "u1") -> PlanUse:
    return PlanUse(FakeContainer(plans), user_id, "searches", "ref1")  # type: ignore[arg-type]


async def events(*items: str, then: BaseException | None = None) -> AsyncIterator[str]:
    for item in items:
        yield item
    if then is not None:
        raise then


async def test_take_counts_once_and_give_back_releases_once() -> None:
    plans = FakePlans()
    use = make(plans)
    await use.take()
    await use.give_back()
    await use.give_back()
    assert plans.used == [("u1", "searches", "ref1")]
    assert plans.released == [("u1", "searches", "ref1")]


async def test_a_kept_use_is_not_given_back() -> None:
    plans = FakePlans()
    use = make(plans)
    await use.take()
    use.keep()
    await use.give_back()
    assert plans.released == []


async def test_without_an_account_nothing_is_taken_or_given_back() -> None:
    plans = FakePlans()
    use = make(plans, None)
    await use.take()
    await use.give_back()
    assert plans.used == [] and plans.released == []


async def test_a_refusal_propagates_and_leaves_nothing_to_give_back() -> None:
    plans = FakePlans(refuse=True)
    use = make(plans)
    with pytest.raises(PaymentRequired):
        await use.take()
    await use.give_back()
    assert plans.released == []


async def test_a_failing_release_never_raises() -> None:
    plans = FakePlans(release_fails=True)
    use = make(plans)
    await use.take()
    await use.give_back()  # logged, not raised


async def test_watch_keeps_the_use_at_the_first_produced_event() -> None:
    plans = FakePlans()
    use = make(plans)
    await use.take()
    seen = [e async for e in use.watch(events("step", "job", "done"), lambda e: e == "job")]
    assert seen == ["step", "job", "done"] and plans.released == []


async def test_watch_gives_back_when_nothing_was_produced() -> None:
    plans = FakePlans()
    use = make(plans)
    await use.take()
    seen = [e async for e in use.watch(events("step", "error"), lambda e: e == "job")]
    assert seen == ["step", "error"] and len(plans.released) == 1


async def test_watch_gives_back_when_the_stream_raises_or_is_closed_early() -> None:
    plans = FakePlans()
    use = make(plans)
    await use.take()
    with pytest.raises(RuntimeError):
        _ = [e async for e in use.watch(events("step", then=RuntimeError("x")), lambda e: e == "job")]
    assert len(plans.released) == 1

    early = FakePlans()
    other = make(early)
    await other.take()
    stream: Any = other.watch(events("step", "more"), lambda e: e == "job")
    assert await anext(stream) == "step"
    await stream.aclose()
    assert len(early.released) == 1
