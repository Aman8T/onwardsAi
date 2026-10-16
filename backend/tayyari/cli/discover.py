"""``discover-refresh`` and ``discover-worker``: read employers' own public job boards into the database.

``discover-refresh`` does one refresh and exits (0 when at least one board was read, 1 when none was). ``discover-worker`` does
one every ``--every`` hours (default: ``TAYYARI_DISCOVER__REFRESH_HOURS``) until it is stopped. Both use the configured
database (``DATABASE_URL``; SQLite under the user data folder when unset), migrate it first, and need no API key: the boards
are public. Only the registry's Greenhouse and Lever boards are fetched. They share the single-flight rule with the web app,
so running the worker next to the server never reads a board twice at the same time.

The service is the same object the web app builds; this is the second way to run it, so it can move to its own deployment
without a code change.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from tayyari.core.clock import SystemClock
from tayyari.core.settings import Settings, get_settings
from tayyari.discover.refresh import RefreshReport
from tayyari.discover.service import DiscoverService
from tayyari.storage.db import Database
from tayyari.storage.migrate import migrate_to_head
from tayyari.storage.repos.discover import SqlDiscoverRepo

SECONDS_PER_HOUR = 3600.0


def _settings(registry: Path | None) -> Settings:
    settings = get_settings()
    if registry is None:
        return settings
    copy = settings.model_copy(
        update={"discover": settings.discover.model_copy(update={"registry_path": registry})}
    )
    copy.__dict__.pop("db", None)
    return copy


def describe(report: RefreshReport | None) -> list[str]:
    """The lines the command prints for a finished refresh (``None``: another run was in flight)."""
    if report is None:
        return ["Another refresh is already running; nothing was done."]
    lines = [
        f"Discover refresh {report.status}: {report.employers_ok} boards read, {report.employers_failed} failed.",
        f"Listings open now: {report.listings_open}. Added {report.inserted}, changed {report.updated}, "
        f"closed {report.listings_closed}, left out over the per-employer cap {report.dropped}.",
    ]
    if report.note:
        lines.append(report.note)
    lines.extend(f"  {employer}: {why}" for employer, why in sorted(report.failures.items()))
    return lines


async def _service(settings: Settings) -> tuple[Database, DiscoverService]:
    db = Database(settings.db)
    try:
        await migrate_to_head(db)
        service = DiscoverService(settings=settings.discover, repo=SqlDiscoverRepo(db), clock=SystemClock())
    except BaseException:
        await db.dispose()
        raise
    return db, service


async def _refresh_once(settings: Settings) -> int:
    db, service = await _service(settings)
    try:
        if not settings.discover.enabled:
            print("Discover is switched off (TAYYARI_DISCOVER__ENABLED=false); nothing was read.")
            return 0
        report = await service.refresh()
        print("\n".join(describe(report)))
        return 1 if report is not None and report.status == "failed" else 0
    finally:
        await service.aclose()
        await db.dispose()


async def _work(settings: Settings, every_hours: float) -> int:
    db, service = await _service(settings)
    try:
        if not settings.discover.enabled:
            print("Discover is switched off (TAYYARI_DISCOVER__ENABLED=false); the worker has nothing to do.")
            return 0
        print(f"Discover worker: reading the boards every {every_hours:g} hours. Stop it with Ctrl+C.")
        while True:
            try:
                print("\n".join(describe(await service.refresh())))
            except Exception as exc:  # the loop outlives a bad round
                print(f"Refresh failed: {type(exc).__name__}", file=sys.stderr)
            await asyncio.sleep(every_hours * SECONDS_PER_HOUR)
    finally:
        await service.aclose()
        await db.dispose()


def run_refresh(registry: Path | None = None) -> int:
    """Read the boards once and print what happened. Returns 1 when no board could be read."""
    return asyncio.run(_refresh_once(_settings(registry)))


def run_worker(every_hours: float | None = None, registry: Path | None = None) -> int:
    """Read the boards every ``every_hours`` hours until interrupted."""
    settings = _settings(registry)
    return asyncio.run(_work(settings, every_hours or settings.discover.refresh_hours))
