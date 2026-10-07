"""GET and HEAD /api/health (no database access) and GET /api/health/deep (a database round trip).

The plain check is what the web app and the platform's readiness probe call: it answers from memory, so it
stays fast while a Neon database is waking up. A HEAD request (a probe that does not want the body) gets the
same status and headers. Any other method on it is a JSON 405 with an ``Allow`` header. ``storage`` is the one
additive field (the web app ignores it): ``"postgres"`` for a real database, ``"ephemeral"`` for a
throw-away one, omitted for a local SQLite file.

The deep check touches the database, which a public URL must not let anyone do at will (it would also keep a
scale-to-zero database awake for ever). So it is shed by the in-memory limiter like the profile route (the
caller's ``chat`` bucket: `RateLimiter` only has ``analyze`` and ``chat``), and it asks the database at most
once per ``settings.guard.deep_health_min_interval_s``: callers in between get the last result (ok or the
503), and callers that arrive together share one query.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from tayyari.api.deps import get_request_context
from tayyari.api.lifespan import get_container
from tayyari.api.security import subnet_hash
from tayyari.contract.models import Health, HealthModels
from tayyari.core.errors import ServiceUnavailable
from tayyari.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter()

DEEP_CHECK_TIMEOUT_S = 5.0
DB_UNREACHABLE_MESSAGE = "The database is not reachable."
METHOD_NOT_ALLOWED_MESSAGE = "Method not allowed"
_ATTR = "deep_health"


@dataclass
class _DeepHealth:
    """The last deep-check result: ``ok`` (None until the first check) and the clock reading it was taken at."""

    ok: bool | None = None
    at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@router.api_route("/api/health", methods=["GET", "HEAD"])
async def health(request: Request) -> JSONResponse:
    container = get_container(request)
    body = Health(
        ok=True,
        models=HealthModels(**container.settings.models.model_dump()),
        hasSample=container.sample.has_sample(),
        storage=container.storage_kind,
    )
    return JSONResponse(body.to_dict())


@router.api_route(
    "/api/health", methods=["POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False
)
async def health_wrong_method() -> JSONResponse:
    raise HTTPException(405, METHOD_NOT_ALLOWED_MESSAGE, headers={"Allow": "GET, HEAD"})


def _state(request: Request) -> _DeepHealth:
    found = getattr(request.app.state, _ATTR, None)
    if not isinstance(found, _DeepHealth):
        found = _DeepHealth()
        setattr(request.app.state, _ATTR, found)
    return found


def _answer(ok: bool) -> JSONResponse:
    if ok:
        return JSONResponse({"ok": True, "db": "ok"})
    raise ServiceUnavailable(DB_UNREACHABLE_MESSAGE, code="db_unreachable")


@router.get("/api/health/deep")
async def health_deep(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    container.rate_limiter.shed(
        "chat", rc.ip_hash, ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip)
    )
    if container.db is None:  # fake repositories in a test: there is no database to ask
        return JSONResponse({"ok": True, "db": "none"})

    clock = container.clock
    min_interval = container.settings.guard.deep_health_min_interval_s
    last = _state(request)

    def fresh() -> bool:
        return last.ok is not None and 0 <= clock.monotonic() - last.at < min_interval

    if fresh():
        return _answer(bool(last.ok))
    async with last.lock:  # callers that arrive together share one query
        if fresh():
            return _answer(bool(last.ok))
        try:
            async with asyncio.timeout(DEEP_CHECK_TIMEOUT_S):
                await container.db.ping()
            last.ok = True
        except Exception as exc:
            log.warning("deep_health_failed", error=type(exc).__name__, exc_info=True)
            last.ok = False
        last.at = clock.monotonic()
        return _answer(last.ok)


__all__ = ["DB_UNREACHABLE_MESSAGE", "DEEP_CHECK_TIMEOUT_S", "METHOD_NOT_ALLOWED_MESSAGE", "router"]
