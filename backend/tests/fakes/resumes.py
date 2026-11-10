"""A dict-backed `ResumeRepo` that behaves like the SQL one (the repo contract tests run both).

What it keeps exact: a profile has at most one resume of each ``(kind, role_key)`` and a second `create` hands back the
first, `save` is a compare-and-swap on the version and never touches the owner, the title or the default, `set_default`
moves the profile's one default and only onto a resume with a finalised version, and every read hands out a copy so a
test cannot change the store by mutating what it was given. Deliberate difference: no foreign keys, so a resume may be
stored for an unknown profile and does not go with its profile or account.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from datetime import UTC, datetime

from tayyari.storage.ports import ResumeKind, StoredResume


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class FakeResumeRepo:
    def __init__(self) -> None:
        self.rows: dict[str, StoredResume] = {}

    def _find(self, profile_id: str, kind: ResumeKind, role_key: str) -> StoredResume | None:
        return next(
            (
                r
                for r in self.rows.values()
                if r.profile_id == profile_id and r.kind == kind and r.role_key == role_key
            ),
            None,
        )

    async def create(self, item: StoredResume) -> StoredResume:
        await asyncio.sleep(0)
        existing = self._find(item.profile_id, item.kind, item.role_key)
        if existing is not None:
            return copy.deepcopy(existing)
        if item.id in self.rows:
            raise ValueError(f"duplicate resume id {item.id}")
        self.rows[item.id] = copy.deepcopy(item)
        return copy.deepcopy(item)

    async def get(self, resume_id: str) -> StoredResume | None:
        await asyncio.sleep(0)
        found = self.rows.get(resume_id)
        return None if found is None else copy.deepcopy(found)

    async def find(self, profile_id: str, kind: ResumeKind, role_key: str) -> StoredResume | None:
        await asyncio.sleep(0)
        found = self._find(profile_id, kind, role_key)
        return None if found is None else copy.deepcopy(found)

    async def list_for_profile(self, profile_id: str) -> list[StoredResume]:
        await asyncio.sleep(0)
        mine = [r for r in self.rows.values() if r.profile_id == profile_id]
        mine.sort(key=lambda r: (_utc(r.created_at), r.id))
        return [copy.deepcopy(r) for r in mine]

    async def save(self, item: StoredResume, *, expected_version: int) -> bool:
        await asyncio.sleep(0)
        stored = self.rows.get(item.id)
        if stored is None or stored.version != expected_version:
            return False
        self.rows[item.id] = replace(
            stored,
            draft=copy.deepcopy(item.draft),
            final=copy.deepcopy(item.final),
            facts=copy.deepcopy(item.facts),
            finalised_at=item.finalised_at,
            updated_at=item.updated_at,
            version=item.version,
        )
        return True

    async def set_default(self, profile_id: str, resume_id: str) -> bool:
        await asyncio.sleep(0)
        chosen = self.rows.get(resume_id)
        if chosen is None or chosen.profile_id != profile_id or chosen.final is None:
            return False
        for rid, row in list(self.rows.items()):
            if row.profile_id != profile_id:
                continue
            if rid == resume_id:
                self.rows[rid] = replace(row, is_default=True)
            elif row.is_default:
                self.rows[rid] = replace(row, is_default=False)
        return True

    async def adopt(self, profile_id: str, user_id: str) -> int:
        await asyncio.sleep(0)
        changed = 0
        for rid, row in list(self.rows.items()):
            if row.profile_id == profile_id and row.user_id is None:
                self.rows[rid] = replace(row, user_id=user_id)
                changed += 1
        return changed


__all__ = ["FakeResumeRepo"]
