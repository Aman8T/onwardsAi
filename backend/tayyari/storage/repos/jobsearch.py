"""Searches with AI (`jobsearch_runs`) and the prep pages and referral notes written for their roles (`jobsearch_docs`).

Each write is one short transaction. A document's id is a hash of (profile, role, kind), so writing it again replaces it
(delete then insert in one transaction). Nothing here is shared between profiles: the route checks that a run belongs to
the profile that asks before anything is read back.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, insert, select

from tayyari.core.hashing import sha256_hex

from ..db import Database, rowcount
from ..ports import StoredSearchDoc, StoredSearchRun
from ..tables import JobSearchDocRow, JobSearchRunRow


def doc_id(profile_id: str, job_id: str, kind: str) -> str:
    """The id of a profile's document of ``kind`` for one role."""
    return sha256_hex(f"{profile_id}\x1f{job_id}\x1f{kind}")[:64]


def _run(row: Any) -> StoredSearchRun:
    return StoredSearchRun(
        id=row.id,
        profile_id=row.profile_id,
        city=row.city,
        remote=row.remote,
        created_at=row.created_at,
        result=dict(row.result or {}),
        postings=dict(row.postings or {}),
    )


class SqlJobSearchRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_run(self, run: StoredSearchRun) -> None:
        async with self._db.session() as s:
            await s.execute(
                insert(JobSearchRunRow).values(
                    id=run.id,
                    profile_id=run.profile_id,
                    city=run.city,
                    remote=run.remote,
                    created_at=run.created_at,
                    result=run.result,
                    postings=run.postings,
                )
            )

    async def latest_run(self, profile_id: str, city: str | None, remote: bool) -> StoredSearchRun | None:
        stmt = select(JobSearchRunRow).where(
            JobSearchRunRow.profile_id == profile_id,
            JobSearchRunRow.remote == remote,
            JobSearchRunRow.city.is_(None) if city is None else JobSearchRunRow.city == city,
        )
        stmt = stmt.order_by(JobSearchRunRow.created_at.desc()).limit(1)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalars().one_or_none()
        return None if row is None else _run(row)

    async def get_run(self, run_id: str) -> StoredSearchRun | None:
        async with self._db.session() as s:
            row = await s.get(JobSearchRunRow, run_id)
            return None if row is None else _run(row)

    async def get_doc(self, profile_id: str, job_id: str, kind: str) -> StoredSearchDoc | None:
        async with self._db.session() as s:
            row = await s.get(JobSearchDocRow, doc_id(profile_id, job_id, kind))
            if row is None:
                return None
            return StoredSearchDoc(
                profile_id=row.profile_id,
                job_id=row.job_id,
                kind=row.kind,
                created_at=row.created_at,
                body=dict(row.body or {}),
            )

    async def save_doc(self, doc: StoredSearchDoc) -> None:
        key = doc_id(doc.profile_id, doc.job_id, doc.kind)
        async with self._db.session() as s:
            await s.execute(delete(JobSearchDocRow).where(JobSearchDocRow.id == key))
            await s.execute(
                insert(JobSearchDocRow).values(
                    id=key,
                    profile_id=doc.profile_id,
                    job_id=doc.job_id,
                    kind=doc.kind,
                    created_at=doc.created_at,
                    body=doc.body,
                )
            )

    async def purge(self, cutoff: datetime) -> int:
        async with self._db.session() as s:
            runs = rowcount(
                await s.execute(delete(JobSearchRunRow).where(JobSearchRunRow.created_at < cutoff))
            )
            docs = rowcount(
                await s.execute(delete(JobSearchDocRow).where(JobSearchDocRow.created_at < cutoff))
            )
        return runs + docs


__all__ = ["SqlJobSearchRepo", "doc_id"]
