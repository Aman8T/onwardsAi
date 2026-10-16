"""Fakes for the Discover tests: a dict-backed `DiscoverRepo`, the hand-written board answers, and the transport that serves them.

FakeDiscoverRepo   the repository Protocol over dicts, behaving like the SQL one (the repo contract tests run both)
FakeBoards         an ``httpx2.MockTransport`` handler: Greenhouse and Lever JSON by board token, per-board failures, a
                   record of every request (so a test can prove what was and was not fetched)
fixture_registry   the fixture registry under tests/data/discover
profile_of         the two fixture profiles (a frontend engineer in Bengaluru, a B.Com fresher in Coimbatore)

Nothing here touches the network; a request nobody scripted is answered 404 and recorded.
"""

from __future__ import annotations

import dataclasses
import json
import secrets
from collections.abc import Collection, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2

from tayyari.contract.models import Profile
from tayyari.discover.registry import Registry, load_registry
from tayyari.storage.ports import DiscoverListing, DiscoverRun, UpsertCounts

DATA = Path(__file__).resolve().parents[1] / "data" / "discover"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class FakeDiscoverRepo:
    def __init__(self) -> None:
        self.listings: dict[str, DiscoverListing] = {}
        self.runs: list[DiscoverRun] = []

    # listings
    async def upsert_employer(
        self, employer_id: str, listings: Sequence[DiscoverListing], *, now: datetime
    ) -> UpsertCounts:
        now = _utc(now)
        inserted = updated = unchanged = 0
        for item in listings:
            held = self.listings.get(item.id)
            if held is None or held.employer_id != employer_id:
                self.listings[item.id] = dataclasses.replace(
                    item,
                    employer_id=employer_id,
                    first_seen_at=now,
                    last_seen_at=now,
                    status="open",
                    closed_at=None,
                )
                inserted += 1
                continue
            seen = dataclasses.replace(held, last_seen_at=now, status="open", closed_at=None)
            if held.content_hash != item.content_hash:
                seen = dataclasses.replace(
                    item,
                    employer_id=employer_id,
                    first_seen_at=held.first_seen_at,
                    last_seen_at=now,
                    status="open",
                    closed_at=None,
                )
                updated += 1
            else:
                unchanged += 1
            self.listings[item.id] = seen
        return UpsertCounts(inserted, updated, unchanged)

    async def close_missing(self, employer_id: str, seen_ids: Collection[str], *, now: datetime) -> int:
        closed = 0
        for key, item in list(self.listings.items()):
            if item.employer_id == employer_id and item.status == "open" and key not in set(seen_ids):
                self.listings[key] = dataclasses.replace(item, status="closed", closed_at=_utc(now))
                closed += 1
        return closed

    async def close_employers_except(self, employer_ids: Collection[str], *, now: datetime) -> int:
        closed = 0
        for key, item in list(self.listings.items()):
            if item.status == "open" and item.employer_id not in set(employer_ids):
                self.listings[key] = dataclasses.replace(item, status="closed", closed_at=_utc(now))
                closed += 1
        return closed

    async def list_open(self) -> list[DiscoverListing]:
        return [
            dataclasses.replace(i, posting_text=None) for i in self.listings.values() if i.status == "open"
        ]

    async def get(self, listing_id: str) -> DiscoverListing | None:
        return self.listings.get(listing_id)

    async def texts(self, listing_ids: Sequence[str]) -> dict[str, tuple[str, str]]:
        return {
            key: (item.content_hash, item.posting_text or "")
            for key in listing_ids
            if (item := self.listings.get(key)) is not None and item.status == "open"
        }

    async def counts(self) -> tuple[int, int]:
        open_rows = [i for i in self.listings.values() if i.status == "open"]
        return len({i.employer_id for i in open_rows}), len(open_rows)

    async def purge_closed(self, cutoff: datetime) -> int:
        doomed = [
            k
            for k, i in self.listings.items()
            if i.status == "closed" and i.closed_at and i.closed_at < cutoff
        ]
        for key in doomed:
            del self.listings[key]
        return len(doomed)

    # runs
    async def start_run(self, *, now: datetime, stale_after: timedelta) -> DiscoverRun | None:
        now = _utc(now)
        for index, run in enumerate(self.runs):
            if run.status == "running" and run.started_at <= now - stale_after:
                self.runs[index] = dataclasses.replace(
                    run, status="failed", finished_at=now, note="The run did not finish."
                )
        if any(run.status == "running" for run in self.runs):
            return None
        run = DiscoverRun(secrets.token_hex(16), now, None, "running", 0, 0, 0, 0, None)
        self.runs.append(run)
        return run

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
        for index, run in enumerate(self.runs):
            if run.id == run_id:
                self.runs[index] = dataclasses.replace(
                    run,
                    finished_at=_utc(now),
                    status=status,  # type: ignore[arg-type]
                    employers_ok=employers_ok,
                    employers_failed=employers_failed,
                    listings_open=listings_open,
                    listings_closed=listings_closed,
                    note=None if note is None else note[:500],
                )

    async def last_ok_run(self) -> DiscoverRun | None:
        ok = [r for r in self.runs if r.status == "ok"]
        return max(ok, key=lambda r: r.started_at) if ok else None

    async def first_run(self) -> DiscoverRun | None:
        return min(self.runs, key=lambda r: r.started_at) if self.runs else None

    async def latest_run(self) -> DiscoverRun | None:
        return max(self.runs, key=lambda r: r.started_at) if self.runs else None


# ───────────────────────── the boards ─────────────────────────


def read_json(name: str) -> Any:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class FakeBoards:
    """An ``httpx2.MockTransport`` that serves board JSON by token.

    ``serve("kestrel", payload)`` answers that board; ``fail("northwind", 503)`` makes a board answer an error status (once
    or always); ``hang`` raises a timeout. ``requests`` records every URL asked for.
    """

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self._payloads: dict[str, Any] = {}
        self._errors: dict[str, list[int | str]] = {}

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]

    def serve(self, token: str, payload: Any) -> None:
        self._payloads[token] = payload

    def fail(self, token: str, *statuses: int | str) -> None:
        """The next answers for ``token`` are these statuses (an int), or ``"timeout"``, or ``"garbage"`` (not JSON);
        after them the board is served normally again. Give the same status many times for a board that stays down."""
        self._errors[token] = list(statuses)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        parts = request.url.path.strip("/").split("/")
        token = parts[2] if "greenhouse" in request.url.host and len(parts) > 3 else parts[-1]
        errors = self._errors.get(token)
        if errors:
            kind = errors.pop(0)
            if kind == "timeout":
                raise httpx2.ReadTimeout("slow", request=request)
            if kind == "garbage":
                return httpx2.Response(200, content=b"<html>not json</html>")
            return httpx2.Response(int(kind), json={"error": "scripted"})
        if token not in self._payloads:
            return httpx2.Response(404, json={"error": "not found"})
        return httpx2.Response(200, json=self._payloads[token])


def fixture_registry() -> Registry:
    return load_registry(DATA / "registry.json")


def profile_of(name: str) -> Profile:
    """``frontend`` (Riya-like, Bengaluru) or ``fresher`` (a B.Com graduate in Coimbatore)."""
    return Profile.from_dict(read_json(f"profile-{name}.json"))


NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)

__all__ = [
    "DATA",
    "NOW",
    "FakeBoards",
    "FakeDiscoverRepo",
    "fixture_registry",
    "profile_of",
    "read_json",
]
