"""Desk notes (`desk_notes`): the dashboard's slips of an account's profile.

Every statement is scoped by ``(user_id, profile_id)``. `replace` makes a list the whole list in one transaction: the
rows it does not name are deleted, the others are upserted by ``note_id``. The text is stored as given (data).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select

from ..db import Database, dialect_insert
from ..ports import StoredDeskNote
from ..tables import DeskNoteRow


class SqlDeskNoteRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self, user_id: str, profile_id: str) -> list[StoredDeskNote]:
        stmt = (
            select(DeskNoteRow)
            .where(DeskNoteRow.user_id == user_id, DeskNoteRow.profile_id == profile_id)
            .order_by(DeskNoteRow.at.desc(), DeskNoteRow.note_id)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [
            StoredDeskNote(id=r.note_id, kind=r.kind, text=r.text, at=r.at, label=r.label, by=r.by)
            for r in rows
        ]

    async def replace(
        self, user_id: str, profile_id: str, notes: Sequence[StoredDeskNote], now: datetime
    ) -> None:
        keep = [n.id for n in notes]
        async with self._db.session() as s:
            await s.execute(
                delete(DeskNoteRow).where(
                    DeskNoteRow.user_id == user_id,
                    DeskNoteRow.profile_id == profile_id,
                    DeskNoteRow.note_id.not_in(keep),
                )
            )
            for n in notes:
                stmt = dialect_insert(self._db.dialect, DeskNoteRow).values(
                    user_id=user_id,
                    profile_id=profile_id,
                    note_id=n.id,
                    kind=n.kind,
                    text=n.text,
                    label=n.label,
                    by=n.by,
                    at=n.at,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[DeskNoteRow.user_id, DeskNoteRow.profile_id, DeskNoteRow.note_id],
                    set_={
                        name: getattr(stmt.excluded, name)
                        for name in ("kind", "text", "label", "by", "at", "updated_at")
                    },
                )
                await s.execute(stmt)
