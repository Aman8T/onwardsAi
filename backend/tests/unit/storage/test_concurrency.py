"""Atomicity under concurrency, on a real database (SQLite file; Postgres when configured)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from tayyari.contract.records import LlmCallRecord
from tayyari.contract.stored import StoredContext
from tayyari.storage.db import Database
from tayyari.storage.repos import build_repositories
from tayyari.storage.tables import ChatMessageRow, RateLimitHitRow, SpendDailyRow

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


async def test_fifty_concurrent_hits_count_to_fifty(any_real_db: Database) -> None:
    limits = build_repositories(any_real_db).limits
    counts = await asyncio.gather(*(limits.hit("analyze:min:abc", 60, NOW) for _ in range(50)))

    assert sorted(counts) == list(range(1, 51))  # every caller saw its own, distinct count
    assert await limits.hit("analyze:min:abc", 60, NOW) == 51
    async with any_real_db.session() as s:
        stored = (await s.execute(select(RateLimitHitRow.count))).scalar_one()
    assert stored == 51


async def test_concurrent_hits_in_different_windows_and_buckets_do_not_mix(any_real_db: Database) -> None:
    limits = build_repositories(any_real_db).limits
    jobs = [limits.hit(f"b{i % 3}", 60, NOW + timedelta(minutes=i % 2)) for i in range(30)]
    counts = await asyncio.gather(*jobs)
    # 3 buckets x 2 windows, 5 hits each.
    assert sorted(counts) == sorted([1, 2, 3, 4, 5] * 6)


async def test_concurrent_calls_sum_the_ledger_exactly(
    any_real_db: Database, make_llm_call: Callable[..., LlmCallRecord]
) -> None:
    repos = build_repositories(any_real_db)
    costs = [1_000 + i for i in range(40)]
    await asyncio.gather(
        *(repos.llm_calls.record(make_llm_call(created_at=NOW, cost_micro_usd=c)) for c in costs)
    )

    assert await repos.limits.spent_today(NOW.date()) == sum(costs)
    async with any_real_db.session() as s:
        rows = (
            await s.execute(select(SpendDailyRow.scope, SpendDailyRow.micro_usd, SpendDailyRow.calls))
        ).all()
    assert {r.scope: (r.micro_usd, r.calls) for r in rows} == {
        "total": (sum(costs), 40),
        "anthropic": (sum(costs), 40),
    }


async def test_concurrent_turns_on_one_session_get_distinct_sequence_numbers(any_real_db: Database) -> None:
    repos = build_repositories(any_real_db)
    await repos.profiles.save("p1", {}, StoredContext(), goal=None, owner_hash=None, is_sample=False)
    session = await repos.sessions.create("p1", None, None)
    llm_ids = [uuid.uuid4() for _ in range(10)]
    await asyncio.gather(
        *(
            repos.sessions.append_turn(
                session.id, user=f"q{i}", assistant=f"a{i}", tools=[], llm_call_id=llm_ids[i]
            )
            for i in range(10)
        )
    )
    async with any_real_db.session() as s:
        seqs = (
            (
                await s.execute(
                    select(ChatMessageRow.seq).where(ChatMessageRow.session_id == session.id).order_by("seq")
                )
            )
            .scalars()
            .all()
        )
        total = (await s.execute(select(func.count()).select_from(ChatMessageRow))).scalar_one()
    assert list(seqs) == list(range(20))
    assert total == 20
    loaded = await repos.sessions.load(session.id, "p1", limit=100)
    assert loaded is not None and loaded.turn_count == 10
