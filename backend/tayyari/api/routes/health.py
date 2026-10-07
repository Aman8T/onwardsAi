"""GET /api/health (no database access) and GET /api/health/deep (a database round trip).

The plain check is what the web app and the platform's readiness probe call: it answers from memory, so it
stays fast while a Neon database is waking up. ``storage`` is the one additive field (the web app ignores it):
``"postgres"`` for a real database, ``"ephemeral"`` for a throw-away one, omitted for a local SQLite file.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tayyari.api.lifespan import get_container
from tayyari.contract.models import Health, HealthModels
from tayyari.core.errors import ServiceUnavailable
from tayyari.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter()

DEEP_CHECK_TIMEOUT_S = 5.0
DB_UNREACHABLE_MESSAGE = "The database is not reachable."


@router.get("/api/health")
async def health(request: Request) -> JSONResponse:
    container = get_container(request)
    body = Health(
        ok=True,
        models=HealthModels(**container.settings.models.model_dump()),
        hasSample=container.sample.has_sample(),
        storage=container.storage_kind,
    )
    return JSONResponse(body.to_dict())


@router.get("/api/health/deep")
async def health_deep(request: Request) -> JSONResponse:
    container = get_container(request)
    if container.db is None:  # fake repositories in a test: there is no database to ask
        return JSONResponse({"ok": True, "db": "none"})
    try:
        async with asyncio.timeout(DEEP_CHECK_TIMEOUT_S):
            await container.db.ping()
    except Exception as exc:
        log.warning("deep_health_failed", error=type(exc).__name__, exc_info=True)
        raise ServiceUnavailable(DB_UNREACHABLE_MESSAGE, code="db_unreachable") from None
    return JSONResponse({"ok": True, "db": "ok"})


__all__ = ["DB_UNREACHABLE_MESSAGE", "DEEP_CHECK_TIMEOUT_S", "router"]
