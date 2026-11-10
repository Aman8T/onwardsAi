"""Resumes (`resumes`, R2): one row per resume of a profile.

A profile has at most one resume of each ``(kind, role_key)``: the unique key decides a race between two first
requests, and `create` then hands back the row that won. `save` is a compare-and-swap on ``version``, so two tabs
editing the same draft cannot overwrite each other (the loser gets False and the route answers 409). `set_default`
moves the profile's one default in a single transaction, and only onto a resume that has a finalised version.

The rows are read by id alone: who may open a resume is decided above storage, from its profile.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from tayyari.core.logging import get_logger

from ..db import Database, rowcount
from ..ports import ResumeKind, StoredResume
from ..tables import ResumeRow

log = get_logger(__name__)


def _item(row: Any) -> StoredResume:
    return StoredResume(
        id=row.id,
        user_id=row.user_id,
        profile_id=row.profile_id,
        kind=cast("ResumeKind", row.kind),
        role_key=row.role_key,
        title=row.title,
        draft=dict(row.draft or {}),
        final=None if row.final is None else dict(row.final),
        facts=dict(row.facts or {}),
        is_default=bool(row.is_default),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        finalised_at=row.finalised_at,
    )


class SqlResumeRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, item: StoredResume) -> StoredResume:
        try:
            async with self._db.session() as s:
                s.add(
                    ResumeRow(
                        id=item.id,
                        user_id=item.user_id,
                        profile_id=item.profile_id,
                        kind=item.kind,
                        role_key=item.role_key,
                        title=item.title,
                        draft=item.draft,
                        final=item.final,
                        facts=item.facts,
                        is_default=item.is_default,
                        version=item.version,
                        created_at=item.created_at,
                        updated_at=item.updated_at,
                        finalised_at=item.finalised_at,
                    )
                )
        except IntegrityError:
            # A concurrent first request made the same resume and won the unique key: that one is the resume.
            existing = await self.find(item.profile_id, item.kind, item.role_key)
            if existing is None:
                raise
            log.info("resume_create_raced", kind=item.kind)
            return existing
        stored = await self.get(item.id)
        return stored if stored is not None else item

    async def get(self, resume_id: str) -> StoredResume | None:
        async with self._db.session() as s:
            row = (
                (await s.execute(select(ResumeRow).where(ResumeRow.id == resume_id))).scalars().one_or_none()
            )
            return None if row is None else _item(row)

    async def find(self, profile_id: str, kind: ResumeKind, role_key: str) -> StoredResume | None:
        stmt = select(ResumeRow).where(
            ResumeRow.profile_id == profile_id, ResumeRow.kind == kind, ResumeRow.role_key == role_key
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
            return None if row is None else _item(row)

    async def list_for_profile(self, profile_id: str) -> list[StoredResume]:
        stmt = (
            select(ResumeRow)
            .where(ResumeRow.profile_id == profile_id)
            .order_by(ResumeRow.created_at, ResumeRow.id)
        )
        async with self._db.session() as s:
            return [_item(row) for row in (await s.execute(stmt)).scalars().all()]

    async def save(self, item: StoredResume, *, expected_version: int) -> bool:
        stmt = (
            update(ResumeRow)
            .where(ResumeRow.id == item.id, ResumeRow.version == expected_version)
            .values(
                draft=item.draft,
                final=item.final,
                facts=item.facts,
                finalised_at=item.finalised_at,
                updated_at=item.updated_at,
                version=item.version,
            )
        )
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt)) == 1

    async def set_default(self, profile_id: str, resume_id: str) -> bool:
        async with self._db.session() as s:
            chosen = await s.execute(
                update(ResumeRow)
                .where(
                    ResumeRow.id == resume_id,
                    ResumeRow.profile_id == profile_id,
                    ResumeRow.final.is_not(None),
                )
                .values(is_default=True)
            )
            if rowcount(chosen) != 1:
                return False
            await s.execute(
                update(ResumeRow)
                .where(
                    ResumeRow.profile_id == profile_id,
                    ResumeRow.id != resume_id,
                    ResumeRow.is_default.is_(True),
                )
                .values(is_default=False)
            )
            return True

    async def adopt(self, profile_id: str, user_id: str) -> int:
        stmt = (
            update(ResumeRow)
            .where(ResumeRow.profile_id == profile_id, ResumeRow.user_id.is_(None))
            .values(user_id=user_id)
        )
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt))


__all__ = ["SqlResumeRepo"]
