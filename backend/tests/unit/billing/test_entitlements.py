"""`EntitlementService`: the order a use is paid for in (pass, then the free posting of the month, then a credit, then a
402), the month boundary, idempotency, grants, give-backs and the ledger arithmetic, over the fake repository."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from tayyari.billing.entitlements import ZERO, EntitlementService, month_start
from tayyari.core.errors import PaymentRequired
from tayyari.core.settings import BillingSettings
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

DAY = 86_400


def build(
    *, start: datetime | None = None, free: int = 1
) -> tuple[EntitlementService, FakeRepositories, FakeClock]:
    clock = FakeClock(start)
    fakes = FakeRepositories(clock)
    return (
        EntitlementService(fakes.billing, BillingSettings(free_matches_per_month=free), clock),
        fakes,
        clock,
    )


# ───────────────────────── the order of use ─────────────────────────


async def test_a_use_is_paid_by_the_pass_then_the_free_posting_then_a_credit_then_a_402() -> None:
    service, _, clock = build()
    await service.grant("ana", "pack3", "order-1")
    await service.grant("ana", "pass30", "order-2")

    # 1. the pass pays for everything while it runs
    for n in range(5):
        used = await service.consume("ana", f"pass-{n}")
        assert used.source == "pass" and used.credits == 3
    assert (await service.summary("ana")).credits == 3

    # 2. the pass ends; the month's free posting pays next (still October: it was not used)
    clock.advance(30 * DAY)
    first = await service.consume("ana", "after-pass-1")
    assert first.source == "free_monthly" and first.credits == 3

    # 3. then the credits, one each
    paid = [await service.consume("ana", f"credit-{n}") for n in range(3)]
    assert [(u.source, u.credits) for u in paid] == [("credit", 2), ("credit", 1), ("credit", 0)]

    # 4. then nothing
    with pytest.raises(PaymentRequired) as refused:
        await service.consume("ana", "one-too-many")
    assert refused.value.status == 402 and refused.value.reason == "no_credits"
    assert refused.value.to_body() == {"error": "payment_required", "reason": "no_credits"}
    assert (await service.summary("ana")).credits == 0


async def test_a_new_account_gets_one_free_posting_then_a_402() -> None:
    service, _, _ = build()
    assert (await service.summary("ana")).free_match_available is True
    used = await service.consume("ana", "m1")
    assert (used.source, used.credits, used.pass_until, used.replayed) == ("free_monthly", 0, None, False)
    assert (await service.summary("ana")).free_match_available is False
    with pytest.raises(PaymentRequired):
        await service.consume("ana", "m2")


async def test_the_free_allowance_comes_from_the_settings() -> None:
    twice, _, _ = build(free=2)
    assert [(await twice.consume("ana", f"m{n}")).source for n in range(2)] == ["free_monthly"] * 2
    with pytest.raises(PaymentRequired):
        await twice.consume("ana", "m3")
    none, _, _ = build(free=0)
    assert (await none.summary("ana")).free_match_available is False
    with pytest.raises(PaymentRequired):
        await none.consume("ana", "m1")


async def test_the_free_posting_renews_when_the_utc_month_turns() -> None:
    service, _, clock = build(start=datetime(2026, 10, 31, 23, 59, 30, tzinfo=UTC))
    await service.consume("ana", "october")
    with pytest.raises(PaymentRequired):
        await service.consume("ana", "october-again")
    assert (await service.summary("ana")).free_match_available is False

    clock.advance(30)  # 00:00:00 on 1 November, UTC
    assert (await service.summary("ana")).free_match_available is True
    assert (await service.consume("ana", "november")).source == "free_monthly"
    with pytest.raises(PaymentRequired):
        await service.consume("ana", "november-again")


@pytest.mark.parametrize(
    ("moment", "start"),
    [
        (datetime(2026, 10, 11, 9, 30, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)),
        (datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC)),
        (datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
        (datetime(2026, 2, 28, 12, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)),
    ],
)
def test_the_month_starts_at_midnight_utc_on_the_first(moment: datetime, start: datetime) -> None:
    assert month_start(moment) == start


async def test_a_use_asked_for_twice_is_paid_once() -> None:
    service, fakes, _ = build()
    await service.grant("ana", "pack3", "order-1")
    await service.consume("ana", "free-one")
    first = await service.consume("ana", "m1")
    again = await service.consume("ana", "m1")
    assert (first.source, first.replayed, first.credits) == ("credit", False, 2)
    assert (again.source, again.replayed, again.credits) == ("credit", True, 2)
    assert (await service.summary("ana")).credits == 2
    assert fakes.billing.ledger_sum("ana") == 2


# ───────────────────────── grants ─────────────────────────


async def test_a_pack_adds_three_credits_and_is_granted_once_per_order() -> None:
    service, fakes, _ = build()
    assert await service.grant("ana", "pack3", "order-1") is True
    assert await service.grant("ana", "pack3", "order-1") is False
    assert await service.grant("ana", "pack3", "order-2") is True
    assert (await service.summary("ana")).credits == 6
    assert fakes.billing.ledger_sum("ana") == 6


async def test_a_pass_lasts_thirty_days_and_a_renewal_adds_thirty_more() -> None:
    service, _, clock = build()
    start = clock.now()
    await service.grant("ana", "pass30", "order-1")
    summary = await service.summary("ana")
    assert summary.pass_until == start + timedelta(days=30) and summary.credits == 0

    clock.advance(20 * DAY)
    await service.grant("ana", "pass30", "order-2")  # bought early: the 30 days start where the first ends
    assert (await service.summary("ana")).pass_until == start + timedelta(days=60)


async def test_a_pass_stops_counting_the_moment_it_ends() -> None:
    service, _, clock = build()
    await service.grant("ana", "pass30", "order-1")
    clock.advance(30 * DAY - 1)
    assert (await service.summary("ana")).pass_until is not None
    assert (await service.consume("ana", "last-second")).source == "pass"
    clock.advance(1)
    assert (await service.summary("ana")).pass_until is None
    assert (await service.consume("ana", "after")).source == "free_monthly"


async def test_one_account_buying_does_not_help_another() -> None:
    service, _, _ = build()
    await service.grant("ana", "pack3", "order-1")
    assert (
        (await service.summary("raj")) == (await service.summary("nobody")) != (await service.summary("ana"))
    )


# ───────────────────────── give-backs ─────────────────────────


async def test_a_posting_that_could_not_be_read_is_given_back() -> None:
    service, _, _ = build()
    await service.grant("ana", "pack3", "order-1")
    await service.consume("ana", "free-one")

    assert (await service.consume("ana", "m1")).source == "credit"
    assert (await service.summary("ana")).credits == 2
    assert await service.refund("ana", "m1") is True
    assert (await service.summary("ana")).credits == 3
    assert await service.refund("ana", "m1") is False  # once only
    assert await service.refund("ana", "never") is False

    assert await service.refund("ana", "free-one") is True  # the free posting too
    assert (await service.summary("ana")).free_match_available is True


# ───────────────────────── the ledger ─────────────────────────


def test_an_anonymous_visitor_has_nothing() -> None:
    assert (ZERO.credits, ZERO.pass_until, ZERO.free_match_available) == (0, None, False)


async def test_whatever_happens_the_balance_is_the_sum_of_the_ledger_and_never_negative() -> None:
    rng = random.Random(20261011)
    service, fakes, clock = build(start=datetime(2026, 10, 25, tzinfo=UTC), free=2)
    uses: list[str] = []
    for step in range(400):
        user = rng.choice(["ana", "raj"])
        action = rng.choice(["consume", "consume", "consume", "buy_pack", "buy_pass", "refund", "wait"])
        if action == "consume":
            ref = f"m{step}"
            try:
                await service.consume(user, ref)
                uses.append(ref)
            except PaymentRequired:
                pass
        elif action == "buy_pack":
            await service.grant(user, "pack3", f"order-{step}")
        elif action == "buy_pass" and rng.random() < 0.15:
            await service.grant(user, "pass30", f"order-{step}")
        elif action == "refund" and uses:
            await service.refund(user, rng.choice(uses))
        elif action == "wait":
            clock.advance(rng.choice([60, DAY, 5 * DAY]))
        for who in ("ana", "raj"):
            summary = await service.summary(who)
            assert summary.credits >= 0
            assert summary.credits == fakes.billing.ledger_sum(who)
