"""A dict-backed `ResumeFileRepo` that behaves like the SQL one (the repo contract tests run both): a second add of one id
changes nothing, reads hand out copies, files list oldest first, the bytes are read apart. Deliberate difference: no foreign
keys, so a file may be stored for an unknown profile and does not go with its profile or account."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from datetime import UTC, datetime

from tayyari.storage.ports import StoredResumeFile


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class FakeResumeFileRepo:
    def __init__(self) -> None:
        self.rows: dict[str, StoredResumeFile] = {}
        self.data: dict[str, bytes] = {}

    async def add(self, item: StoredResumeFile, data: bytes) -> StoredResumeFile:
        await asyncio.sleep(0)
        if item.id not in self.rows:
            self.rows[item.id] = copy.deepcopy(item)
            self.data[item.id] = bytes(data)
        return copy.deepcopy(self.rows[item.id])

    async def get(self, file_id: str) -> StoredResumeFile | None:
        await asyncio.sleep(0)
        found = self.rows.get(file_id)
        return None if found is None else copy.deepcopy(found)

    async def get_data(self, file_id: str) -> bytes | None:
        await asyncio.sleep(0)
        return self.data.get(file_id)

    async def list_for_profile(self, profile_id: str) -> list[StoredResumeFile]:
        await asyncio.sleep(0)
        mine = [r for r in self.rows.values() if r.profile_id == profile_id]
        mine.sort(key=lambda r: (_utc(r.created_at), r.id))
        return [copy.deepcopy(r) for r in mine]

    async def adopt(self, profile_id: str, user_id: str) -> int:
        await asyncio.sleep(0)
        changed = 0
        for fid, row in list(self.rows.items()):
            if row.profile_id == profile_id and row.user_id is None:
                self.rows[fid] = replace(row, user_id=user_id)
                changed += 1
        return changed


__all__ = ["FakeResumeFileRepo"]
