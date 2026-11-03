"""Reading the employers' boards into the store.

One refresh: for every employer in the registry, fetch its public board (Greenhouse or Lever, no key), parse the India
listings, and store them; close the listings of an employer whose board was read and no longer lists them. Nothing else is
fetched: no careers-page crawling, no job portal, no aggregator, no link found inside a posting.

Guarantees:

    single flight     a run starts only if no other run started less than 20 minutes ago is still ``running`` (decided in the
                      database, so two replicas, or the web app and the worker, cannot both refresh)
    a failed board    never closes that employer's listings: they stay as they were, and their ``last_seen_at`` is how old
                      the card says "checked" is. A run with at least one board read is ``ok``; with none, ``failed``
    never blocking    the web app starts this as a background task (`DiscoverService.maybe_refresh`); a request never waits
    politeness        a User-Agent that says who we are, a bounded number of boards at once, a timeout per request, at most
                      one retry of a failure that may pass (a timeout, a 429 or a 5xx), and a cap on the size of an answer

The transport is injectable, so tests run on ``httpx2.MockTransport`` with hand-written Greenhouse and Lever answers.
`board_client` and `fetch_board` are the same politeness for one board at a time, for callers outside the refresh.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Final
from urllib.parse import quote

import httpx2

from tayyari.core.clock import Clock
from tayyari.core.logging import get_logger
from tayyari.core.settings import DiscoverSettings
from tayyari.storage.ports import DiscoverRepo, RunStatus

from .errors import BoardError
from .parse import parse_board
from .registry import Employer, Registry

log = get_logger(__name__)

USER_AGENT: Final = "Leafout/1.0 (+https://app.leafout.in) job discovery"
GREENHOUSE_URL: Final = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
LEVER_URL: Final = "https://api.lever.co/v0/postings/{board}?mode=json"
STALE_RUN: Final = timedelta(minutes=20)
"""A ``running`` run older than this is taken to have died."""
MAX_BODY_BYTES: Final = 64 * 1024 * 1024
RETRY_AFTER_S: Final = 2.0
RETRIABLE_STATUS: Final = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class RefreshReport:
    run_id: str
    status: RunStatus
    employers_ok: int
    employers_failed: int
    listings_open: int
    listings_closed: int
    inserted: int = 0
    updated: int = 0
    dropped: int = 0
    failures: dict[str, str] = field(default_factory=dict)
    """``{employer id: why}`` for every board that could not be read (a short sentence, never a response body)."""
    note: str | None = None


def board_url_for(ats: str, board: str) -> str:
    """The public API address of a board (``ats`` is ``greenhouse`` or ``lever``; the token is URL-quoted)."""
    template = GREENHOUSE_URL if ats == "greenhouse" else LEVER_URL
    return template.format(board=quote(board, safe=""))


def board_url(employer: Employer) -> str:
    return board_url_for(employer.ats, employer.board)


def board_client(
    *, timeout_s: float, transport: httpx2.AsyncBaseTransport | None = None
) -> httpx2.AsyncClient:
    """An HTTP client for reading boards: says who we are, asks for JSON, follows no redirect."""
    return httpx2.AsyncClient(
        transport=transport,
        timeout=httpx2.Timeout(timeout_s),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=False,
    )


async def fetch_board(client: httpx2.AsyncClient, url: str, clock: Clock) -> Any:
    """The parsed JSON at ``url``, with one retry after `RETRY_AFTER_S` of a failure that may pass (a timeout, a 429 or a
    5xx). Raises `BoardError` with a short sentence (never a response body)."""
    last = BoardError("the board could not be read")
    for attempt in (1, 2):
        try:
            return await _get_json(client, url)
        except _Transient as exc:
            last = BoardError(str(exc))
            if attempt == 1:
                await clock.sleep(RETRY_AFTER_S)
    raise last


async def _get_json(client: httpx2.AsyncClient, url: str) -> Any:
    try:
        async with client.stream("GET", url) as response:
            status = response.status_code
            if status in RETRIABLE_STATUS:
                raise _Transient(f"the board answered {status}")
            if status == 404:
                raise BoardError("the board was not found")
            if status >= 400:
                raise BoardError(f"the board answered {status}")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise BoardError("the board's answer is too large")
    except httpx2.TimeoutException:
        raise _Transient("the board timed out") from None
    except httpx2.TransportError as exc:
        raise _Transient(f"the board could not be reached ({type(exc).__name__})") from None
    except httpx2.HTTPError as exc:
        raise BoardError(f"the board could not be read ({type(exc).__name__})") from None
    try:
        return json.loads(bytes(body))
    except ValueError:
        raise BoardError("the board did not answer with JSON") from None


class _Transient(BoardError):
    """A failure that may pass on a second try."""


class Refresher:
    def __init__(
        self,
        *,
        repo: DiscoverRepo,
        settings: DiscoverSettings,
        clock: Clock,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._clock = clock
        self._transport = transport

    async def run(self, registry: Registry, problem: str | None = None) -> RefreshReport | None:
        """Refresh every board of ``registry``. None when another run is in flight (nothing was done).

        ``problem`` says the registry could not be read; the run is then recorded as failed with that note and nothing is
        fetched or closed."""
        started = self._clock.now()
        run = await self._repo.start_run(now=started, stale_after=STALE_RUN)
        if run is None:
            log.info("discover_refresh_skipped", reason="another run is in flight")
            return None
        counts = {"ok": 0, "failed": 0, "inserted": 0, "updated": 0, "dropped": 0, "closed": 0}
        failures: dict[str, str] = {}
        note: str | None = None
        status: RunStatus = "failed"
        try:
            if problem is not None:
                note = problem
            elif not registry.employers:
                note = "The employer registry is empty."
            else:
                await self._read_boards(registry, counts, failures)
                counts["closed"] += await self._repo.close_employers_except(
                    registry.ids, now=self._clock.now()
                )
                status = "ok" if counts["ok"] else "failed"
                if failures:
                    note = f"{len(failures)} of {len(registry.employers)} boards could not be read."
        except Exception as exc:
            note = f"The refresh stopped: {type(exc).__name__}."
            log.error("discover_refresh_crashed", exc_info=True)
        employers, listings = await self._repo.counts()
        await self._repo.finish_run(
            run.id,
            now=self._clock.now(),
            status=status,
            employers_ok=counts["ok"],
            employers_failed=counts["failed"],
            listings_open=listings,
            listings_closed=counts["closed"],
            note=note,
        )
        log.info(
            "discover_refresh_done",
            status=status,
            employers_ok=counts["ok"],
            employers_failed=counts["failed"],
            listings_open=listings,
            listings_closed=counts["closed"],
            inserted=counts["inserted"],
            updated=counts["updated"],
            dropped=counts["dropped"],
            employers_with_listings=employers,
        )
        return RefreshReport(
            run_id=run.id,
            status=status,
            employers_ok=counts["ok"],
            employers_failed=counts["failed"],
            listings_open=listings,
            listings_closed=counts["closed"],
            inserted=counts["inserted"],
            updated=counts["updated"],
            dropped=counts["dropped"],
            failures=failures,
            note=note,
        )

    async def _read_boards(
        self, registry: Registry, counts: dict[str, int], failures: dict[str, str]
    ) -> None:
        gate = asyncio.Semaphore(self._settings.fetch_concurrency)
        async with board_client(
            timeout_s=self._settings.request_timeout_s, transport=self._transport
        ) as client:

            async def one(employer: Employer) -> None:
                async with gate:
                    try:
                        payload = await fetch_board(client, board_url(employer), self._clock)
                        now = self._clock.now()
                        parsed = await asyncio.to_thread(
                            parse_board,
                            employer,
                            payload,
                            now=now,
                            cap=self._settings.max_listings_per_employer,
                        )
                        del payload
                        stored = await self._repo.upsert_employer(employer.id, parsed.listings, now=now)
                        counts["closed"] += await self._repo.close_missing(
                            employer.id, [item.id for item in parsed.listings], now=self._clock.now()
                        )
                    except BoardError as exc:
                        counts["failed"] += 1
                        failures[employer.id] = str(exc)
                        log.warning(
                            "discover_board_failed", employer=employer.id, ats=employer.ats, reason=str(exc)
                        )
                        return
                    except Exception as exc:  # a storage error on one employer must not stop the others
                        counts["failed"] += 1
                        failures[employer.id] = f"could not be stored ({type(exc).__name__})"
                        log.error("discover_board_store_failed", employer=employer.id, exc_info=True)
                        return
                    counts["ok"] += 1
                    counts["inserted"] += stored.inserted
                    counts["updated"] += stored.updated
                    counts["dropped"] += parsed.dropped
                    if parsed.dropped:
                        log.info(
                            "discover_board_capped",
                            employer=employer.id,
                            kept=len(parsed.listings),
                            dropped=parsed.dropped,
                            india=parsed.india,
                            cap=self._settings.max_listings_per_employer,
                        )

            await asyncio.gather(*(one(employer) for employer in registry.employers))


__all__ = [
    "GREENHOUSE_URL",
    "LEVER_URL",
    "MAX_BODY_BYTES",
    "STALE_RUN",
    "USER_AGENT",
    "RefreshReport",
    "Refresher",
    "board_client",
    "board_url",
    "board_url_for",
    "fetch_board",
]
