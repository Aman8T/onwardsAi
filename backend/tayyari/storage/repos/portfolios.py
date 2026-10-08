"""Portfolios: one document per profile, stored apart so the profile purge never deletes it."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, delete, func, literal, or_, select, update

from tayyari.core.clock import Clock, SystemClock
from tayyari.core.logging import get_logger

from ..db import Database, dialect_insert, rowcount
from ..ports import PortfolioConflict, StoredPortfolio
from ..tables import PortfolioRow, ProfileRow

log = get_logger(__name__)

VIEW_TOUCH_INTERVAL = timedelta(minutes=10)
"""`get_published` records the view time at most this often per page: a popular page is not a write per visit."""


def _published_at(published: bool, now: datetime) -> Any:
    """The new ``published_at`` of an UPDATE (or the DO UPDATE of an upsert) that sets ``published``.

    Unpublished: cleared. Published: the time it first went public, kept while it stays public (a
    regenerate or an edit does not move it), ``now`` when this write is the one that publishes it.
    """
    if not published:
        return None
    stamp = literal(now, type_=PortfolioRow.published_at.type)
    return case(
        (PortfolioRow.published.is_(True), func.coalesce(PortfolioRow.published_at, stamp)), else_=stamp
    )


class SqlPortfolioRepo:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def get(self, profile_id: str) -> StoredPortfolio | None:
        """The owner's portfolio (one per profile), or None."""
        stmt = select(
            PortfolioRow.profile_id,
            PortfolioRow.public_id,
            PortfolioRow.doc,
            PortfolioRow.facts_hash,
            PortfolioRow.generated,
            PortfolioRow.version,
            PortfolioRow.published,
            PortfolioRow.created_at,
            PortfolioRow.updated_at,
        ).where(PortfolioRow.profile_id == profile_id)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            return None
        return StoredPortfolio(
            profile_id=row.profile_id,
            public_id=row.public_id,
            doc=row.doc,
            facts_hash=row.facts_hash,
            generated=row.generated,
            version=row.version,
            published=row.published,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def get_published(self, public_id: str) -> StoredPortfolio | None:
        """A published portfolio by its public id (None when unknown or unpublished);
        records the view time."""
        stmt = select(
            PortfolioRow.profile_id,
            PortfolioRow.public_id,
            PortfolioRow.doc,
            PortfolioRow.facts_hash,
            PortfolioRow.generated,
            PortfolioRow.version,
            PortfolioRow.published,
            PortfolioRow.created_at,
            PortfolioRow.updated_at,
        ).where(PortfolioRow.public_id == public_id, PortfolioRow.published.is_(True))
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            return None
        await self._touch_viewed(public_id)
        return StoredPortfolio(
            profile_id=row.profile_id,
            public_id=row.public_id,
            doc=row.doc,
            facts_hash=row.facts_hash,
            generated=row.generated,
            version=row.version,
            published=row.published,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def put(self, rec: StoredPortfolio, *, expected_version: int | None) -> StoredPortfolio:
        """Insert or update; returns the stored row with its new ``version`` and ``updated_at``.

        ``expected_version=None`` creates the row, or replaces it outright when one exists (a regenerate).
        Otherwise the update applies only when the stored version equals ``expected_version`` and raises
        `PortfolioConflict` when it does not. ``public_id`` and ``created_at`` of an existing row are kept.
        """
        now = self._clock.now()

        async with self._db.session() as s:
            # Check if row exists to determine new version
            existing_stmt = select(PortfolioRow.version).where(PortfolioRow.profile_id == rec.profile_id)
            existing_row = (await s.execute(existing_stmt)).one_or_none()
            existing_version = existing_row[0] if existing_row else None

            if expected_version is None:
                # Create or regenerate: new version is 1 for new row, or existing+1 for regenerate
                new_version = 1 if existing_version is None else existing_version + 1
                # Create or regenerate: insert outright if it doesn't exist, or replace with the new doc.
                insert_stmt = dialect_insert(self._db.dialect, PortfolioRow).values(
                    profile_id=rec.profile_id,
                    public_id=rec.public_id,
                    doc=rec.doc,
                    generated=rec.generated,
                    facts_hash=rec.facts_hash,
                    version=new_version,
                    published=rec.published,
                    created_at=now,
                    updated_at=now,
                    published_at=now if rec.published else None,
                    last_viewed_at=None,
                )
                upsert_stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[PortfolioRow.profile_id],
                    set_={
                        "public_id": insert_stmt.excluded.public_id,
                        "doc": insert_stmt.excluded.doc,
                        "generated": insert_stmt.excluded.generated,
                        "facts_hash": insert_stmt.excluded.facts_hash,
                        "version": PortfolioRow.version + 1,
                        "published": insert_stmt.excluded.published,
                        "updated_at": insert_stmt.excluded.updated_at,
                        "published_at": _published_at(rec.published, now),
                        "last_viewed_at": insert_stmt.excluded.last_viewed_at,
                    },
                )
                await s.execute(upsert_stmt)
            else:
                # Optimistic update: only update if version matches, else raise PortfolioConflict.
                new_version = expected_version + 1
                update_stmt = (
                    update(PortfolioRow)
                    .where(
                        PortfolioRow.profile_id == rec.profile_id, PortfolioRow.version == expected_version
                    )
                    .values(
                        doc=rec.doc,
                        generated=rec.generated,
                        facts_hash=rec.facts_hash,
                        version=new_version,
                        published=rec.published,
                        updated_at=now,
                        published_at=_published_at(rec.published, now),
                    )
                )
                result = await s.execute(update_stmt)
                if rowcount(result) == 0:
                    # no profile id in the message: it reaches the logs, and the id is the owner's key
                    raise PortfolioConflict(f"portfolio version conflict: expected {expected_version}")

            # Re-fetch the row to get the created_at and public_id.
            fetch_stmt = select(
                PortfolioRow.profile_id,
                PortfolioRow.public_id,
                PortfolioRow.doc,
                PortfolioRow.facts_hash,
                PortfolioRow.generated,
                PortfolioRow.version,
                PortfolioRow.published,
                PortfolioRow.created_at,
                PortfolioRow.updated_at,
            ).where(PortfolioRow.profile_id == rec.profile_id)
            row = (await s.execute(fetch_stmt)).one()

        return StoredPortfolio(
            profile_id=row.profile_id,
            public_id=row.public_id,
            doc=row.doc,
            facts_hash=row.facts_hash,
            generated=row.generated,
            version=row.version,
            published=row.published,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def delete(self, profile_id: str) -> bool:
        """Remove the owner's portfolio; True when a row was deleted."""
        stmt = delete(PortfolioRow).where(PortfolioRow.profile_id == profile_id)
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt)) > 0

    async def purge_orphan_drafts(self, cutoff: datetime) -> int:
        """Delete unpublished portfolios last updated before ``cutoff`` whose profile no longer exists."""
        # Subquery: profile ids that still exist
        profiles_exist = select(ProfileRow.id)
        stmt = delete(PortfolioRow).where(
            PortfolioRow.published.is_(False),
            PortfolioRow.updated_at < cutoff,
            ~PortfolioRow.profile_id.in_(profiles_exist),
        )
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt))

    async def _touch_viewed(self, public_id: str) -> None:
        """Record that the portfolio was viewed, unless it was already recorded in the last `VIEW_TOUCH_INTERVAL`
        (the condition is part of the UPDATE, so a busy page costs a write every ten minutes, not one per visit)."""
        now = self._clock.now()
        stmt = (
            update(PortfolioRow)
            .where(
                PortfolioRow.public_id == public_id,
                or_(
                    PortfolioRow.last_viewed_at.is_(None),
                    PortfolioRow.last_viewed_at < now - VIEW_TOUCH_INTERVAL,
                ),
            )
            .values(last_viewed_at=now)
        )
        try:
            async with self._db.session() as s:
                await s.execute(stmt)
        except Exception:
            log.warning("portfolio_touch_viewed_failed", exc_info=True)


__all__ = ["SqlPortfolioRepo"]
