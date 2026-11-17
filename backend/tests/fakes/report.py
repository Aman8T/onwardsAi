"""A dict-backed `ReportRepo` that behaves like the SQL one (the repo contract tests run both).

What it keeps exact: one report per account with ``created_at`` kept on a re-save, a slug held by another report refused,
a slug found only while its report is shared, and copies in and out (JSON round-tripped, as the database stores them), so a
test cannot change the store by mutating what it got.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from tayyari.storage.ports import StoredReportSnapshot


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _copy(doc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(doc))
    return out


def _out(item: StoredReportSnapshot) -> StoredReportSnapshot:
    return replace(item, choices=_copy(item.choices), content=_copy(item.content))


class FakeReportRepo:
    def __init__(self) -> None:
        self.rows: dict[str, StoredReportSnapshot] = {}

    async def get(self, user_id: str) -> StoredReportSnapshot | None:
        found = self.rows.get(user_id)
        return None if found is None else _out(found)

    async def get_by_slug(self, slug: str) -> StoredReportSnapshot | None:
        for row in self.rows.values():
            if row.slug == slug and row.shared:
                return _out(row)
        return None

    async def save(self, item: StoredReportSnapshot) -> None:
        if item.slug is not None and any(
            row.slug == item.slug and uid != item.user_id for uid, row in self.rows.items()
        ):
            raise ValueError(f"slug {item.slug!r} is another report's")
        before = self.rows.get(item.user_id)
        self.rows[item.user_id] = replace(
            _out(item),
            created_at=_utc(before.created_at if before is not None else item.created_at),
            updated_at=_utc(item.updated_at),
        )


__all__ = ["FakeReportRepo"]
