"""`EntitlementService` hold, capture and release for auto-apply, over the fake repository: the lifetime free
applications first, a hold of nothing while no price is set, a 402 when a set price is not covered, idempotency on the
``apply:<application id>`` ref, a charge that is never given back and a give-back that is never charged, and that the
service is the `apply.seams.ApplyCredits` the apply engine is handed."""

from __future__ import annotations

import inspect

import pytest

from tayyari.apply.seams import ApplyCredits
from tayyari.billing.entitlements import (
    NO_APPLY_CREDITS_MESSAGE,
    NO_CREDITS,
    EntitlementService,
    apply_ref,
)
from tayyari.core.errors import PaymentRequired
from tayyari.core.settings import BillingSettings
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

Row = tuple[str, int, str | None]
"""``(reason, delta, ref)`` of one ledger row."""


class World:
    """The service over the fake repository, with the ledger to read what each call wrote."""

    def __init__(self, *, free: int = 10, cost: int | None = None) -> None:
        self.clock = FakeClock()
        self.fakes = FakeRepositories(self.clock)
        settings = BillingSettings(free_applications=free, apply_credit_cost=cost)
        self.service = EntitlementService(self.fakes.billing, settings, self.clock)

    def ledger(self, user_id: str = "ana") -> list[Row]:
        return [(r.reason, r.delta, r.ref) for r in self.fakes.billing.ledger_of(user_id)]

    async def hold(self, ref: str, user_id: str = "ana") -> Row:
        """Hold ``ref`` and return the hold row it stands on (the one this call wrote, or the earlier one)."""
        await self.service.hold(user_id, ref)
        rows = [r for r in self.ledger(user_id) if r[2] == ref and r[0] in ("apply_free", "apply_hold")]
        return rows[-1]

    async def credits(self, user_id: str = "ana") -> int:
        return (await self.service.summary(user_id)).credits

    async def buy_three(self, user_id: str = "ana") -> None:
        await self.service.grant(user_id, "pack3", f"order-{user_id}")


# ───────────────────────── the ref ─────────────────────────


def test_the_ref_of_a_hold_is_apply_and_the_application_id() -> None:
    assert apply_ref("a1b2") == "apply:a1b2"


# ───────────────────────── with the price unset (the default) ─────────────────────────


def test_the_defaults_are_ten_free_applications_and_no_price() -> None:
    settings = BillingSettings()
    assert settings.free_applications == 10
    assert settings.apply_credit_cost is None


async def test_the_first_ten_holds_are_free_then_the_eleventh_holds_nothing() -> None:
    world = World()
    free = [await world.hold(apply_ref(f"a{n}")) for n in range(10)]
    assert {reason for reason, _, _ in free} == {"apply_free"}

    eleventh = await world.hold(apply_ref("a10"))
    assert eleventh == ("apply_hold", 0, "apply:a10")  # a zero-delta hold: nothing is sold

    rows = world.ledger()
    assert [r[0] for r in rows] == ["apply_free"] * 10 + ["apply_hold"]
    assert all(delta == 0 for _, delta, _ in rows)
    assert await world.credits() == 0


async def test_with_no_price_an_account_with_no_credits_is_never_refused() -> None:
    world = World(free=0)
    for n in range(3):
        assert (await world.hold(apply_ref(f"a{n}")))[:2] == ("apply_hold", 0)
    assert await world.credits() == 0


async def test_a_hold_of_nothing_can_be_captured_and_released() -> None:
    world = World(free=0)
    await world.hold("apply:sent")
    await world.hold("apply:failed")
    assert await world.service.capture("ana", "apply:sent") is True
    assert await world.service.release("ana", "apply:failed") is True
    assert await world.fakes.billing.hold_state("ana", "apply:sent") == "charged"
    assert await world.fakes.billing.hold_state("ana", "apply:failed") == "released"


async def test_a_released_free_application_counts_as_unused_again() -> None:
    world = World(free=1)
    assert (await world.hold("apply:a1"))[0] == "apply_free"
    assert (await world.hold("apply:a2"))[0] == "apply_hold"
    assert await world.service.release("ana", "apply:a1") is True
    assert (await world.hold("apply:a3"))[0] == "apply_free"


# ───────────────────────── with a price set ─────────────────────────


async def test_with_a_price_and_no_credits_the_hold_is_a_402() -> None:
    world = World(free=0, cost=1)
    with pytest.raises(PaymentRequired) as refused:
        await world.service.hold("ana", apply_ref("a1"))
    assert refused.value.status == 402
    assert refused.value.reason == NO_CREDITS
    assert refused.value.to_body() == {"error": "payment_required", "reason": "no_credits"}
    assert refused.value.user_message == NO_APPLY_CREDITS_MESSAGE
    assert world.ledger() == []  # nothing was written


async def test_with_a_price_the_free_applications_still_come_first() -> None:
    world = World(free=2, cost=1)
    await world.buy_three()
    reasons = [(await world.hold(apply_ref(f"a{n}")))[:2] for n in range(5)]
    assert reasons == [("apply_free", 0)] * 2 + [("apply_hold", -1)] * 3
    assert await world.credits() == 0
    with pytest.raises(PaymentRequired):
        await world.service.hold("ana", apply_ref("a5"))


async def test_a_held_credit_comes_back_on_release_and_stays_spent_on_capture() -> None:
    world = World(free=0, cost=1)
    await world.buy_three()
    await world.hold("apply:sent")
    await world.hold("apply:failed")
    assert await world.credits() == 1

    assert await world.service.capture("ana", "apply:sent") is True
    assert await world.credits() == 1  # the capture takes nothing more
    assert await world.service.release("ana", "apply:failed") is True
    assert await world.credits() == 2  # the failed one's credit is back


async def test_a_price_larger_than_the_balance_is_refused() -> None:
    world = World(free=0, cost=4)
    await world.buy_three()
    with pytest.raises(PaymentRequired):
        await world.service.hold("ana", "apply:a1")
    assert await world.credits() == 3


# ───────────────────────── idempotency ─────────────────────────


@pytest.mark.parametrize("cost", [None, 1])
async def test_holding_the_same_ref_again_takes_nothing_more(cost: int | None) -> None:
    world = World(free=0, cost=cost)
    await world.buy_three()
    first = await world.hold("apply:a1")
    again = await world.hold("apply:a1")
    assert again == first == ("apply_hold", -(cost or 0), "apply:a1")
    assert [r for r in world.ledger() if r[2] == "apply:a1"] == [first]
    assert await world.credits() == 3 - (cost or 0)


@pytest.mark.parametrize("cost", [None, 1])
async def test_capture_then_release_returns_false(cost: int | None) -> None:
    world = World(free=0, cost=cost)
    await world.buy_three()
    await world.hold("apply:a1")
    assert await world.service.capture("ana", "apply:a1") is True
    assert await world.service.capture("ana", "apply:a1") is True  # repeating a capture is harmless
    assert await world.service.release("ana", "apply:a1") is False  # a charge is never given back
    assert await world.credits() == 3 - (cost or 0)
    assert [r[0] for r in world.ledger()].count("apply_capture") == 1


@pytest.mark.parametrize("cost", [None, 1])
async def test_release_then_capture_returns_false(cost: int | None) -> None:
    world = World(free=0, cost=cost)
    await world.buy_three()
    await world.hold("apply:a1")
    assert await world.service.release("ana", "apply:a1") is True
    assert await world.service.release("ana", "apply:a1") is False  # already given back
    assert await world.service.capture("ana", "apply:a1") is False  # a give-back is never charged
    assert await world.credits() == 3


async def test_with_nothing_held_capture_and_release_return_false() -> None:
    """An unlimited account skips the hold, so the engine's capture and release find nothing."""
    world = World()
    assert await world.service.capture("ana", "apply:never-held") is False
    assert await world.service.release("ana", "apply:never-held") is False
    assert world.ledger() == []


async def test_a_released_application_can_be_held_again_when_reopened() -> None:
    world = World(free=0, cost=1)
    await world.buy_three()
    await world.hold("apply:a1")
    await world.service.release("ana", "apply:a1")
    await world.service.hold("ana", "apply:a1")
    assert await world.credits() == 2
    assert await world.fakes.billing.hold_state("ana", "apply:a1") == "held"


async def test_holds_are_per_account() -> None:
    world = World(free=1)
    assert (await world.hold("apply:a1", "ana"))[0] == "apply_free"
    assert (await world.hold("apply:b1", "ben"))[0] == "apply_free"
    assert await world.service.capture("ben", "apply:a1") is False


# ───────────────────────── the seam apply is handed ─────────────────────────


def test_the_service_satisfies_the_apply_credits_protocol() -> None:
    world = World()
    credits_seam: ApplyCredits = world.service  # mypy checks this assignment structurally
    for name in ("hold", "capture", "release"):
        wanted = inspect.signature(getattr(ApplyCredits, name))
        have = inspect.signature(getattr(type(credits_seam), name))
        assert list(have.parameters) == list(wanted.parameters), name
        assert have.return_annotation == wanted.return_annotation, name
        assert inspect.iscoroutinefunction(getattr(world.service, name)), name


@pytest.mark.parametrize("ref", ["a1", "match:a1", "apply:", ""])
async def test_a_ref_that_is_not_an_application_ref_is_refused(ref: str) -> None:
    world = World()
    with pytest.raises(ValueError):
        await world.service.hold("ana", ref)
    with pytest.raises(ValueError):
        await world.service.capture("ana", ref)
    with pytest.raises(ValueError):
        await world.service.release("ana", ref)
