"""The auto-apply credit hold on the ledger, run against every backend (the fakes, SQLite file and memory, and Postgres
when configured), so `FakeBillingRepo` cannot drift from `SqlBillingRepo`: the free applications first and then the
price, a hold of nothing while no price is set, idempotency, and that a released hold is never charged and a charged
one is never given back."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from tayyari.billing.entitlements import month_start
from tayyari.storage.tables import CreditLedgerRow

if TYPE_CHECKING:
    from tests.unit.storage.conftest import Env

FREE = 2
"""Free applications in these tests (the setting's default is 10)."""


async def account(env: Env, email: str = "ana@example.com") -> str:
    return (await env.repos.users.sign_in_email(email=email, now=env.clock.now())).id


async def credits(env: Env, user_id: str) -> int:
    return (await env.repos.billing.entitlements(user_id, month_start=month_start(env.clock.now()))).credits


async def ledger(env: Env, user_id: str) -> list[tuple[str, int, str | None]]:
    """``(reason, delta, ref)`` of every ledger row of the account, oldest first."""
    if env.fakes is not None:
        return [(r.reason, r.delta, r.ref) for r in env.fakes.billing.ledger_of(user_id)]
    assert env.db is not None
    async with env.db.session() as s:
        rows = (
            await s.execute(
                select(CreditLedgerRow.reason, CreditLedgerRow.delta, CreditLedgerRow.ref)
                .where(CreditLedgerRow.user_id == user_id)
                .order_by(CreditLedgerRow.created_at, CreditLedgerRow.id)
            )
        ).all()
    return [(r.reason, r.delta, r.ref) for r in rows]


async def hold(env: Env, user_id: str, ref: str, *, cost: int | None = 1, free: int = FREE):  # type: ignore[no-untyped-def]
    return await env.repos.billing.hold(user_id, ref, now=env.clock.now(), free_allowance=free, cost=cost)


async def test_the_free_applications_come_first_then_the_price(env: Env) -> None:
    ana = await account(env)
    await env.repos.billing.grant(ana, credits=3, pass_days=0, ref="o1", now=env.clock.now())
    first = await hold(env, ana, "apply:a1", cost=2)
    second = await hold(env, ana, "apply:a2", cost=2)
    assert first is not None and first.source == "free_apply" and first.credits == 3
    assert second is not None and second.source == "free_apply"
    paid = await hold(env, ana, "apply:a3", cost=2)
    assert paid is not None and paid.source == "credit" and paid.credits == 1
    assert await hold(env, ana, "apply:a4", cost=2) is None  # one credit left does not cover two
    assert await credits(env, ana) == 1
    rows = await ledger(env, ana)
    assert sorted(r for r in rows if r[0] != "purchase") == [  # one clock tick: compared as a set
        ("apply_free", 0, "apply:a1"),
        ("apply_free", 0, "apply:a2"),
        ("apply_hold", -2, "apply:a3"),
    ]
    assert sum(r[1] for r in rows) == await credits(env, ana)  # the balance is the sum of the deltas


async def test_free_applications_are_lifetime_and_a_released_one_is_free_again(env: Env) -> None:
    ana = await account(env)
    await hold(env, ana, "apply:a1", cost=None)
    await hold(env, ana, "apply:a2", cost=None)
    env.clock.advance(timedelta(days=45))  # a new month gives no new free applications
    third = await hold(env, ana, "apply:a3", cost=None)
    assert third is not None and third.source == "hold"
    assert await env.repos.billing.release(ana, "apply:a1", now=env.clock.now()) is True
    fourth = await hold(env, ana, "apply:a4", cost=None)
    assert fourth is not None and fourth.source == "free_apply"
    # a hold never spends the month's free posting match
    used = await env.repos.billing.entitlements(ana, month_start=month_start(env.clock.now()))
    assert used.free_used == 0


async def test_no_price_gives_a_hold_of_nothing_that_still_captures_and_releases(env: Env) -> None:
    ana = await account(env)
    held = await hold(env, ana, "apply:a1", cost=None, free=0)
    assert held is not None and held.source == "hold" and held.credits == 0
    assert await ledger(env, ana) == [("apply_hold", 0, "apply:a1")]
    assert await env.repos.billing.hold_state(ana, "apply:a1") == "held"
    assert await env.repos.billing.capture(ana, "apply:a1", now=env.clock.now()) is True
    assert await env.repos.billing.hold_state(ana, "apply:a1") == "charged"
    other = await hold(env, ana, "apply:a2", cost=None, free=0)
    assert other is not None
    assert await env.repos.billing.release(ana, "apply:a2", now=env.clock.now()) is True
    assert await env.repos.billing.hold_state(ana, "apply:a2") == "released"
    assert await credits(env, ana) == 0


async def test_a_hold_is_idempotent_on_its_ref(env: Env) -> None:
    ana = await account(env)
    await env.repos.billing.grant(ana, credits=2, pass_days=0, ref="o1", now=env.clock.now())
    first = await hold(env, ana, "apply:a1", free=0)
    again = await hold(env, ana, "apply:a1", free=0)
    assert first is not None and first.replayed is False and first.credits == 1
    assert again is not None and again.replayed is True and again.source == "credit" and again.credits == 1
    assert await credits(env, ana) == 1
    assert [r for r in await ledger(env, ana) if r[0] == "apply_hold"] == [("apply_hold", -1, "apply:a1")]
    # a captured hold is still the same hold: asking again takes nothing
    await env.repos.billing.capture(ana, "apply:a1", now=env.clock.now())
    replay = await hold(env, ana, "apply:a1", free=0)
    assert replay is not None and replay.replayed is True and await credits(env, ana) == 1


async def test_capture_after_release_is_refused(env: Env) -> None:
    ana = await account(env)
    await env.repos.billing.grant(ana, credits=1, pass_days=0, ref="o1", now=env.clock.now())
    await hold(env, ana, "apply:a1", free=0)
    assert await env.repos.billing.release(ana, "apply:a1", now=env.clock.now()) is True
    assert await credits(env, ana) == 1
    assert await env.repos.billing.capture(ana, "apply:a1", now=env.clock.now()) is False
    assert await env.repos.billing.hold_state(ana, "apply:a1") == "released"
    assert not any(r[0] == "apply_capture" for r in await ledger(env, ana))
    assert await env.repos.billing.release(ana, "apply:a1", now=env.clock.now()) is False  # once only


async def test_release_after_capture_is_refused(env: Env) -> None:
    ana = await account(env)
    await env.repos.billing.grant(ana, credits=1, pass_days=0, ref="o1", now=env.clock.now())
    await hold(env, ana, "apply:a1", free=0)
    assert await env.repos.billing.capture(ana, "apply:a1", now=env.clock.now()) is True
    assert await env.repos.billing.release(ana, "apply:a1", now=env.clock.now()) is False
    assert await credits(env, ana) == 0
    assert await env.repos.billing.hold_state(ana, "apply:a1") == "charged"
    assert not any(r[0] == "refund" for r in await ledger(env, ana))


async def test_capture_is_idempotent(env: Env) -> None:
    ana = await account(env)
    await hold(env, ana, "apply:a1")
    assert await env.repos.billing.capture(ana, "apply:a1", now=env.clock.now()) is True
    assert await env.repos.billing.capture(ana, "apply:a1", now=env.clock.now()) is True
    rows = await ledger(env, ana)
    held_rows = [r for r in rows if r[0] == "apply_capture"]
    assert len(held_rows) == 1 and (held_rows[0][2] or "").startswith("capture:") and held_rows[0][1] == 0
    assert await env.repos.billing.capture(ana, "apply:never", now=env.clock.now()) is False
    assert await env.repos.billing.hold_state(ana, "apply:never") is None


async def test_a_reopened_application_takes_a_new_hold_on_the_same_ref(env: Env) -> None:
    ana = await account(env)
    await env.repos.billing.grant(ana, credits=2, pass_days=0, ref="o1", now=env.clock.now())
    await hold(env, ana, "apply:a1", free=0)
    await env.repos.billing.release(ana, "apply:a1", now=env.clock.now())
    reopened = await hold(env, ana, "apply:a1", free=0)
    assert reopened is not None and reopened.replayed is False and await credits(env, ana) == 1
    assert await env.repos.billing.hold_state(ana, "apply:a1") == "held"
    assert await env.repos.billing.capture(ana, "apply:a1", now=env.clock.now()) is True
    assert await env.repos.billing.hold_state(ana, "apply:a1") == "charged"


async def test_holds_and_posting_matches_do_not_mix(env: Env) -> None:
    ana = await account(env)
    now = env.clock.now()
    used = await env.repos.billing.consume(
        ana, "match-1", now=now, month_start=month_start(now), free_allowance=1
    )
    assert used is not None and used.source == "free_monthly"
    held = await hold(env, ana, "apply:a1", cost=None)
    assert held is not None and held.source == "free_apply" and held.replayed is False
    # releasing the match gives back the match, not the hold
    assert await env.repos.billing.release(ana, "match-1", now=now) is True
    assert await env.repos.billing.hold_state(ana, "apply:a1") == "held"
