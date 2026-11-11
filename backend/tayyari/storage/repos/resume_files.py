"""Resume files (`resume_files`): every file a profile was analysed from or had added, with its original bytes.

The bytes are read on their own (`get_data`), so listing a profile's files never loads them. A file is never changed
after it is stored; the repository only adds, reads and hands an account's files over when its profile is claimed.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from ..db import Database, rowcount
from ..ports import ResumeFileOrigin, StoredResumeFile
from ..tables import ResumeFileRow


def _item(row: Any) -> StoredResumeFile:
    return StoredResumeFile(
        id=row.id,
        user_id=row.user_id,
        profile_id=row.profile_id,
        origin=cast("ResumeFileOrigin", row.origin),
        filename=row.filename,
        mime=row.mime,
        size=row.size,
        sha256=row.sha256,
        text=row.text,
        facts=dict(row.facts or {}),
        merge=None if row.merge is None else dict(row.merge),
        merged_book=None if row.merged_book is None else dict(row.merged_book),
        created_at=row.created_at,
    )


class SqlResumeFileRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, item: StoredResumeFile, data: bytes) -> StoredResumeFile:
        try:
            async with self._db.session() as s:
                s.add(
                    ResumeFileRow(
                        id=item.id,
                        user_id=item.user_id,
                        profile_id=item.profile_id,
                        origin=item.origin,
                        filename=item.filename,
                        mime=item.mime,
                        size=item.size,
                        sha256=item.sha256,
                        data=data,
                        text=item.text,
                        facts=item.facts,
                        merge=item.merge,
                        merged_book=item.merged_book,
                        created_at=item.created_at,
                    )
                )
        except IntegrityError:
            existing = await self.get(item.id)
            if existing is None:
                raise
            return existing
        stored = await self.get(item.id)
        return stored if stored is not None else item

    async def get(self, file_id: str) -> StoredResumeFile | None:
        stmt = select(ResumeFileRow).where(ResumeFileRow.id == file_id).options(defer(ResumeFileRow.data))
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
            return None if row is None else _item(row)

    async def get_data(self, file_id: str) -> bytes | None:
        async with self._db.session() as s:
            data = (
                await s.execute(select(ResumeFileRow.data).where(ResumeFileRow.id == file_id))
            ).scalar_one_or_none()
            return None if data is None else bytes(data)

    async def list_for_profile(self, profile_id: str) -> list[StoredResumeFile]:
        stmt = (
            select(ResumeFileRow)
            .where(ResumeFileRow.profile_id == profile_id)
            .order_by(ResumeFileRow.created_at, ResumeFileRow.id)
            .options(defer(ResumeFileRow.data))
        )
        async with self._db.session() as s:
            return [_item(row) for row in (await s.execute(stmt)).scalars().all()]

    async def adopt(self, profile_id: str, user_id: str) -> int:
        stmt = (
            update(ResumeFileRow)
            .where(ResumeFileRow.profile_id == profile_id, ResumeFileRow.user_id.is_(None))
            .values(user_id=user_id)
        )
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt))


__all__ = ["SqlResumeFileRepo"]
