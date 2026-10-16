"""Discover: the city list behind Search with AI, and the nudge that keeps the board store it searches fresh.

    GET /api/discover/cities    `DiscoverCitiesResponse`: the cities and localities we know, for the pickers

The roles themselves come from `POST /api/jobsearch` (`api.routes.jobsearch`); this file holds what that screen needs from
Discover. **Nothing about a person is stored here.** The city a person looks in is a name from this list that arrives with
the search request, answers it and is forgotten.

The read goes through the in-memory limiter first (``shed``, no database), in the ``chat`` bucket like the other light
reads. Nothing here calls a model, so nothing is spent. The answer is never cached by a shared cache. When Discover is
switched off (``TAYYARI_DISCOVER__ENABLED=false``) the route answers 503 ``{"error": "discover_disabled"}``. A request that
finds the stored boards old (or never read) starts a refresh in the background and does not wait for it.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tayyari.api.deps import get_request_context
from tayyari.api.lifespan import get_container
from tayyari.api.security import subnet_hash

router = APIRouter()

NO_STORE: Final = {"Cache-Control": "no-store"}


@router.get("/api/discover/cities")
async def get_cities(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    container.rate_limiter.shed(
        "chat", rc.ip_hash, ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip)
    )
    container.discover.require_enabled()
    await container.discover.maybe_refresh()
    return JSONResponse(container.discover.cities().to_dict(), headers=NO_STORE)


__all__ = ["router"]
