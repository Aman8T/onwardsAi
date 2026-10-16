"""`DiscoverService`: the board store behind Search with AI. It reads employers' own public job boards into the store and
keeps it fresh; `jobsearch` reads the store and the registry.

    cities      the places we know, for the web app's pickers
    refresh     read the boards into the store (also the CLI's ``discover-refresh`` and ``discover-worker``)

A self-contained service (like billing): it imports only `core`, `contract` and `storage.ports`, owns its tables, its
settings and its HTTP client. No model is called anywhere.

**Nothing about a person is stored here.** The city a person is looking in arrives with each Search with AI request,
answers it and is forgotten.

**Refresh never blocks a request.** A request that finds the last good refresh older than ``refresh_hours`` (or none) starts
one as a background task (`maybe_refresh`), guarded in process and, through the database, across replicas and the worker.
"""

from __future__ import annotations

import asyncio
import contextvars
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

import httpx2

from tayyari.contract.discover import DiscoverCitiesResponse, DiscoverCity
from tayyari.core.clock import Clock
from tayyari.core.logging import get_logger
from tayyari.core.settings import DiscoverSettings
from tayyari.storage.ports import DiscoverRepo, DiscoverRun

from . import places
from .errors import DiscoverDisabled, RegistryError
from .refresh import Refresher, RefreshReport
from .registry import EMPTY, Registry, load_registry

log = get_logger(__name__)

RETRY_AFTER_ATTEMPT: Final = timedelta(minutes=10)
"""After a refresh was started, no other is started by this process for this long (a failing network is not hammered)."""


class DiscoverService:
    def __init__(
        self,
        *,
        settings: DiscoverSettings,
        repo: DiscoverRepo,
        clock: Clock,
        registry: Registry | None = None,
        registry_path: Path | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._clock = clock
        self._registry_path = registry_path if registry_path is not None else settings.registry_path
        self._problem: str | None = None
        self._reload_registry = registry is None
        self._registry = registry if registry is not None else self._load_registry()
        self._refresher = Refresher(repo=repo, settings=settings, clock=clock, transport=transport)
        self._task: asyncio.Task[None] | None = None
        self._retry_after: datetime | None = None

    # ───────────────────────── settings and registry ─────────────────────────

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    def require_enabled(self) -> None:
        if not self._settings.enabled:
            raise DiscoverDisabled()

    @property
    def registry(self) -> Registry:
        return self._registry

    def _load_registry(self) -> Registry:
        try:
            found = load_registry(self._registry_path)
        except RegistryError as exc:
            self._problem = str(exc)
            log.error("discover_registry_invalid", problem=str(exc)[:500])
            return EMPTY
        self._problem = None
        return found

    # ───────────────────────── places ─────────────────────────

    def cities(self) -> DiscoverCitiesResponse:
        return DiscoverCitiesResponse(
            cities=[
                DiscoverCity(name=city.name, localities=[locality.name for locality in city.localities])
                for city in places.CITIES
            ]
        )

    # ───────────────────────── refresh ─────────────────────────

    async def refresh(self) -> RefreshReport | None:
        """Read the employers' boards now and wait for it (the CLI, and the background task). The registry file is read
        again first, so a worker picks up an edit without a restart. None when another run is in flight."""
        if self._reload_registry:
            self._registry = self._load_registry()
        return await self._refresher.run(self._registry, problem=self._problem)

    async def maybe_refresh(self) -> bool:
        """Start a refresh in the background when the last good one is old or missing. Returns whether one was started."""
        if not self._settings.enabled:
            return False
        return self._start_if_stale(await self._repo.last_ok_run())

    def _start_if_stale(self, last_ok: DiscoverRun | None) -> bool:
        if not self._settings.enabled or (self._task is not None and not self._task.done()):
            return False
        now = self._clock.now()
        if self._retry_after is not None and now < self._retry_after:
            return False
        if last_ok is not None:
            newest = last_ok.finished_at or last_ok.started_at
            if now < newest + timedelta(hours=self._settings.refresh_hours):
                return False
        self._retry_after = now + RETRY_AFTER_ATTEMPT
        # An empty context: the request that noticed the data is old must not lend its identity (request id, client hash)
        # to the background work, whose log lines are nobody's request.
        self._task = asyncio.create_task(
            self._refresh_in_background(), name="tayyari-discover-refresh", context=contextvars.Context()
        )
        return True

    async def _refresh_in_background(self) -> None:
        try:
            await self.refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("discover_background_refresh_failed", exc_info=True)

    async def wait_for_refresh(self) -> None:
        """Wait for the background refresh in flight, if any (tests)."""
        task = self._task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def aclose(self) -> None:
        """Stop a background refresh that is still running."""
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


__all__ = ["DiscoverService"]
