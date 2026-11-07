"""Rate-limit counters and the daily spend ledger.

``hit`` is one atomic ``INSERT ... ON CONFLICT DO UPDATE SET count = count + 1 RETURNING count``, so the
count is exact across replicas and under concurrency, on Postgres and on SQLite 3.35+.

``add_spend`` is the single writer of ``spend_daily``; the LLM-call and decision-log repositories call it
inside their own transaction, so a call and its cost are written together or not at all. Spend no LLM-call
row records (a Browser Use session) goes through ``record_spend``, which calls it in its own transaction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import Database, dialect_insert, rowcount
from ..tables import RateLimitHitRow, SpendDailyRow, as_utc

TOTAL_SCOPE = "total"
ANTHROPIC_SCOPE = "anthropic"
JEV_SCOPE = "jev"
BROWSER_USE_SCOPE = "browser_use"
"""Browser Use sessions: their cost is read after each session stops and recorded with `SqlLimitRepo.record_spend`."""


def window_start(now: datetime, window_s: int) -> datetime:
    """``now`` floored to a multiple of ``window_s`` seconds since the Unix epoch (UTC)."""
    if window_s <= 0:
        raise ValueError("window_s must be positive")
    epoch = int(as_utc(now).timestamp())
    return datetime.fromtimestamp(epoch - epoch % window_s, UTC)


def day_of(moment: datetime) -> date:
    """The UTC calendar day of ``moment`` (the spend ledger's key)."""
    return as_utc(moment).date()


async def add_spend(
    session: AsyncSession, dialect: str, deltas: Mapping[tuple[date, str], tuple[int, int]]
) -> None:
    """Add ``(micro_usd, calls)`` to each ``(day, scope)`` row, creating it when missing.

    Rows are written in key order so concurrent transactions lock them in the same order (no deadlocks),
    and each key appears once per statement (Postgres rejects a repeated key in one upsert).
    """
    if not deltas:
        return
    values = [
        {"day": day, "scope": scope, "micro_usd": micro, "calls": calls}
        for (day, scope), (micro, calls) in sorted(deltas.items())
    ]
    stmt = dialect_insert(dialect, SpendDailyRow).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[SpendDailyRow.day, SpendDailyRow.scope],
        set_={
            "micro_usd": SpendDailyRow.micro_usd + stmt.excluded.micro_usd,
            "calls": SpendDailyRow.calls + stmt.excluded.calls,
        },
    )
    await session.execute(stmt)


def spend_deltas(
    entries: Iterable[tuple[datetime, int, tuple[str, ...]]],
) -> dict[tuple[date, str], tuple[int, int]]:
    """Fold ``(created_at, cost, scopes)`` entries into per ``(day, scope)`` ``(micro_usd, calls)`` sums."""
    out: dict[tuple[date, str], tuple[int, int]] = {}
    for created_at, cost, scopes in entries:
        day = day_of(created_at)
        for scope in scopes:
            micro, calls = out.get((day, scope), (0, 0))
            out[(day, scope)] = (micro + cost, calls + 1)
    return out


class SqlLimitRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def hit(self, bucket: str, window_s: int, now: datetime) -> int:
        start = window_start(now, window_s)
        insert_stmt = dialect_insert(self._db.dialect, RateLimitHitRow).values(
            bucket=bucket, window_start=start, count=1
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[RateLimitHitRow.bucket, RateLimitHitRow.window_start],
            set_={"count": RateLimitHitRow.count + 1},
        ).returning(RateLimitHitRow.count)
        async with self._db.session() as s:
            return int((await s.execute(stmt)).scalar_one())

    async def spent_today(self, day: date) -> int:
        stmt = select(SpendDailyRow.micro_usd).where(
            SpendDailyRow.day == day, SpendDailyRow.scope == TOTAL_SCOPE
        )
        async with self._db.session() as s:
            value = (await s.execute(stmt)).scalar_one_or_none()
        return int(value or 0)

    async def purge_hits(self, before: datetime) -> int:
        async with self._db.session() as s:
            return rowcount(
                await s.execute(delete(RateLimitHitRow).where(RateLimitHitRow.window_start < before))
            )

    async def record_spend(self, at: datetime, micro_usd: int, scope: str) -> None:
        scopes = (TOTAL_SCOPE,) if scope == TOTAL_SCOPE else (TOTAL_SCOPE, scope)
        async with self._db.session() as s:
            await add_spend(s, self._db.dialect, spend_deltas([(at, micro_usd, scopes)]))


__all__ = [
    "ANTHROPIC_SCOPE",
    "BROWSER_USE_SCOPE",
    "JEV_SCOPE",
    "TOTAL_SCOPE",
    "SqlLimitRepo",
    "add_spend",
    "day_of",
    "spend_deltas",
    "window_start",
]
