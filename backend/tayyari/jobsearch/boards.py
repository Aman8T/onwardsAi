"""Reading the boards the scouts' searches led to: the same public, keyless APIs and the same politeness as Discover's
refresh (`discover.refresh.board_client` and `fetch_board`), the same parsing (`discover.parse.parse_board`, India
listings only), so a role found on the web is held to exactly the rules of a role Discover lists.

A board is read at most once per ``board_cache_minutes`` in this process: a second search a few minutes later reuses
the first read. Nothing is stored. A board that cannot be read is reported and skipped; it never stops the search.

An employer found on the web is not in Discover's registry, so its name comes from the board (Greenhouse gives
``company_name``) or from the result title, and it has no known offices: a listing is placed by its location text only,
never by a guess.
"""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

import httpx2

from tayyari.core.clock import Clock
from tayyari.core.logging import get_logger
from tayyari.discover.errors import BoardError
from tayyari.discover.parse import parse_board
from tayyari.discover.refresh import board_client, board_url_for, fetch_board
from tayyari.discover.registry import Employer, Registry
from tayyari.storage.ports import DiscoverListing

from .harvest import BoardLead, BoardRef

log = get_logger(__name__)

CACHE_SIZE: Final = 128
NAME_MAX_CHARS: Final = 80
_ID_CHARS = re.compile(r"[^a-z0-9]+")
_BOARD_PAGES: Final = {
    "greenhouse": "https://job-boards.greenhouse.io/{token}",
    "lever": "https://jobs.lever.co/{token}",
}


@dataclass(frozen=True, slots=True)
class BoardRead:
    """One board as read for a search: the employer, its open listings in India (with their text), or why not."""

    lead: BoardLead
    employer: Employer
    listings: tuple[DiscoverListing, ...] = ()
    seen: int = 0
    """Jobs on the board, anywhere."""
    error: str | None = None
    cached: bool = False


def employer_id_of(ref: BoardRef) -> str:
    """An id for an employer found on the web: ``web-`` and the board token, in the registry's id alphabet."""
    tail = _ID_CHARS.sub("-", ref.token.lower()).strip("-")[:56] or "board"
    return f"web-{ref.ats}-{tail}"[:63]


def pretty_name(slug: str) -> str:
    """A readable employer name from a board slug when nobody names the employer: ``kestrel-pay`` is "Kestrel Pay".

    A board that shares its name with another employer gets a number on the end (``appen-2``): that is the board's, not
    the employer's, so trailing all-digit segments are left out ("Appen"). A slug of nothing but digits keeps them.
    """
    parts = [part for part in re.split(r"[-_.]+", slug) if part]
    while len(parts) > 1 and parts[-1].isdigit():
        parts.pop()
    return " ".join(part.capitalize() for part in parts) or slug


def _company_name(payload: Any) -> str | None:
    """Greenhouse names the company on every job; Lever does not name it at all."""
    if isinstance(payload, Mapping):
        jobs = payload.get("jobs")
        if isinstance(jobs, list):
            for job in jobs[:5]:
                name = job.get("company_name") if isinstance(job, Mapping) else None
                if isinstance(name, str) and name.strip():
                    return name.strip()[:NAME_MAX_CHARS]
    return None


class BoardReader:
    def __init__(
        self,
        *,
        clock: Clock,
        timeout_s: float,
        concurrency: int,
        cache_minutes: float,
        listing_cap: int,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._clock = clock
        self._timeout_s = timeout_s
        self._concurrency = max(1, concurrency)
        self._ttl = timedelta(minutes=cache_minutes)
        self._cap = listing_cap
        self._transport = transport
        self._cache: OrderedDict[
            tuple[str, str], tuple[datetime, Employer, tuple[DiscoverListing, ...], int]
        ] = OrderedDict()

    def employer_for(self, lead: BoardLead, registry: Registry) -> Employer | None:
        """The registry's employer for this board, when Discover already reads it."""
        for employer in registry.employers:
            if (employer.ats, employer.board.lower()) == lead.ref.key:
                return employer
        return None

    async def read(self, leads: Sequence[BoardLead]) -> list[BoardRead]:
        """Read every lead's board (at most `concurrency` at once), in the order of ``leads``."""
        if not leads:
            return []
        gate = asyncio.Semaphore(self._concurrency)
        async with board_client(timeout_s=self._timeout_s, transport=self._transport) as client:
            return list(await asyncio.gather(*(self._one(client, gate, lead) for lead in leads)))

    def _cached(self, lead: BoardLead) -> BoardRead | None:
        held = self._cache.get(lead.ref.key)
        if held is None:
            return None
        at, employer, listings, seen = held
        if self._clock.now() - at > self._ttl:
            del self._cache[lead.ref.key]
            return None
        self._cache.move_to_end(lead.ref.key)
        return BoardRead(lead=lead, employer=employer, listings=listings, seen=seen, cached=True)

    def _employer(self, lead: BoardLead, payload: Any) -> Employer:
        ref = lead.ref
        name = (
            _company_name(payload)
            or (lead.name_hint or "").strip()[:NAME_MAX_CHARS]
            or pretty_name(ref.token)
        )
        return Employer(
            id=employer_id_of(ref),
            name=name,
            ats=ref.ats,
            board=ref.token,
            careers_url=_BOARD_PAGES[ref.ats].format(token=ref.token),
            offices=(),
        )

    async def _one(self, client: httpx2.AsyncClient, gate: asyncio.Semaphore, lead: BoardLead) -> BoardRead:
        held = self._cached(lead)
        if held is not None:
            return held
        fallback = self._employer(lead, None)
        async with gate:
            try:
                payload = await fetch_board(client, board_url_for(lead.ref.ats, lead.ref.token), self._clock)
                employer = self._employer(lead, payload)
                now = self._clock.now()
                parsed = await asyncio.to_thread(parse_board, employer, payload, now=now, cap=self._cap)
                del payload
            except BoardError as exc:
                log.info("jobsearch_board_failed", ats=lead.ref.ats, board=lead.ref.token, reason=str(exc))
                return BoardRead(lead=lead, employer=fallback, error=str(exc))
            except Exception as exc:  # one odd board must never stop a search
                log.warning("jobsearch_board_crashed", ats=lead.ref.ats, board=lead.ref.token, exc_info=True)
                return BoardRead(
                    lead=lead, employer=fallback, error=f"could not be read ({type(exc).__name__})"
                )
        listings = tuple(parsed.listings)
        self._cache[lead.ref.key] = (now, employer, listings, parsed.seen)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return BoardRead(lead=lead, employer=employer, listings=listings, seen=parsed.seen)


__all__ = ["BoardRead", "BoardReader", "employer_id_of", "pretty_name"]
