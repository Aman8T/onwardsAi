"""The key-value table: small bookkeeping values and generated secrets."""

from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import select

from tayyari.core.clock import Clock, SystemClock

from ..db import Database, dialect_insert
from ..tables import KvRow


class SqlKvRepo:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def get(self, key: str) -> Any | None:
        async with self._db.session() as s:
            return (await s.execute(select(KvRow.value).where(KvRow.key == key))).scalar_one_or_none()

    async def set(self, key: str, value: Any) -> None:
        stmt = dialect_insert(self._db.dialect, KvRow).values(
            key=key, value=value, updated_at=self._clock.now()
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[KvRow.key],
            set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
        )
        async with self._db.session() as s:
            await s.execute(stmt)

    async def get_or_create_secret(self, key: str, nbytes: int = 32) -> bytes:
        """Insert-if-absent, then read: concurrent callers all end up reading the one stored value."""
        if nbytes < 1:
            raise ValueError("nbytes must be at least 1")
        candidate = secrets.token_bytes(nbytes).hex()
        stmt = (
            dialect_insert(self._db.dialect, KvRow)
            .values(key=key, value=candidate, updated_at=self._clock.now())
            .on_conflict_do_nothing(index_elements=[KvRow.key])
        )
        async with self._db.session() as s:
            await s.execute(stmt)
            stored = (await s.execute(select(KvRow.value).where(KvRow.key == key))).scalar_one()
        try:
            if not isinstance(stored, str):
                raise ValueError
            return bytes.fromhex(stored)
        except ValueError:
            raise ValueError(f"kv key {key!r} does not hold a hex-encoded secret") from None


__all__ = ["SqlKvRepo"]
