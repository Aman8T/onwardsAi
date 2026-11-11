"""A dict-backed `DeskNoteRepo` that behaves like the SQL one (scoped by account and profile, replace is the whole list)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from tayyari.storage.ports import StoredDeskNote


class FakeDeskNoteRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, StoredDeskNote]] = {}

    async def list(self, user_id: str, profile_id: str) -> list[StoredDeskNote]:
        found = self.rows.get((user_id, profile_id), {})
        return sorted(found.values(), key=lambda n: (-n.at, n.id))

    async def replace(
        self, user_id: str, profile_id: str, notes: Sequence[StoredDeskNote], now: datetime
    ) -> None:
        self.rows[(user_id, profile_id)] = {n.id: n for n in notes}


__all__ = ["FakeDeskNoteRepo"]
