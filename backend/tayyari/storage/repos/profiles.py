"""Profiles and their chat context."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError

from tayyari.contract.stored import StoredContext
from tayyari.core.clock import Clock, SystemClock
from tayyari.core.hashing import canonical_json
from tayyari.core.logging import get_logger

from ..db import Database, dialect_insert, rowcount
from ..tables import ProfileContextRow, ProfileRow, as_utc

log = get_logger(__name__)

TOUCH_INTERVAL = timedelta(hours=1)
"""``last_accessed_at`` is refreshed at most this often, so reads stay reads."""


class SqlProfileRepo:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def save(
        self,
        profile_id: str,
        profile: dict[str, Any],
        context: StoredContext,
        *,
        goal: str | None,
        owner_hash: str | None,
        is_sample: bool,
        schema_version: int = 1,
    ) -> None:
        now = self._clock.now()
        context_doc = context.to_dict()
        dialect = self._db.dialect

        profile_stmt = dialect_insert(dialect, ProfileRow).values(
            id=profile_id,
            created_at=now,
            last_accessed_at=now,
            owner_hash=owner_hash,
            is_sample=is_sample,
            schema_version=schema_version,
            goal=goal,
            profile=profile,
        )
        profile_stmt = profile_stmt.on_conflict_do_update(
            index_elements=[ProfileRow.id],
            set_={  # created_at stays: it is when the profile first existed
                "last_accessed_at": profile_stmt.excluded.last_accessed_at,
                "owner_hash": profile_stmt.excluded.owner_hash,
                "is_sample": profile_stmt.excluded.is_sample,
                "schema_version": profile_stmt.excluded.schema_version,
                "goal": profile_stmt.excluded.goal,
                "profile": profile_stmt.excluded.profile,
            },
        )
        context_stmt = dialect_insert(dialect, ProfileContextRow).values(
            profile_id=profile_id, context=context_doc, bytes=len(canonical_json(context_doc))
        )
        context_stmt = context_stmt.on_conflict_do_update(
            index_elements=[ProfileContextRow.profile_id],
            set_={"context": context_stmt.excluded.context, "bytes": context_stmt.excluded.bytes},
        )
        async with self._db.session() as s:
            await s.execute(profile_stmt)
            await s.execute(context_stmt)

    async def get_profile(self, pid: str) -> dict[str, Any] | None:
        stmt = select(ProfileRow.profile, ProfileRow.last_accessed_at).where(ProfileRow.id == pid)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            return None
        await self._touch(pid, row.last_accessed_at)
        profile: dict[str, Any] = row.profile
        return profile

    async def get_for_chat(self, pid: str) -> tuple[dict[str, Any], StoredContext] | None:
        stmt = (
            select(ProfileRow.profile, ProfileRow.last_accessed_at, ProfileContextRow.context)
            .outerjoin(ProfileContextRow, ProfileContextRow.profile_id == ProfileRow.id)
            .where(ProfileRow.id == pid)
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            return None
        await self._touch(pid, row.last_accessed_at)
        return row.profile, StoredContext.model_validate(row.context or {})

    async def purge_expired(self, cutoff: datetime) -> int:
        stmt = delete(ProfileRow).where(ProfileRow.last_accessed_at < cutoff, ProfileRow.is_sample.is_(False))
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt))

    async def _touch(self, pid: str, last_accessed_at: datetime) -> None:
        """Refresh ``last_accessed_at`` when it is over an hour old.

        A separate transaction from the read: a SQLite transaction must not upgrade from reading to
        writing, and a read that fails to record its own access is not worth failing the request for.
        """
        now = self._clock.now()
        threshold = now - TOUCH_INTERVAL
        if as_utc(last_accessed_at) > threshold:
            return
        stmt = (
            update(ProfileRow)
            .where(ProfileRow.id == pid, ProfileRow.last_accessed_at <= threshold)
            .values(last_accessed_at=now)
        )
        try:
            async with self._db.session() as s:
                await s.execute(stmt)
        except SQLAlchemyError:
            log.warning("profile_touch_failed", exc_info=True)


__all__ = ["TOUCH_INTERVAL", "SqlProfileRepo"]
