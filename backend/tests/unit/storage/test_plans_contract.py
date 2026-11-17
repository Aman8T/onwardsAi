"""The plans' storage (roadmap B2), run against every backend (the fakes, SQLite file and memory, and Postgres when
configured) so the fake cannot drift from the SQL repository: counted uses with and without a limit, idempotency on
``ref``, give-backs, the month boundary, the "tell me when it opens" wishes, paid orders, and the free applications
counted in the balance."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from tayyari.billing.plans import month_start
from tayyari.storage.ports import StoredOrder

if TYPE_CHECKING:
    from tests.unit.storage.conftest import Env

DAY = timedelta(days=1)


async def account(env: Env, email: str = "ana@example.com") -> str:
    return (await env.repos.users.sign_in_email(email=email, now=env.clock.now())).id


async def use(env: Env, user_id: str, ref: str, *, feature: str = "searches", limit: int | None = 2):  # type: ignore[no-untyped-def]
    now = env.clock.now()
    return await env.repos.billing.use_feature(
        user_id, feature, ref, now=now, since=month_start(now), limit=limit
    )


async def counts(env: Env, user_id: str) -> dict[str, int]:
    return await env.repos.billing.feature_counts(user_id, since=month_start(env.clock.now()))


async def test_uses_are_counted_up_to_the_limit_and_no_further(env: Env) -> None:
    user = await account(env)
    first, second, third = [await use(env, user, ref) for ref in ("r1", "r2", "r3")]
    assert first is not None and (first.used, first.replayed) == (1, False)
    assert second is not None and second.used == 2
    assert third is None
    assert await counts(env, user) == {"searches": 2}


async def test_a_ref_already_counted_is_replayed_and_takes_nothing(env: Env) -> None:
    user = await account(env)
    await use(env, user, "r1")
    await use(env, user, "r2")
    again = await use(env, user, "r1")  # the limit is reached, but this use was already counted
    assert again is not None and again.replayed and again.used == 2
    assert await counts(env, user) == {"searches": 2}


async def test_no_limit_records_every_use(env: Env) -> None:
    user = await account(env)
    for n in range(5):
        got = await use(env, user, f"r{n}", limit=None)
        assert got is not None and got.used == n + 1
    assert await counts(env, user) == {"searches": 5}


async def test_features_and_accounts_are_counted_apart(env: Env) -> None:
    ana, raj = await account(env), await account(env, "raj@example.com")
    await use(env, ana, "same", feature="searches")
    await use(env, ana, "same", feature="coach")
    await use(env, raj, "same", feature="searches")
    assert await counts(env, ana) == {"searches": 1, "coach": 1}
    assert await counts(env, raj) == {"searches": 1}


async def test_a_use_given_back_frees_its_place(env: Env) -> None:
    user = await account(env)
    await use(env, user, "r1")
    await use(env, user, "r2")
    now = env.clock.now()
    assert await env.repos.billing.release_feature(user, "searches", "r1", now=now) is True
    assert await env.repos.billing.release_feature(user, "searches", "r1", now=now) is False
    assert await env.repos.billing.release_feature(user, "coach", "r2", now=now) is False  # another feature
    assert await counts(env, user) == {"searches": 1}
    assert await use(env, user, "r3") is not None


async def test_the_count_starts_again_with_the_month(env: Env) -> None:
    user = await account(env)
    env.clock.advance(datetime(2026, 10, 31, 23, 59, tzinfo=UTC) - env.clock.now())
    await use(env, user, "october-1")
    await use(env, user, "october-2")
    assert await use(env, user, "october-3") is None
    env.clock.advance(timedelta(minutes=1))  # 1 Nov, 00:00 UTC
    november = await use(env, user, "november-1")
    assert november is not None and november.used == 1
    assert await counts(env, user) == {"searches": 1}


async def test_two_requests_for_the_last_use_count_once(env: Env) -> None:
    user = await account(env)
    await use(env, user, "r1")
    results = await asyncio.gather(*(use(env, user, f"race-{n}") for n in range(4)))
    assert sum(1 for got in results if got is not None) == 1
    assert await counts(env, user) == {"searches": 2}


async def test_interest_is_set_and_cleared_idempotently(env: Env) -> None:
    user = await account(env)
    billing = env.repos.billing
    now = env.clock.now()
    assert await billing.interests(user) == []
    await billing.set_interest(user, "locked_in", on=True, now=now)
    await billing.set_interest(user, "lowkey", on=True, now=now)
    await billing.set_interest(user, "lowkey", on=True, now=now)
    assert await billing.interests(user) == ["locked_in", "lowkey"]
    await billing.set_interest(user, "lowkey", on=False, now=now)
    await billing.set_interest(user, "lowkey", on=False, now=now)
    assert await billing.interests(user) == ["locked_in"]
    assert await billing.interests("nobody") == []


def _order(user_id: str | None, n: int, *, created: datetime) -> StoredOrder:
    return StoredOrder(
        id=f"order-{n}",
        user_id=user_id,
        provider="razorpay",
        provider_order_id=f"order_rzp_{n}",
        product="dayone",
        currency="INR",
        amount_minor=99_900,
        status="created",
        created_at=created,
    )


async def test_paid_orders_are_listed_newest_first(env: Env) -> None:
    user, other = await account(env), await account(env, "raj@example.com")
    billing = env.repos.billing
    start = env.clock.now()
    for n in range(3):
        await billing.add_order(_order(user, n, created=start))
    await billing.add_order(_order(other, 9, created=start))
    await billing.settle_order("order_rzp_0", now=start, credits=0, pass_days=0, raw=None)
    await billing.settle_order("order_rzp_2", now=start + DAY, credits=0, pass_days=0, raw=None)
    await billing.settle_order("order_rzp_9", now=start, credits=0, pass_days=0, raw=None)
    await billing.fail_order("order_rzp_1")
    paid = await billing.paid_orders(user)
    assert [o.id for o in paid] == ["order-2", "order-0"]
    assert all(o.status == "paid" and o.paid_at is not None for o in paid)
    assert await billing.paid_orders("nobody") == []


async def test_the_balance_counts_the_free_applications_in_use(env: Env) -> None:
    user = await account(env)
    billing = env.repos.billing
    now = env.clock.now()
    for n in range(3):
        await billing.hold(user, f"apply:a{n}", now=now, free_allowance=2, cost=None)
    assert (await billing.entitlements(user, month_start=month_start(now))).apply_free_used == 2
    await billing.release(user, "apply:a0", now=now)
    assert (await billing.entitlements(user, month_start=month_start(now))).apply_free_used == 1


async def test_a_hold_of_nothing_can_be_refused(env: Env) -> None:
    user = await account(env)
    billing = env.repos.billing
    now = env.clock.now()
    free = await billing.hold(user, "apply:a1", now=now, free_allowance=1, cost=None, zero_hold=False)
    assert free is not None and free.source == "free_apply"
    assert await billing.hold(user, "apply:a2", now=now, free_allowance=1, cost=None, zero_hold=False) is None
    replay = await billing.hold(user, "apply:a1", now=now, free_allowance=1, cost=None, zero_hold=False)
    assert replay is not None and replay.replayed
    held = await billing.hold(user, "apply:a2", now=now, free_allowance=1, cost=None)
    assert held is not None and held.source == "hold"
