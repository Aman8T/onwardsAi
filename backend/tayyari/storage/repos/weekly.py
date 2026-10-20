"""The Leafout Weekly (`weekly_subscriptions`, `weekly_editions`), and the claims two replicas use to share its work.

An edition is unique per reader and week (``uq_weekly_editions_user_id_week_of``): `create_edition` is an insert that does
nothing on a clash, so a second replica that composed the same week gets False and stores nothing. A claim is a conditional
upsert of one ``kv`` row (``weekly:claim:<key>``) that only lands when the stored row is at least ``hold`` old, the same
single-flight rule the retention purge uses; the attempts made for a week are kept in the row's value, so a reader whose
edition keeps failing is given up on after ``max_tries`` and a new week starts again at one.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, func, or_, select, update

from ..db import Database, dialect_insert, rowcount
from ..ports import StoredWeeklyEdition, StoredWeeklySubscription
from ..tables import KvRow, WeeklyEditionRow, WeeklySubscriptionRow

CLAIM_PREFIX = "weekly:claim:"
PAID_PREFIX = "weekly:paid:"


def _subscription(row: Any) -> StoredWeeklySubscription:
    return StoredWeeklySubscription(
        user_id=row.user_id,
        email=row.email,
        day=row.day,
        sections=[str(part) for part in (row.sections or [])],
        status=row.status,
        paused_until=row.paused_until,
        unsubscribe_token=row.unsubscribe_token,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _edition(row: Any) -> StoredWeeklyEdition:
    return StoredWeeklyEdition(
        id=row.id,
        user_id=row.user_id,
        issue=row.issue,
        week_of=row.week_of,
        payload=dict(row.payload or {}),
        created_at=row.created_at,
        emailed_at=row.emailed_at,
        read_at=row.read_at,
    )


def _wants(day: str, now: datetime) -> Any:
    """Rows whose reader wants ``day`` and whose subscription is running at ``now``."""
    return and_(
        WeeklySubscriptionRow.day == day,
        or_(
            WeeklySubscriptionRow.status == "active",
            and_(
                WeeklySubscriptionRow.status == "paused",
                WeeklySubscriptionRow.paused_until.is_not(None),
                WeeklySubscriptionRow.paused_until <= now,
            ),
        ),
    )


class SqlWeeklyRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ───────────────────────── subscriptions ─────────────────────────

    async def get_subscription(self, user_id: str) -> StoredWeeklySubscription | None:
        stmt = select(WeeklySubscriptionRow).where(WeeklySubscriptionRow.user_id == user_id)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _subscription(row)

    async def get_subscription_by_token(self, token: str) -> StoredWeeklySubscription | None:
        stmt = select(WeeklySubscriptionRow).where(WeeklySubscriptionRow.unsubscribe_token == token)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _subscription(row)

    async def save_subscription(self, item: StoredWeeklySubscription) -> None:
        stmt = dialect_insert(self._db.dialect, WeeklySubscriptionRow).values(
            user_id=item.user_id,
            email=item.email,
            day=item.day,
            sections=list(item.sections),
            status=item.status,
            paused_until=item.paused_until,
            unsubscribe_token=item.unsubscribe_token,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[WeeklySubscriptionRow.user_id],
            set_={
                "email": stmt.excluded.email,
                "day": stmt.excluded.day,
                "sections": stmt.excluded.sections,
                "status": stmt.excluded.status,
                "paused_until": stmt.excluded.paused_until,
                "unsubscribe_token": stmt.excluded.unsubscribe_token,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        async with self._db.session() as s:
            await s.execute(stmt)

    async def unsubscribe_by_token(self, token: str, now: datetime) -> bool:
        stmt = (
            update(WeeklySubscriptionRow)
            .where(WeeklySubscriptionRow.unsubscribe_token == token)
            .values(status="off", paused_until=None, updated_at=now)
        )
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt)) > 0

    async def due_subscriptions(
        self, *, day: str, week_of: date, now: datetime, limit: int
    ) -> list[StoredWeeklySubscription]:
        has_edition = exists().where(
            WeeklyEditionRow.user_id == WeeklySubscriptionRow.user_id, WeeklyEditionRow.week_of == week_of
        )
        stmt = (
            select(WeeklySubscriptionRow)
            .where(_wants(day, now), ~has_edition)
            .order_by(WeeklySubscriptionRow.user_id)
            .limit(limit)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_subscription(row) for row in rows]

    async def unsent_editions(
        self, *, day: str, week_of: date, now: datetime, limit: int
    ) -> list[tuple[StoredWeeklySubscription, StoredWeeklyEdition]]:
        stmt = (
            select(WeeklySubscriptionRow, WeeklyEditionRow)
            .join(
                WeeklyEditionRow,
                and_(
                    WeeklyEditionRow.user_id == WeeklySubscriptionRow.user_id,
                    WeeklyEditionRow.week_of == week_of,
                ),
            )
            .where(
                _wants(day, now),
                WeeklyEditionRow.emailed_at.is_(None),
                WeeklySubscriptionRow.email.is_not(None),
            )
            .order_by(WeeklySubscriptionRow.user_id)
            .limit(limit)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).all()
        return [(_subscription(sub), _edition(edition)) for sub, edition in rows]

    # ───────────────────────── editions ─────────────────────────

    async def create_edition(self, item: StoredWeeklyEdition) -> bool:
        stmt = (
            dialect_insert(self._db.dialect, WeeklyEditionRow)
            .values(
                id=item.id,
                user_id=item.user_id,
                issue=item.issue,
                week_of=item.week_of,
                payload=item.payload,
                created_at=item.created_at,
                emailed_at=item.emailed_at,
                read_at=item.read_at,
            )
            .on_conflict_do_nothing(index_elements=[WeeklyEditionRow.user_id, WeeklyEditionRow.week_of])
        )
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt)) == 1

    async def get_edition(self, user_id: str, edition_id: str) -> StoredWeeklyEdition | None:
        stmt = select(WeeklyEditionRow).where(
            WeeklyEditionRow.id == edition_id, WeeklyEditionRow.user_id == user_id
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _edition(row)

    async def edition_for_week(self, user_id: str, week_of: date) -> StoredWeeklyEdition | None:
        stmt = select(WeeklyEditionRow).where(
            WeeklyEditionRow.user_id == user_id, WeeklyEditionRow.week_of == week_of
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _edition(row)

    async def list_editions(self, user_id: str, limit: int) -> list[StoredWeeklyEdition]:
        stmt = (
            select(WeeklyEditionRow)
            .where(WeeklyEditionRow.user_id == user_id)
            .order_by(WeeklyEditionRow.week_of.desc(), WeeklyEditionRow.issue.desc())
            .limit(limit)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_edition(row) for row in rows]

    async def next_issue(self, user_id: str) -> int:
        stmt = select(func.max(WeeklyEditionRow.issue)).where(WeeklyEditionRow.user_id == user_id)
        async with self._db.session() as s:
            highest = (await s.execute(stmt)).scalar_one_or_none()
        return (highest or 0) + 1

    async def mark_emailed(self, user_id: str, edition_id: str, now: datetime) -> None:
        stmt = (
            update(WeeklyEditionRow)
            .where(
                WeeklyEditionRow.id == edition_id,
                WeeklyEditionRow.user_id == user_id,
                WeeklyEditionRow.emailed_at.is_(None),
            )
            .values(emailed_at=now)
        )
        async with self._db.session() as s:
            await s.execute(stmt)

    async def mark_read(self, user_id: str, edition_id: str, now: datetime) -> None:
        stmt = (
            update(WeeklyEditionRow)
            .where(
                WeeklyEditionRow.id == edition_id,
                WeeklyEditionRow.user_id == user_id,
                WeeklyEditionRow.read_at.is_(None),
            )
            .values(read_at=now)
        )
        async with self._db.session() as s:
            await s.execute(stmt)

    async def unread_count(self, user_id: str) -> int:
        stmt = select(func.count()).where(
            WeeklyEditionRow.user_id == user_id, WeeklyEditionRow.read_at.is_(None)
        )
        async with self._db.session() as s:
            return int((await s.execute(stmt)).scalar_one())

    # ───────────────────────── claims and the paid counter ─────────────────────────

    async def claim(
        self, key: str, week_of: date, *, now: datetime, hold: timedelta, max_tries: int
    ) -> int | None:
        kv_key = f"{CLAIM_PREFIX}{key}"
        week = week_of.isoformat()
        async with self._db.session() as s:
            held = (await s.execute(select(KvRow.value).where(KvRow.key == kv_key))).scalar_one_or_none()
        tries = 0
        if isinstance(held, dict) and held.get("week") == week and isinstance(held.get("tries"), int):
            tries = held["tries"]
        if tries >= max_tries:
            return None
        insert_stmt = dialect_insert(self._db.dialect, KvRow).values(
            key=kv_key, value={"week": week, "tries": tries + 1}, updated_at=now
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[KvRow.key],
            set_={"value": insert_stmt.excluded.value, "updated_at": insert_stmt.excluded.updated_at},
            where=KvRow.updated_at <= now - hold,
        ).returning(KvRow.key)
        async with self._db.session() as s:
            won = (await s.execute(stmt)).scalar_one_or_none() is not None
        return tries + 1 if won else None

    async def paid_this_week(self, week_of: date) -> int:
        async with self._db.session() as s:
            value = (
                await s.execute(select(KvRow.value).where(KvRow.key == f"{PAID_PREFIX}{week_of.isoformat()}"))
            ).scalar_one_or_none()
        return value if isinstance(value, int) else 0

    async def add_paid(self, week_of: date, now: datetime) -> None:
        count = await self.paid_this_week(week_of) + 1
        stmt = dialect_insert(self._db.dialect, KvRow).values(
            key=f"{PAID_PREFIX}{week_of.isoformat()}", value=count, updated_at=now
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[KvRow.key],
            set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
        )
        async with self._db.session() as s:
            await s.execute(stmt)


__all__ = ["SqlWeeklyRepo"]
