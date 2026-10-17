"""Practice interviews (`interview_sessions`).

One row per session, scoped to a profile: another profile's session id reads as nothing, whatever the method. Each write
is one short transaction that starts with a write. `save` is a compare-and-swap on ``version``: it lands only when the
stored version is the one before the item's, so two tabs that answer at once cannot overwrite each other (the loser gets
False and tells the person the interview moved on). Nothing here is shared between profiles.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update

from ..db import Database, rowcount
from ..ports import StoredInterview
from ..tables import InterviewSessionRow


def _item(row: Any) -> StoredInterview:
    return StoredInterview(
        id=row.id,
        profile_id=row.profile_id,
        kind=row.kind,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=row.version,
        state=dict(row.state or {}),
        debrief=None if row.debrief is None else dict(row.debrief),
    )


class SqlInterviewRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, item: StoredInterview) -> None:
        async with self._db.session() as s:
            await s.execute(
                insert(InterviewSessionRow).values(
                    id=item.id,
                    profile_id=item.profile_id,
                    kind=item.kind,
                    status=item.status,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    version=item.version,
                    state=item.state,
                    debrief=item.debrief,
                )
            )

    async def get(self, profile_id: str, session_id: str) -> StoredInterview | None:
        stmt = select(InterviewSessionRow).where(
            InterviewSessionRow.id == session_id, InterviewSessionRow.profile_id == profile_id
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _item(row)

    async def save(self, item: StoredInterview) -> bool:
        async with self._db.session() as s:
            result = await s.execute(
                update(InterviewSessionRow)
                .where(
                    InterviewSessionRow.id == item.id,
                    InterviewSessionRow.profile_id == item.profile_id,
                    InterviewSessionRow.version == item.version - 1,
                )
                .values(
                    state=item.state,
                    status=item.status,
                    debrief=item.debrief,
                    updated_at=item.updated_at,
                    version=item.version,
                )
            )
            return rowcount(result) == 1

    async def list(self, profile_id: str, limit: int) -> list[StoredInterview]:
        if limit <= 0:
            return []
        stmt = (
            select(InterviewSessionRow)
            .where(InterviewSessionRow.profile_id == profile_id)
            .order_by(InterviewSessionRow.created_at.desc(), InterviewSessionRow.id.desc())
            .limit(limit)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_item(row) for row in rows]

    async def delete(self, profile_id: str, session_id: str) -> bool:
        async with self._db.session() as s:
            result = await s.execute(
                delete(InterviewSessionRow).where(
                    InterviewSessionRow.id == session_id, InterviewSessionRow.profile_id == profile_id
                )
            )
            return rowcount(result) == 1

    async def count_since(self, profile_id: str, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(InterviewSessionRow)
            .where(InterviewSessionRow.profile_id == profile_id, InterviewSessionRow.created_at >= since)
        )
        async with self._db.session() as s:
            return int((await s.execute(stmt)).scalar_one())

    async def purge(self, cutoff: datetime) -> int:
        async with self._db.session() as s:
            return rowcount(
                await s.execute(delete(InterviewSessionRow).where(InterviewSessionRow.created_at < cutoff))
            )


__all__ = ["SqlInterviewRepo"]
