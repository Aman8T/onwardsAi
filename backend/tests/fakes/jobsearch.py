"""A dict-backed `JobSearchRepo` that behaves like the SQL one (the repo contract tests run both)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

from tayyari.storage.ports import StoredSearchDoc, StoredSearchRun


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class FakeJobSearchRepo:
    def __init__(self) -> None:
        self.runs: dict[str, StoredSearchRun] = {}
        self.docs: dict[tuple[str, str, str], StoredSearchDoc] = {}

    async def save_run(self, run: StoredSearchRun) -> None:
        if run.id in self.runs:
            raise ValueError(f"duplicate run id {run.id}")
        self.runs[run.id] = copy.deepcopy(run)

    async def latest_run(self, profile_id: str, city: str | None, remote: bool) -> StoredSearchRun | None:
        found = [
            r
            for r in self.runs.values()
            if r.profile_id == profile_id and r.city == city and r.remote == remote
        ]
        return copy.deepcopy(max(found, key=lambda r: _utc(r.created_at))) if found else None

    async def get_run(self, run_id: str) -> StoredSearchRun | None:
        found = self.runs.get(run_id)
        return copy.deepcopy(found) if found else None

    async def get_doc(self, profile_id: str, job_id: str, kind: str) -> StoredSearchDoc | None:
        found = self.docs.get((profile_id, job_id, kind))
        return copy.deepcopy(found) if found else None

    async def save_doc(self, doc: StoredSearchDoc) -> None:
        self.docs[(doc.profile_id, doc.job_id, doc.kind)] = copy.deepcopy(doc)

    async def purge(self, cutoff: datetime) -> int:
        cutoff = _utc(cutoff)
        old_runs = [key for key, run in self.runs.items() if _utc(run.created_at) < cutoff]
        old_docs = [key for key, doc in self.docs.items() if _utc(doc.created_at) < cutoff]
        for key in old_runs:
            del self.runs[key]
        for doc_key in old_docs:
            del self.docs[doc_key]
        return len(old_runs) + len(old_docs)


__all__ = ["FakeJobSearchRepo"]
