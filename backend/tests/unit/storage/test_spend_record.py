"""`LimitRepo.record_spend` on every backend: spend no LLM-call row records (a Browser Use session) still lands in the
daily ledger, in scope ``total`` (so the spend ceiling sees it) and in its own scope."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from tayyari.storage.tables import SpendDailyRow
from tests.unit.storage.conftest import T0

if TYPE_CHECKING:
    from tests.unit.storage.conftest import Env


async def row(env: Env, day: date, scope: str) -> tuple[int, int]:
    """``(micro_usd, calls)`` of a spend_daily row, ``(0, 0)`` when there is none."""
    if env.fakes is not None:
        return env.fakes.limits.spend(day, scope)
    assert env.db is not None
    async with env.db.session() as s:
        found = (
            await s.execute(
                select(SpendDailyRow.micro_usd, SpendDailyRow.calls).where(
                    SpendDailyRow.day == day, SpendDailyRow.scope == scope
                )
            )
        ).one_or_none()
    return (0, 0) if found is None else (int(found.micro_usd), int(found.calls))


async def test_record_spend_adds_to_total_and_browser_use(env: Env) -> None:
    await env.repos.limits.record_spend(T0, 12_000, "browser_use")
    await env.repos.limits.record_spend(T0 + timedelta(hours=2), 3_000, "browser_use")
    day = T0.date()
    assert await row(env, day, "browser_use") == (15_000, 2)
    assert await row(env, day, "total") == (15_000, 2)
    assert await env.repos.limits.spent_today(day) == 15_000


async def test_spend_lands_on_its_own_utc_day(env: Env) -> None:
    late = T0.replace(hour=23, minute=59)
    await env.repos.limits.record_spend(late, 1_000, "browser_use")
    await env.repos.limits.record_spend(late + timedelta(minutes=2), 2_000, "browser_use")
    assert await env.repos.limits.spent_today(late.date()) == 1_000
    assert await env.repos.limits.spent_today(late.date() + timedelta(days=1)) == 2_000


async def test_the_total_scope_is_counted_once(env: Env) -> None:
    await env.repos.limits.record_spend(T0, 500, "total")
    assert await row(env, T0.date(), "total") == (500, 1)
