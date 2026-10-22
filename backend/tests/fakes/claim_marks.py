"""A dict-backed `ClaimMarkRepo` that behaves like the SQL one (the repo contract tests run both).

What it keeps exact: a profile only ever sees and changes its own marks, `add` skips a ``(profile, session, slot)`` already
stored (leaving its Fix step's place and tick alone), the orders (oldest mark first; oldest placed Fix step first), the Fix
step rules (only a mark with a step can be placed; only a placed step can be ticked or cleared), and every read hands out a
copy so a test cannot change the store by mutating what it was given.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

from tayyari.storage.ports import StoredClaimMark


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _placed(m: StoredClaimMark) -> bool:
    return m.fix_plan_id is not None or m.fix_kept


def _placed_order(m: StoredClaimMark) -> tuple[datetime, str]:
    return (_utc(m.fix_added_at) if m.fix_added_at is not None else datetime.min.replace(tzinfo=UTC), m.id)


class FakeClaimMarkRepo:
    def __init__(self) -> None:
        self.rows: dict[str, StoredClaimMark] = {}

    def _mine(self, profile_id: str) -> list[StoredClaimMark]:
        return [m for m in self.rows.values() if m.profile_id == profile_id]

    def _own(self, profile_id: str, mark_id: str) -> StoredClaimMark | None:
        found = self.rows.get(mark_id)
        return found if found is not None and found.profile_id == profile_id else None

    async def add(self, items: Sequence[StoredClaimMark]) -> int:
        added = 0
        for item in items:
            taken = any(
                m.profile_id == item.profile_id
                and m.session_id == item.session_id
                and m.slot_id == item.slot_id
                for m in self.rows.values()
            )
            if taken:
                continue
            if item.id in self.rows:
                raise ValueError(f"duplicate claim mark id {item.id}")
            self.rows[item.id] = copy.deepcopy(item)
            added += 1
        return added

    async def list_for_profile(self, profile_id: str, limit: int) -> list[StoredClaimMark]:
        mine = sorted(self._mine(profile_id), key=lambda m: (m.marked_on, _utc(m.created_at), m.id))
        return [copy.deepcopy(m) for m in mine[:limit]]

    async def session_ids(self, profile_id: str) -> set[str]:
        return {m.session_id for m in self._mine(profile_id)}

    async def get(self, profile_id: str, mark_id: str) -> StoredClaimMark | None:
        found = self._own(profile_id, mark_id)
        return copy.deepcopy(found) if found is not None else None

    async def find(self, profile_id: str, session_id: str, slot_id: str) -> StoredClaimMark | None:
        for m in self._mine(profile_id):
            if m.session_id == session_id and m.slot_id == slot_id:
                return copy.deepcopy(m)
        return None

    async def place_fix(self, profile_id: str, mark_id: str, *, plan_id: str | None, now: datetime) -> bool:
        found = self._own(profile_id, mark_id)
        if found is None or found.fix_text is None:
            return False
        self.rows[mark_id] = replace(found, fix_plan_id=plan_id, fix_kept=plan_id is None, fix_added_at=now)
        return True

    async def set_fix_done(self, profile_id: str, mark_id: str, done_at: datetime | None) -> bool:
        found = self._own(profile_id, mark_id)
        if found is None or not _placed(found):
            return False
        self.rows[mark_id] = replace(found, fix_done_at=done_at)
        return True

    async def clear_fix(self, profile_id: str, mark_id: str) -> bool:
        found = self._own(profile_id, mark_id)
        if found is None or not _placed(found):
            return False
        self.rows[mark_id] = replace(
            found, fix_plan_id=None, fix_kept=False, fix_added_at=None, fix_done_at=None
        )
        return True

    async def fixes_for_plan(self, profile_id: str, plan_id: str) -> list[StoredClaimMark]:
        mine = sorted((m for m in self._mine(profile_id) if m.fix_plan_id == plan_id), key=_placed_order)
        return [copy.deepcopy(m) for m in mine]

    async def kept_fixes(self, profile_id: str) -> list[StoredClaimMark]:
        mine = sorted((m for m in self._mine(profile_id) if m.fix_kept), key=_placed_order)
        return [copy.deepcopy(m) for m in mine]

    async def delete_for_session(self, profile_id: str, session_id: str) -> int:
        gone = [m.id for m in self._mine(profile_id) if m.session_id == session_id]
        for mark_id in gone:
            del self.rows[mark_id]
        return len(gone)


__all__ = ["FakeClaimMarkRepo"]
