"""Plans for roles (`prepare_plans`).

One row per plan, scoped to a profile: another profile's plan id reads as nothing, whatever the method. `save_state` is a
compare-and-swap on ``version``: it lands only when the stored version is the one the client last saw, so two tabs ticking
tasks at once cannot overwrite each other (the loser gets False and the route answers 409). `find` returns the newest plan
for a role (and requirement), which is what the stream hands back instead of writing a new one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, update

from ..db import Database, rowcount
from ..ports import StoredPlan
from ..tables import PreparePlanRow


def _item(row: Any) -> StoredPlan:
    return StoredPlan(
        id=row.id,
        profile_id=row.profile_id,
        role_id=row.role_id,
        requirement=row.requirement,
        content=dict(row.content or {}),
        state=dict(row.state or {}),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlPrepareRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, item: StoredPlan) -> None:
        async with self._db.session() as s:
            await s.execute(
                insert(PreparePlanRow).values(
                    id=item.id,
                    profile_id=item.profile_id,
                    role_id=item.role_id,
                    requirement=item.requirement,
                    content=item.content,
                    state=item.state,
                    version=item.version,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
            )

    async def get(self, profile_id: str, plan_id: str) -> StoredPlan | None:
        stmt = select(PreparePlanRow).where(
            PreparePlanRow.id == plan_id, PreparePlanRow.profile_id == profile_id
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _item(row)

    async def find(self, profile_id: str, role_id: str, requirement: str | None) -> StoredPlan | None:
        stmt = select(PreparePlanRow).where(
            PreparePlanRow.profile_id == profile_id,
            PreparePlanRow.role_id == role_id,
            PreparePlanRow.requirement.is_(None)
            if requirement is None
            else PreparePlanRow.requirement == requirement,
        )
        stmt = stmt.order_by(PreparePlanRow.created_at.desc(), PreparePlanRow.id.desc()).limit(1)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().first()
        return None if row is None else _item(row)

    async def list_for_profile(self, profile_id: str, limit: int) -> list[StoredPlan]:
        stmt = (
            select(PreparePlanRow)
            .where(PreparePlanRow.profile_id == profile_id)
            .order_by(PreparePlanRow.created_at.desc(), PreparePlanRow.id.desc())
            .limit(limit)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_item(row) for row in rows]

    async def save_state(
        self,
        profile_id: str,
        plan_id: str,
        *,
        state: dict[str, Any],
        expected_version: int,
        now: datetime,
    ) -> bool:
        async with self._db.session() as s:
            result = await s.execute(
                update(PreparePlanRow)
                .where(
                    PreparePlanRow.id == plan_id,
                    PreparePlanRow.profile_id == profile_id,
                    PreparePlanRow.version == expected_version,
                )
                .values(state=state, version=expected_version + 1, updated_at=now)
            )
            return rowcount(result) == 1


__all__ = ["SqlPrepareRepo"]
