"""Claim marks (`claim_marks`, I2): one row per judged claim of a finished practice session.

Scoped to a profile: another profile's mark reads as nothing and cannot be changed, whatever the method. `add` is an insert
that skips a ``(profile, session, slot)`` already stored, so writing a session's marks twice (an ``end`` retried, a history
read backfilling) adds nothing and never resets a Fix step's place or tick. The Fix step's own text is written with the mark;
`place_fix`, `set_fix_done` and `clear_fix` change only where it is and whether it is ticked.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, or_, select, update

from ..db import Database, dialect_insert, rowcount
from ..ports import StoredClaimMark
from ..tables import ClaimMarkRow


def _item(row: Any) -> StoredClaimMark:
    return StoredClaimMark(
        id=row.id,
        profile_id=row.profile_id,
        claim_key=row.claim_key,
        claim_text=row.claim_text,
        source=row.source,
        session_id=row.session_id,
        slot_id=row.slot_id,
        session_kind=row.session_kind,
        slot_kind=row.slot_kind,
        verdict=row.verdict,
        rung=row.rung,
        question=row.question,
        marked_on=row.marked_on,
        created_at=row.created_at,
        quote=row.quote,
        story=None if row.story is None else dict(row.story),
        fix_text=row.fix_text,
        fix_minutes=row.fix_minutes,
        fix_plan_id=row.fix_plan_id,
        fix_kept=bool(row.fix_kept),
        fix_added_at=row.fix_added_at,
        fix_done_at=row.fix_done_at,
    )


def _values(item: StoredClaimMark) -> dict[str, Any]:
    return {
        "id": item.id,
        "profile_id": item.profile_id,
        "claim_key": item.claim_key,
        "claim_text": item.claim_text,
        "source": item.source,
        "session_id": item.session_id,
        "slot_id": item.slot_id,
        "session_kind": item.session_kind,
        "slot_kind": item.slot_kind,
        "verdict": item.verdict,
        "rung": item.rung,
        "question": item.question,
        "quote": item.quote,
        "story": None if item.story is None else dict(item.story),
        "marked_on": item.marked_on,
        "fix_text": item.fix_text,
        "fix_minutes": item.fix_minutes,
        "fix_plan_id": item.fix_plan_id,
        "fix_kept": item.fix_kept,
        "fix_added_at": item.fix_added_at,
        "fix_done_at": item.fix_done_at,
        "created_at": item.created_at,
    }


_OLDEST_FIRST = (ClaimMarkRow.marked_on, ClaimMarkRow.created_at, ClaimMarkRow.id)
_PLACED_FIRST = (ClaimMarkRow.fix_added_at, ClaimMarkRow.id)


class SqlClaimMarkRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, items: Sequence[StoredClaimMark]) -> int:
        added = 0
        async with self._db.session() as s:
            for item in items:
                result = await s.execute(
                    dialect_insert(self._db.dialect, ClaimMarkRow)
                    .values(**_values(item))
                    .on_conflict_do_nothing(
                        index_elements=[
                            ClaimMarkRow.profile_id,
                            ClaimMarkRow.session_id,
                            ClaimMarkRow.slot_id,
                        ]
                    )
                )
                added += rowcount(result)
        return added

    async def list_for_profile(self, profile_id: str, limit: int) -> list[StoredClaimMark]:
        stmt = (
            select(ClaimMarkRow)
            .where(ClaimMarkRow.profile_id == profile_id)
            .order_by(*_OLDEST_FIRST)
            .limit(limit)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_item(row) for row in rows]

    async def session_ids(self, profile_id: str) -> set[str]:
        stmt = select(ClaimMarkRow.session_id).where(ClaimMarkRow.profile_id == profile_id).distinct()
        async with self._db.session() as s:
            return set((await s.execute(stmt)).scalars().all())

    async def get(self, profile_id: str, mark_id: str) -> StoredClaimMark | None:
        stmt = select(ClaimMarkRow).where(ClaimMarkRow.id == mark_id, ClaimMarkRow.profile_id == profile_id)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _item(row)

    async def find(self, profile_id: str, session_id: str, slot_id: str) -> StoredClaimMark | None:
        stmt = select(ClaimMarkRow).where(
            ClaimMarkRow.profile_id == profile_id,
            ClaimMarkRow.session_id == session_id,
            ClaimMarkRow.slot_id == slot_id,
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _item(row)

    async def place_fix(self, profile_id: str, mark_id: str, *, plan_id: str | None, now: datetime) -> bool:
        async with self._db.session() as s:
            result = await s.execute(
                update(ClaimMarkRow)
                .where(
                    ClaimMarkRow.id == mark_id,
                    ClaimMarkRow.profile_id == profile_id,
                    ClaimMarkRow.fix_text.is_not(None),
                )
                .values(fix_plan_id=plan_id, fix_kept=plan_id is None, fix_added_at=now)
            )
            return rowcount(result) == 1

    async def set_fix_done(self, profile_id: str, mark_id: str, done_at: datetime | None) -> bool:
        async with self._db.session() as s:
            result = await s.execute(
                update(ClaimMarkRow)
                .where(ClaimMarkRow.id == mark_id, ClaimMarkRow.profile_id == profile_id, _placed())
                .values(fix_done_at=done_at)
            )
            return rowcount(result) == 1

    async def clear_fix(self, profile_id: str, mark_id: str) -> bool:
        async with self._db.session() as s:
            result = await s.execute(
                update(ClaimMarkRow)
                .where(ClaimMarkRow.id == mark_id, ClaimMarkRow.profile_id == profile_id, _placed())
                .values(fix_plan_id=None, fix_kept=False, fix_added_at=None, fix_done_at=None)
            )
            return rowcount(result) == 1

    async def fixes_for_plan(self, profile_id: str, plan_id: str) -> list[StoredClaimMark]:
        stmt = (
            select(ClaimMarkRow)
            .where(ClaimMarkRow.profile_id == profile_id, ClaimMarkRow.fix_plan_id == plan_id)
            .order_by(*_PLACED_FIRST)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_item(row) for row in rows]

    async def kept_fixes(self, profile_id: str) -> list[StoredClaimMark]:
        stmt = (
            select(ClaimMarkRow)
            .where(ClaimMarkRow.profile_id == profile_id, ClaimMarkRow.fix_kept.is_(True))
            .order_by(*_PLACED_FIRST)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_item(row) for row in rows]

    async def delete_for_session(self, profile_id: str, session_id: str) -> int:
        async with self._db.session() as s:
            result = await s.execute(
                delete(ClaimMarkRow).where(
                    ClaimMarkRow.profile_id == profile_id, ClaimMarkRow.session_id == session_id
                )
            )
            return rowcount(result)


def _placed() -> Any:
    """A mark whose Fix step is in a plan or kept in Practice."""
    return or_(ClaimMarkRow.fix_plan_id.is_not(None), ClaimMarkRow.fix_kept.is_(True))


__all__ = ["SqlClaimMarkRepo"]
