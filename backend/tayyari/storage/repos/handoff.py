"""Hand-offs from the marketing site (`handoffs`): stored once, taken once.

`take` is a single ``DELETE ... RETURNING``, so two requests for the same id cannot both get it, and a hand-off that is
read is gone in the same transaction. `purge_expired` is the cheap ``DELETE WHERE expires_at < now`` each new hand-off runs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, insert

from ..db import Database, rowcount
from ..ports import StoredHandoff
from ..tables import HandoffRow


def _item(row: Any) -> StoredHandoff:
    return StoredHandoff(
        id=row.id,
        created_at=row.created_at,
        expires_at=row.expires_at,
        links=[str(link) for link in (row.links or [])],
        goal=row.goal,
        notes=row.notes,
        resume_name=row.resume_name,
        resume_type=row.resume_type,
        resume_bytes=None if row.resume_bytes is None else bytes(row.resume_bytes),
    )


class SqlHandoffRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, item: StoredHandoff) -> None:
        async with self._db.session() as s:
            await s.execute(
                insert(HandoffRow).values(
                    id=item.id,
                    created_at=item.created_at,
                    expires_at=item.expires_at,
                    links=list(item.links),
                    goal=item.goal,
                    notes=item.notes,
                    resume_name=item.resume_name,
                    resume_type=item.resume_type,
                    resume_bytes=item.resume_bytes,
                )
            )

    async def take(self, handoff_id: str) -> StoredHandoff | None:
        stmt = delete(HandoffRow).where(HandoffRow.id == handoff_id).returning(*HandoffRow.__table__.columns)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        return None if row is None else _item(row)

    async def purge_expired(self, now: datetime) -> int:
        async with self._db.session() as s:
            return rowcount(await s.execute(delete(HandoffRow).where(HandoffRow.expires_at < now)))


__all__ = ["SqlHandoffRepo"]
