"""A dict-backed `PrepareRepo` that behaves like the SQL one (the repo contract tests run both).

What it keeps exact: a profile only ever sees its own plans, `find` answers the newest plan for a role (and requirement),
`save_state` is a compare-and-swap on the version (it writes only when the stored version is the one the client last saw),
and every read hands out a copy so a test cannot change the store by mutating what it was given.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from tayyari.storage.ports import StoredPlan


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class FakePrepareRepo:
    def __init__(self) -> None:
        self.rows: dict[str, StoredPlan] = {}

    async def create(self, item: StoredPlan) -> None:
        if item.id in self.rows:
            raise ValueError(f"duplicate plan id {item.id}")
        self.rows[item.id] = copy.deepcopy(item)

    async def get(self, profile_id: str, plan_id: str) -> StoredPlan | None:
        found = self.rows.get(plan_id)
        return copy.deepcopy(found) if found is not None and found.profile_id == profile_id else None

    async def find(self, profile_id: str, role_id: str, requirement: str | None) -> StoredPlan | None:
        mine = [
            r
            for r in self.rows.values()
            if r.profile_id == profile_id and r.role_id == role_id and r.requirement == requirement
        ]
        if not mine:
            return None
        mine.sort(key=lambda r: (_utc(r.created_at), r.id), reverse=True)
        return copy.deepcopy(mine[0])

    async def list_for_profile(self, profile_id: str, limit: int) -> list[StoredPlan]:
        mine = [r for r in self.rows.values() if r.profile_id == profile_id]
        mine.sort(key=lambda r: (_utc(r.created_at), r.id), reverse=True)
        return [copy.deepcopy(r) for r in mine[:limit]]

    async def save_state(
        self,
        profile_id: str,
        plan_id: str,
        *,
        state: dict[str, Any],
        expected_version: int,
        now: datetime,
    ) -> bool:
        stored = self.rows.get(plan_id)
        if stored is None or stored.profile_id != profile_id or stored.version != expected_version:
            return False
        self.rows[plan_id] = replace(
            stored, state=copy.deepcopy(state), version=expected_version + 1, updated_at=now
        )
        return True


__all__ = ["FakePrepareRepo"]
