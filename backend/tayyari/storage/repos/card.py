"""Radar cards (`card_snapshots`, R1): one row per account, what its card draws and whether it is shared.

`save` is an upsert on the account; the unique slug (``uq_card_snapshots_slug``) makes a clash an IntegrityError rather than
two cards behind one link. A card not shared has a NULL slug, and both databases let many rows hold NULL under the constraint.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..db import Database, dialect_insert
from ..ports import StoredCardSnapshot
from ..tables import CardSnapshotRow


def _item(row: Any) -> StoredCardSnapshot:
    return StoredCardSnapshot(
        user_id=row.user_id,
        profile_id=row.profile_id,
        slug=row.slug,
        shared=bool(row.shared),
        theme=row.theme,
        areas=[dict(area) for area in (row.areas or [])],
        skills=[dict(skill) for skill in (row.skills or [])],
        role_id=row.role_id,
        role_title=row.role_title,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlCardRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, user_id: str) -> StoredCardSnapshot | None:
        stmt = select(CardSnapshotRow).where(CardSnapshotRow.user_id == user_id)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _item(row)

    async def get_by_slug(self, slug: str) -> StoredCardSnapshot | None:
        stmt = select(CardSnapshotRow).where(CardSnapshotRow.slug == slug, CardSnapshotRow.shared.is_(True))
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _item(row)

    async def save(self, item: StoredCardSnapshot) -> None:
        stmt = dialect_insert(self._db.dialect, CardSnapshotRow).values(
            user_id=item.user_id,
            profile_id=item.profile_id,
            slug=item.slug,
            shared=item.shared,
            theme=item.theme,
            areas=[dict(area) for area in item.areas],
            skills=[dict(skill) for skill in item.skills],
            role_id=item.role_id,
            role_title=item.role_title,
            version=item.version,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[CardSnapshotRow.user_id],
            set_={
                name: getattr(stmt.excluded, name)
                for name in (
                    "profile_id",
                    "slug",
                    "shared",
                    "theme",
                    "areas",
                    "skills",
                    "role_id",
                    "role_title",
                    "version",
                    "updated_at",
                )
            },
        )
        async with self._db.session() as s:
            await s.execute(stmt)

    async def list_page(self, after_user_id: str | None, limit: int) -> list[StoredCardSnapshot]:
        stmt = select(CardSnapshotRow).order_by(CardSnapshotRow.user_id).limit(limit)
        if after_user_id is not None:
            stmt = stmt.where(CardSnapshotRow.user_id > after_user_id)
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_item(row) for row in rows]


__all__ = ["SqlCardRepo"]
