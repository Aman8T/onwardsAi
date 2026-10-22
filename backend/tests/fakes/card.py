"""A dict-backed `CardRepo` that behaves like the SQL one (the repo contract tests run both).

What it keeps exact: one card per account with ``created_at`` kept on a re-save, a slug held by another card refused, a slug
found only while its card is shared, pages in user id order, and copies in and out (JSON round-tripped, as the database
stores them), so a test cannot change the store by mutating what it got.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from tayyari.storage.ports import StoredCardSnapshot


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _copy(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = json.loads(json.dumps(rows))
    return out


def _out(item: StoredCardSnapshot) -> StoredCardSnapshot:
    return replace(item, areas=_copy(item.areas), skills=_copy(item.skills))


class FakeCardRepo:
    def __init__(self) -> None:
        self.rows: dict[str, StoredCardSnapshot] = {}

    async def get(self, user_id: str) -> StoredCardSnapshot | None:
        found = self.rows.get(user_id)
        return None if found is None else _out(found)

    async def get_by_slug(self, slug: str) -> StoredCardSnapshot | None:
        for row in self.rows.values():
            if row.slug == slug and row.shared:
                return _out(row)
        return None

    async def save(self, item: StoredCardSnapshot) -> None:
        if item.slug is not None and any(
            row.slug == item.slug and uid != item.user_id for uid, row in self.rows.items()
        ):
            raise ValueError(f"slug {item.slug!r} is another card's")
        before = self.rows.get(item.user_id)
        self.rows[item.user_id] = replace(
            _out(item),
            created_at=_utc(before.created_at if before is not None else item.created_at),
            updated_at=_utc(item.updated_at),
        )

    async def list_page(self, after_user_id: str | None, limit: int) -> list[StoredCardSnapshot]:
        keys = sorted(k for k in self.rows if after_user_id is None or k > after_user_id)
        return [_out(self.rows[k]) for k in keys[:limit]]


__all__ = ["FakeCardRepo"]
