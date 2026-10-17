"""A dict-backed `InterviewRepo` that behaves like the SQL one (the repo contract tests run both).

What it keeps exact: a profile only ever sees its own sessions, `save` is a compare-and-swap on the version (it writes only
when the stored version is one less than the one saved), and every read hands out a copy so a test cannot change the store by
mutating what it was given.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime

from tayyari.storage.ports import StoredInterview


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class FakeInterviewRepo:
    def __init__(self) -> None:
        self.rows: dict[str, StoredInterview] = {}

    async def create(self, item: StoredInterview) -> None:
        if item.id in self.rows:
            raise ValueError(f"duplicate interview id {item.id}")
        self.rows[item.id] = copy.deepcopy(item)

    async def get(self, profile_id: str, session_id: str) -> StoredInterview | None:
        found = self.rows.get(session_id)
        return copy.deepcopy(found) if found is not None and found.profile_id == profile_id else None

    async def save(self, item: StoredInterview) -> bool:
        stored = self.rows.get(item.id)
        if stored is None or stored.profile_id != item.profile_id or stored.version != item.version - 1:
            return False
        self.rows[item.id] = replace(
            stored,
            state=copy.deepcopy(item.state),
            status=item.status,
            debrief=copy.deepcopy(item.debrief),
            updated_at=item.updated_at,
            version=item.version,
        )
        return True

    async def list(self, profile_id: str, limit: int) -> list[StoredInterview]:
        mine = [r for r in self.rows.values() if r.profile_id == profile_id]
        mine.sort(key=lambda r: _utc(r.created_at), reverse=True)
        return [copy.deepcopy(r) for r in mine[: max(0, limit)]]

    async def delete(self, profile_id: str, session_id: str) -> bool:
        found = self.rows.get(session_id)
        if found is None or found.profile_id != profile_id:
            return False
        del self.rows[session_id]
        return True

    async def count_since(self, profile_id: str, since: datetime) -> int:
        cutoff = _utc(since)
        return sum(
            1 for r in self.rows.values() if r.profile_id == profile_id and _utc(r.created_at) >= cutoff
        )

    async def purge(self, cutoff: datetime) -> int:
        limit = _utc(cutoff)
        old = [key for key, row in self.rows.items() if _utc(row.created_at) < limit]
        for key in old:
            del self.rows[key]
        return len(old)


__all__ = ["FakeInterviewRepo"]
