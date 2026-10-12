"""Payments storage, run against every backend (the fakes, SQLite file and memory, and Postgres when configured), so
the fake cannot drift from the SQL repository: the order lifecycle, the ledger arithmetic, the order in which a use is
paid for, the month boundary, idempotency, give-backs and what two requests for one balance can never do."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tayyari.billing.entitlements import month_start
from tayyari.storage.ports import StoredOrder
from tayyari.storage.tables import CreditLedgerRow, EntitlementRow, OrderRow

if TYPE_CHECKING:
    from tests.unit.storage.conftest import Env

DAY = timedelta(days=1)
EMAIL = "ana@example.com"


async def account(env: Env, email: str = EMAIL) -> str:
    return (await env.repos.users.sign_in_email(email=email, now=env.clock.now())).id


def order(
    user_id: str | None, *, provider_order_id: str = "order_1", order_id: str = "o1", **fields: object
) -> StoredOrder:
    values: dict[str, object] = {
        "id": order_id,
        "user_id": user_id,
        "provider": "razorpay",
        "provider_order_id": provider_order_id,
        "product": "pack3",
        "currency": "INR",
        "amount_minor": 49_900,
        "status": "created",
        "created_at": datetime(2026, 10, 7, 12, tzinfo=UTC),
    }
    values.update(fields)
    return StoredOrder(**values)  # type: ignore[arg-type]


async def balance(env: Env, user_id: str) -> tuple[int, datetime | None, int]:
    """``(credits, pass_until, free postings used this month)``."""
    now = env.clock.now()
    got = await env.repos.billing.entitlements(user_id, month_start=month_start(now))
    return got.credits, got.pass_until, got.free_used


async def ledger_sum(env: Env, user_id: str) -> int:
    if env.fakes is not None:
        return env.fakes.billing.ledger_sum(user_id)
    assert env.db is not None
    async with env.db.session() as s:
        total = (
            await s.execute(
                select(func.coalesce(func.sum(CreditLedgerRow.delta), 0)).where(
                    CreditLedgerRow.user_id == user_id
                )
            )
        ).scalar_one()
    return int(total)


async def consume(env: Env, user_id: str, ref: str, *, allowance: int = 1):  # type: ignore[no-untyped-def]
    now = env.clock.now()
    return await env.repos.billing.consume(
        user_id, ref, now=now, month_start=month_start(now), free_allowance=allowance
    )


async def buy(env: Env, user_id: str, *, credits: int = 0, pass_days: int = 0, ref: str = "o1") -> bool:
    return await env.repos.billing.grant(
        user_id, credits=credits, pass_days=pass_days, ref=ref, now=env.clock.now()
    )


# ───────────────────────── balances ─────────────────────────


async def test_a_new_account_has_nothing(env: Env) -> None:
    user = await account(env)
    assert await balance(env, user) == (0, None, 0)
    assert await balance(env, "nobody") == (0, None, 0)


async def test_a_pack_adds_credits_once_per_order(env: Env) -> None:
    user = await account(env)
    assert await buy(env, user, credits=3) is True
    assert await buy(env, user, credits=3) is False  # the same order again
    assert await balance(env, user) == (3, None, 0)
    assert await buy(env, user, credits=3, ref="o2") is True
    assert (await balance(env, user))[0] == 6
    assert await ledger_sum(env, user) == 6


async def test_a_pass_runs_thirty_days_and_a_renewal_extends_it(env: Env) -> None:
    user = await account(env)
    start = env.clock.now()
    await buy(env, user, pass_days=30, ref="p1")
    assert (await balance(env, user))[1] == start + 30 * DAY

    env.clock.advance(10 * DAY)
    await buy(env, user, pass_days=30, ref="p2")  # bought while the first still runs: starts where it ends
    assert (await balance(env, user))[1] == start + 60 * DAY

    env.clock.advance(100 * DAY)
    renewed = env.clock.now()
    await buy(env, user, pass_days=30, ref="p3")  # bought after it ran out: starts now
    assert (await balance(env, user))[1] == renewed + 30 * DAY
    assert await ledger_sum(env, user) == 0  # a pass moves no credits


# ───────────────────────── paying for a use ─────────────────────────


async def test_a_use_is_paid_by_the_pass_first_then_the_free_posting_then_a_credit_then_nothing(
    env: Env,
) -> None:
    user = await account(env)
    await buy(env, user, credits=2, pass_days=30)

    # the pass pays, and nothing else is touched
    for n in range(3):
        used = await consume(env, user, f"m{n}")
        assert used is not None and used.source == "pass" and used.credits == 2
    assert await balance(env, user) == (2, env.clock.now() + 30 * DAY, 0)

    env.clock.advance(
        31 * DAY
    )  # the pass has ended (into the next month: the free posting is available again)
    order_of_use = [(await consume(env, user, f"n{n}")) for n in range(4)]
    assert [None if u is None else u.source for u in order_of_use] == [
        "free_monthly",
        "credit",
        "credit",
        None,
    ]
    assert [None if u is None else u.credits for u in order_of_use] == [2, 1, 0, None]
    assert await balance(env, user) == (0, env.clock.now() - DAY, 1)
    assert await ledger_sum(env, user) == 0 == (await balance(env, user))[0]


async def test_the_free_posting_renews_on_the_first_of_the_month_utc(env: Env) -> None:
    user = await account(env)
    env.clock.set(datetime(2026, 10, 31, 23, 59, tzinfo=UTC))
    assert (await consume(env, user, "oct")) is not None
    assert (await consume(env, user, "oct-2")) is None  # October's one is used

    env.clock.set(datetime(2026, 11, 1, 0, 0, tzinfo=UTC))
    again = await consume(env, user, "nov")
    assert again is not None and again.source == "free_monthly"
    assert await consume(env, user, "nov-2") is None


async def test_the_allowance_is_a_number_not_a_flag(env: Env) -> None:
    user = await account(env)
    assert [(await consume(env, user, f"m{n}", allowance=2)) is not None for n in range(3)] == [
        True,
        True,
        False,
    ]
    assert await consume(env, user, "off", allowance=0) is None  # 0 turns the free allowance off


async def test_asking_again_for_a_paid_ref_takes_nothing_more(env: Env) -> None:
    user = await account(env)
    await buy(env, user, credits=2)
    await consume(env, user, "free-one")  # the free one
    first = await consume(env, user, "m1")
    again = await consume(env, user, "m1")
    assert first is not None and first.source == "credit" and not first.replayed
    assert again is not None and again.source == "credit" and again.replayed and again.credits == 1
    assert await balance(env, user) == (1, None, 1)
    assert await ledger_sum(env, user) == 1 == (await balance(env, user))[0]


async def test_a_use_that_is_given_back_is_unused_again(env: Env) -> None:
    user = await account(env)
    await buy(env, user, credits=1)

    free = await consume(env, user, "m1")
    assert free is not None and free.source == "free_monthly"
    assert await env.repos.billing.release(user, "m1", now=env.clock.now()) is True
    assert await balance(env, user) == (1, None, 0)  # this month's free posting is available again
    assert await env.repos.billing.release(user, "m1", now=env.clock.now()) is False  # only once

    paid = await consume(env, user, "m2")  # the same ref can be paid for afresh
    assert paid is not None and paid.source == "free_monthly" and not paid.replayed
    credit = await consume(env, user, "m3")
    assert credit is not None and credit.source == "credit"
    assert (await balance(env, user))[0] == 0
    assert await env.repos.billing.release(user, "m3", now=env.clock.now()) is True
    assert (await balance(env, user))[0] == 1
    assert await ledger_sum(env, user) == 1
    assert await env.repos.billing.release(user, "never-paid", now=env.clock.now()) is False


async def test_a_pass_use_given_back_changes_no_balance(env: Env) -> None:
    user = await account(env)
    await buy(env, user, pass_days=30)
    assert (await consume(env, user, "m1")) is not None
    assert await env.repos.billing.release(user, "m1", now=env.clock.now()) is True
    assert await balance(env, user) == (0, env.clock.now() + 30 * DAY, 0)


async def test_accounts_do_not_share_balances(env: Env) -> None:
    ana, raj = await account(env), await account(env, "raj@example.com")
    await buy(env, ana, credits=1)
    await consume(env, ana, "a1")
    await consume(env, ana, "a2")  # ana: free, then her credit
    assert (await consume(env, ana, "a3")) is None
    assert (await consume(env, raj, "r1")) is not None  # raj still has his free one
    assert (await balance(env, raj))[0] == 0


# ───────────────────────── two requests at once ─────────────────────────


async def test_concurrent_uses_never_spend_the_same_credit_twice(env: Env) -> None:
    user = await account(env)
    await buy(env, user, credits=2)
    results = await asyncio.gather(*(consume(env, user, f"m{n}", allowance=0) for n in range(6)))
    sources = sorted("none" if r is None else r.source for r in results)
    assert sources == ["credit", "credit", "none", "none", "none", "none"]
    assert await balance(env, user) == (0, None, 0)
    assert await ledger_sum(env, user) == 0


async def test_concurrent_requests_for_one_ref_pay_once(env: Env) -> None:
    user = await account(env)
    await buy(env, user, credits=3)
    results = await asyncio.gather(*(consume(env, user, "same", allowance=0) for _ in range(5)))
    assert all(r is not None and r.source == "credit" for r in results)
    assert sorted(bool(r.replayed) for r in results if r is not None) == [False, True, True, True, True]
    assert (await balance(env, user))[0] == 2


async def test_concurrent_free_postings_are_counted_once(env: Env) -> None:
    user = await account(env)
    results = await asyncio.gather(*(consume(env, user, f"m{n}") for n in range(4)))
    assert [r is not None for r in results].count(True) == 1
    assert (await balance(env, user))[2] == 1


async def test_a_grant_arriving_with_uses_loses_nothing(env: Env) -> None:
    user = await account(env)
    await buy(env, user, credits=1)
    work = [consume(env, user, f"m{n}", allowance=0) for n in range(3)] + [
        buy(env, user, credits=3, ref="o2")
    ]
    await asyncio.gather(*work)
    credits = (await balance(env, user))[0]
    assert credits == await ledger_sum(env, user)
    assert credits in (1, 2, 3)  # (1 + 3) minus the uses that found a credit, whichever order they ran in


# ───────────────────────── orders ─────────────────────────


async def test_an_order_is_stored_and_found_by_either_id(env: Env) -> None:
    user = await account(env)
    rec = order(user, raw={"provider_order_id": "order_1"})
    await env.repos.billing.add_order(rec)
    by_provider = await env.repos.billing.get_order_by_provider_id("order_1")
    assert by_provider is not None
    assert (by_provider.id, by_provider.user_id, by_provider.status, by_provider.amount_minor) == (
        "o1",
        user,
        "created",
        49_900,
    )
    assert by_provider.raw == {"provider_order_id": "order_1"} and by_provider.paid_at is None
    assert await env.repos.billing.get_order("o1") == by_provider
    assert await env.repos.billing.get_order("nope") is None
    assert await env.repos.billing.get_order_by_provider_id("nope") is None


async def test_a_provider_order_id_is_stored_once(env: Env) -> None:
    user = await account(env)
    await env.repos.billing.add_order(order(user))
    with pytest.raises((ValueError, IntegrityError)):  # a unique violation on SQL, a ValueError in the fake
        await env.repos.billing.add_order(order(user, order_id="o2"))


async def settle(env: Env, provider_order_id: str = "order_1", **fields: object):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {"credits": 3, "pass_days": 0, "raw": {"paid": True}}
    values.update(fields)
    return await env.repos.billing.settle_order(provider_order_id, now=env.clock.now(), **values)  # type: ignore[arg-type]


async def test_settling_pays_the_order_and_grants_exactly_once(env: Env) -> None:
    user = await account(env)
    await env.repos.billing.add_order(order(user))

    first = await settle(env, amount_minor=49_900, currency="INR")
    assert first.status == "granted" and first.order is not None
    assert (first.order.status, first.order.paid_at, first.order.raw) == (
        "paid",
        env.clock.now(),
        {"paid": True},
    )
    assert (await balance(env, user))[0] == 3

    for _ in range(3):  # a retried webhook, the browser hand-off, another retry
        again = await settle(env)
        assert again.status == "already_paid"
    assert (await balance(env, user))[0] == 3
    assert await ledger_sum(env, user) == 3


async def test_settling_a_pass_order_extends_the_pass(env: Env) -> None:
    user = await account(env)
    await env.repos.billing.add_order(order(user, product="pass30"))
    assert (await settle(env, credits=0, pass_days=30)).status == "granted"
    assert (await balance(env, user))[1] == env.clock.now() + 30 * DAY


async def test_two_settles_at_once_grant_once(env: Env) -> None:
    user = await account(env)
    await env.repos.billing.add_order(order(user))
    results = await asyncio.gather(*(settle(env) for _ in range(5)))
    assert sorted(r.status for r in results) == ["already_paid"] * 4 + ["granted"]
    assert (await balance(env, user))[0] == 3


async def test_an_unknown_order_grants_nothing(env: Env) -> None:
    user = await account(env)
    result = await settle(env, "order_unknown")
    assert (result.status, result.order) == ("unknown_order", None)
    assert await balance(env, user) == (0, None, 0)


@pytest.mark.parametrize("fields", [{"amount_minor": 100}, {"currency": "USD"}])
async def test_a_payment_that_is_not_what_was_asked_for_grants_nothing(
    env: Env, fields: dict[str, object]
) -> None:
    user = await account(env)
    await env.repos.billing.add_order(order(user))
    result = await settle(env, **fields)
    assert result.status == "mismatch"
    assert (await balance(env, user))[0] == 0
    stored = await env.repos.billing.get_order("o1")
    assert (
        stored is not None and stored.status == "created"
    )  # left as it was: the real payment can still settle
    assert (await settle(env, amount_minor=49_900, currency="INR")).status == "granted"


async def test_a_failed_attempt_can_still_be_paid(env: Env) -> None:
    user = await account(env)
    await env.repos.billing.add_order(order(user))
    assert await env.repos.billing.fail_order("order_1") is True
    assert (await env.repos.billing.get_order("o1")).status == "failed"  # type: ignore[union-attr]
    assert await env.repos.billing.fail_order("order_1") is False  # already failed
    assert (await settle(env)).status == "granted"  # the customer retried on the same order
    assert await env.repos.billing.fail_order("order_1") is False  # a paid order is never failed afterwards
    assert (await env.repos.billing.get_order("o1")).status == "paid"  # type: ignore[union-attr]
    assert await env.repos.billing.fail_order("order_unknown") is False


# ───────────────────────── real databases only ─────────────────────────


async def test_deleting_the_account_removes_its_balance_and_ledger_but_keeps_the_payment_record(
    env: Env,
) -> None:
    if env.db is None:
        pytest.skip("foreign keys exist only in a real database")
    user = await account(env)
    await env.repos.billing.add_order(order(user))
    await settle(env)
    await consume(env, user, "m1")

    assert await env.repos.users.delete(user) is True

    async with env.db.session() as s:
        assert (await s.execute(select(func.count()).select_from(EntitlementRow))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(CreditLedgerRow))).scalar_one() == 0
        kept = (await s.execute(select(OrderRow.user_id, OrderRow.status))).one()
    assert (kept.user_id, kept.status) == (None, "paid")  # the record outlives the account, unattached


async def test_a_payment_for_a_deleted_account_grants_nothing(env: Env) -> None:
    if env.db is None:
        pytest.skip("foreign keys exist only in a real database")
    user = await account(env)
    await env.repos.billing.add_order(order(user))
    await env.repos.users.delete(user)

    result = await settle(env)
    assert result.status == "orphaned"
    async with env.db.session() as s:
        assert (await s.execute(select(func.count()).select_from(EntitlementRow))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(CreditLedgerRow))).scalar_one() == 0
