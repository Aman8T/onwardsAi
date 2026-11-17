"""Reports (`report_snapshots`, N1): one row per account, the owner's choices and the public content, and whether shared.

`save` is an upsert on the account; the unique slug (``uq_report_snapshots_slug``) makes a clash an IntegrityError rather
than two reports behind one link. A report not shared has a NULL slug, and both databases let many rows hold NULL under it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..db import Database, dialect_insert
from ..ports import StoredReportSnapshot
from ..tables import ReportSnapshotRow


def _item(row: Any) -> StoredReportSnapshot:
    return StoredReportSnapshot(
        user_id=row.user_id,
        profile_id=row.profile_id,
        slug=row.slug,
        shared=bool(row.shared),
        choices=dict(row.choices or {}),
        content=dict(row.content or {}),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlReportRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, user_id: str) -> StoredReportSnapshot | None:
        stmt = select(ReportSnapshotRow).where(ReportSnapshotRow.user_id == user_id)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _item(row)

    async def get_by_slug(self, slug: str) -> StoredReportSnapshot | None:
        stmt = select(ReportSnapshotRow).where(
            ReportSnapshotRow.slug == slug, ReportSnapshotRow.shared.is_(True)
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _item(row)

    async def save(self, item: StoredReportSnapshot) -> None:
        stmt = dialect_insert(self._db.dialect, ReportSnapshotRow).values(
            user_id=item.user_id,
            profile_id=item.profile_id,
            slug=item.slug,
            shared=item.shared,
            choices=dict(item.choices),
            content=dict(item.content),
            version=item.version,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ReportSnapshotRow.user_id],
            set_={
                name: getattr(stmt.excluded, name)
                for name in ("profile_id", "slug", "shared", "choices", "content", "version", "updated_at")
            },
        )
        async with self._db.session() as s:
            await s.execute(stmt)


__all__ = ["SqlReportRepo"]
