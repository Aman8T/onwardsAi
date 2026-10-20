"""Discover's listings and refresh runs.

Every write is one short transaction that starts with a write (a SQLite reader that later writes can fail at once with
"database is locked" when another connection committed in between), so a refresh in the web app and the worker process
can share one database. Single flight (`start_run`) is decided inside the transaction: on Postgres behind an advisory
lock, on SQLite by the write lock the first statement takes.

Listings are written per employer, and an employer's listings are closed only by `close_missing` after its board was
read: a board that failed leaves its rows as they were.
"""

from __future__ import annotations

import secrets
from collections.abc import Collection, Sequence
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tayyari.core.clock import Clock, SystemClock

from ..db import Database, rowcount
from ..ports import DiscoverListing, DiscoverRun, UpsertCounts
from ..tables import DiscoverListingRow, DiscoverRunRow

ADVISORY_LOCK_KEY: Final = 0x5441595941524933
"""``"TAYYARI3"``: the Postgres advisory lock that makes starting a run atomic across replicas."""
ABANDONED_NOTE: Final = "The run did not finish."

_LIST_COLUMNS = (
    DiscoverListingRow.id,
    DiscoverListingRow.employer_id,
    DiscoverListingRow.source,
    DiscoverListingRow.external_id,
    DiscoverListingRow.title,
    DiscoverListingRow.title_norm,
    DiscoverListingRow.work_mode,
    DiscoverListingRow.city,
    DiscoverListingRow.locality,
    DiscoverListingRow.lat,
    DiscoverListingRow.lng,
    DiscoverListingRow.precision,
    DiscoverListingRow.remote_india,
    DiscoverListingRow.location_text,
    DiscoverListingRow.apply_url,
    DiscoverListingRow.apply_host,
    DiscoverListingRow.posted_at,
    DiscoverListingRow.first_seen_at,
    DiscoverListingRow.last_seen_at,
    DiscoverListingRow.status,
    DiscoverListingRow.closed_at,
    DiscoverListingRow.salary_text,
    DiscoverListingRow.years_min,
    DiscoverListingRow.entry_level,
    DiscoverListingRow.content_hash,
)
_CONTENT_FIELDS = (
    "source",
    "external_id",
    "title",
    "title_norm",
    "work_mode",
    "city",
    "locality",
    "lat",
    "lng",
    "precision",
    "remote_india",
    "location_text",
    "apply_url",
    "apply_host",
    "posted_at",
    "salary_text",
    "years_min",
    "entry_level",
    "content_hash",
    "posting_text",
)


def _listing(row: Any, text_: str | None = None) -> DiscoverListing:
    return DiscoverListing(
        id=row.id,
        employer_id=row.employer_id,
        source=row.source,
        external_id=row.external_id,
        title=row.title,
        title_norm=row.title_norm,
        work_mode=row.work_mode,
        city=row.city,
        locality=row.locality,
        lat=row.lat,
        lng=row.lng,
        precision=row.precision,
        remote_india=row.remote_india,
        location_text=row.location_text,
        apply_url=row.apply_url,
        apply_host=row.apply_host,
        posted_at=row.posted_at,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        status=row.status,
        closed_at=row.closed_at,
        salary_text=row.salary_text,
        years_min=row.years_min,
        entry_level=row.entry_level,
        content_hash=row.content_hash,
        posting_text=text_,
    )


def _run(row: Any) -> DiscoverRun:
    return DiscoverRun(
        id=row.id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        status=row.status,
        employers_ok=row.employers_ok,
        employers_failed=row.employers_failed,
        listings_open=row.listings_open,
        listings_closed=row.listings_closed,
        note=row.note,
    )


def _values(listing: DiscoverListing) -> dict[str, Any]:
    """The columns a refresh writes for a listing (everything but the id and the seen/closed bookkeeping)."""
    return {name: getattr(listing, name) for name in _CONTENT_FIELDS}


class SqlDiscoverRepo:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    # ───────────────────────── listings ─────────────────────────

    async def upsert_employer(
        self, employer_id: str, listings: Sequence[DiscoverListing], *, now: datetime
    ) -> UpsertCounts:
        if not listings:
            return UpsertCounts(0, 0, 0)
        by_id = {item.id: item for item in listings}
        ids = list(by_id)
        async with self._db.session() as s:
            # The write comes first: it marks every known listing seen and open (and takes the SQLite write lock).
            await s.execute(
                update(DiscoverListingRow)
                .where(DiscoverListingRow.employer_id == employer_id, DiscoverListingRow.id.in_(ids))
                .values(last_seen_at=now, status="open", closed_at=None)
            )
            known = {
                row.id: row.content_hash
                for row in (
                    await s.execute(
                        select(DiscoverListingRow.id, DiscoverListingRow.content_hash).where(
                            DiscoverListingRow.employer_id == employer_id, DiscoverListingRow.id.in_(ids)
                        )
                    )
                )
            }
            new = [item for item in by_id.values() if item.id not in known]
            changed = [
                item for item in by_id.values() if item.id in known and known[item.id] != item.content_hash
            ]
            if new:
                await s.execute(
                    insert(DiscoverListingRow),
                    [
                        {
                            "id": item.id,
                            "employer_id": employer_id,
                            "first_seen_at": now,
                            "last_seen_at": now,
                            "status": "open",
                            "closed_at": None,
                            **_values(item),
                        }
                        for item in new
                    ],
                )
            for item in changed:
                await s.execute(
                    update(DiscoverListingRow).where(DiscoverListingRow.id == item.id).values(**_values(item))
                )
        return UpsertCounts(len(new), len(changed), len(by_id) - len(new) - len(changed))

    async def close_missing(self, employer_id: str, seen_ids: Collection[str], *, now: datetime) -> int:
        stmt = (
            update(DiscoverListingRow)
            .where(DiscoverListingRow.employer_id == employer_id, DiscoverListingRow.status == "open")
            .values(status="closed", closed_at=now)
        )
        if seen_ids:
            stmt = stmt.where(DiscoverListingRow.id.not_in(list(seen_ids)))
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt))

    async def close_employers_except(self, employer_ids: Collection[str], *, now: datetime) -> int:
        stmt = (
            update(DiscoverListingRow)
            .where(DiscoverListingRow.status == "open")
            .values(status="closed", closed_at=now)
        )
        if employer_ids:
            stmt = stmt.where(DiscoverListingRow.employer_id.not_in(list(employer_ids)))
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt))

    async def list_open(self) -> list[DiscoverListing]:
        stmt = select(*_LIST_COLUMNS).where(DiscoverListingRow.status == "open")
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).all()
        return [_listing(row) for row in rows]

    async def list_open_since(self, since: datetime) -> list[DiscoverListing]:
        stmt = (
            select(*_LIST_COLUMNS)
            .where(DiscoverListingRow.status == "open", DiscoverListingRow.first_seen_at >= since)
            .order_by(DiscoverListingRow.first_seen_at.desc(), DiscoverListingRow.id)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).all()
        return [_listing(row) for row in rows]

    async def get(self, listing_id: str) -> DiscoverListing | None:
        stmt = select(*_LIST_COLUMNS, DiscoverListingRow.posting_text).where(
            DiscoverListingRow.id == listing_id
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        return None if row is None else _listing(row, row.posting_text)

    async def texts(self, listing_ids: Sequence[str]) -> dict[str, tuple[str, str]]:
        if not listing_ids:
            return {}
        stmt = select(
            DiscoverListingRow.id, DiscoverListingRow.content_hash, DiscoverListingRow.posting_text
        ).where(DiscoverListingRow.id.in_(list(listing_ids)), DiscoverListingRow.status == "open")
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).all()
        return {row.id: (row.content_hash, row.posting_text) for row in rows}

    async def counts(self) -> tuple[int, int]:
        stmt = select(func.count(func.distinct(DiscoverListingRow.employer_id)), func.count()).where(
            DiscoverListingRow.status == "open"
        )
        async with self._db.session() as s:
            employers, listings = (await s.execute(stmt)).one()
        return int(employers), int(listings)

    async def purge_closed(self, cutoff: datetime) -> int:
        stmt = delete(DiscoverListingRow).where(
            DiscoverListingRow.status == "closed", DiscoverListingRow.closed_at < cutoff
        )
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt))

    # ───────────────────────── runs ─────────────────────────

    async def start_run(self, *, now: datetime, stale_after: timedelta) -> DiscoverRun | None:
        cutoff = now - stale_after
        run_id = secrets.token_hex(16)
        async with self._db.session() as s:
            await self._lock(s)
            # Runs that were abandoned (a crashed process) stop counting as running; this is also the first write.
            await s.execute(
                update(DiscoverRunRow)
                .where(DiscoverRunRow.status == "running", DiscoverRunRow.started_at <= cutoff)
                .values(status="failed", finished_at=now, note=ABANDONED_NOTE)
            )
            running = (
                await s.execute(
                    select(func.count()).select_from(DiscoverRunRow).where(DiscoverRunRow.status == "running")
                )
            ).scalar_one()
            if running:
                return None
            s.add(
                DiscoverRunRow(
                    id=run_id,
                    started_at=now,
                    finished_at=None,
                    status="running",
                    employers_ok=0,
                    employers_failed=0,
                    listings_open=0,
                    listings_closed=0,
                    note=None,
                )
            )
        return DiscoverRun(run_id, now, None, "running", 0, 0, 0, 0, None)

    async def _lock(self, s: AsyncSession) -> None:
        if self._db.dialect == "postgresql":
            await s.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADVISORY_LOCK_KEY})

    async def finish_run(
        self,
        run_id: str,
        *,
        now: datetime,
        status: str,
        employers_ok: int,
        employers_failed: int,
        listings_open: int,
        listings_closed: int,
        note: str | None,
    ) -> None:
        stmt = (
            update(DiscoverRunRow)
            .where(DiscoverRunRow.id == run_id)
            .values(
                finished_at=now,
                status=status,
                employers_ok=employers_ok,
                employers_failed=employers_failed,
                listings_open=listings_open,
                listings_closed=listings_closed,
                note=None if note is None else note[:500],
            )
        )
        async with self._db.session() as s:
            await s.execute(stmt)

    async def last_ok_run(self) -> DiscoverRun | None:
        stmt = (
            select(DiscoverRunRow)
            .where(DiscoverRunRow.status == "ok")
            .order_by(DiscoverRunRow.started_at.desc())
            .limit(1)
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _run(row)

    async def latest_run(self) -> DiscoverRun | None:
        stmt = select(DiscoverRunRow).order_by(DiscoverRunRow.started_at.desc()).limit(1)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _run(row)

    async def first_run(self) -> DiscoverRun | None:
        stmt = select(DiscoverRunRow).order_by(DiscoverRunRow.started_at.asc()).limit(1)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _run(row)


__all__ = ["ABANDONED_NOTE", "ADVISORY_LOCK_KEY", "SqlDiscoverRepo"]
