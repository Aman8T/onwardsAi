"""A dict-backed `HandoffRepo` that behaves like the SQL one (the repo contract tests run both).

What it keeps exact: `take` hands a hand-off out once and removes it (an expired one too), and `purge_expired` removes only the
rows whose ``expires_at`` is before ``now``. Reads hand out copies, so a test cannot change the store by mutating them.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from tayyari.storage.ports import StoredHandoff


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class FakeHandoffRepo:
    def __init__(self) -> None:
        self.rows: dict[str, StoredHandoff] = {}

    async def create(self, item: StoredHandoff) -> None:
        if item.id in self.rows:
            raise ValueError(f"hand-off {item.id!r} already exists")
        self.rows[item.id] = replace(
            item, links=list(item.links), created_at=_utc(item.created_at), expires_at=_utc(item.expires_at)
        )

    async def take(self, handoff_id: str) -> StoredHandoff | None:
        found = self.rows.pop(handoff_id, None)
        return None if found is None else replace(found, links=list(found.links))

    async def purge_expired(self, now: datetime) -> int:
        gone = [key for key, row in self.rows.items() if row.expires_at < _utc(now)]
        for key in gone:
            del self.rows[key]
        return len(gone)


__all__ = ["FakeHandoffRepo"]
