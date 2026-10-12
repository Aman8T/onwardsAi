"""GET /api/profile/{pid}: a saved `Profile` as the web app expects it.

Ids are 128-bit random hex (the only thing protecting a profile), so an id that does not have the id shape is
answered exactly like an unknown one. The bundled sample persona is served from its file when it is not in the
database, so "Try a sample profile" can be reopened and chatted about even before it has been seeded.

Every read goes through the in-memory limiter first (``shed``, no database access), so a client cannot turn this
route into a stream of database queries. `RateLimiter` has a bucket for ``analyze`` and one for ``chat``; a
profile read is a light chat-side action (you open a profile to talk about it), so it takes a token from the
caller's ``chat`` bucket. That bucket holds twice the per-minute chat limit and refills at that rate, so
opening profiles at human speed never gets in the way of chatting.

A profile that an account has claimed is read only by that account's session; anyone else, signed in or not, gets the
same 404 as for an unknown id (`api.deps.require_profile_access`). Unclaimed profiles and sample runs are open to
whoever holds the id, as before.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tayyari.api.deps import get_request_context, require_profile_access
from tayyari.api.lifespan import get_container
from tayyari.api.security import subnet_hash
from tayyari.core.errors import NotFound
from tayyari.core.ids import is_valid_id

router = APIRouter()

PROFILE_NOT_FOUND = "Profile not found"


@router.get("/api/profile/{pid}")
async def get_profile(pid: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    container.rate_limiter.shed(
        "chat", rc.ip_hash, ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip)
    )
    if not is_valid_id(pid):
        raise NotFound(PROFILE_NOT_FOUND)
    await require_profile_access(request, pid, not_found=PROFILE_NOT_FOUND)
    found = await container.repos.profiles.get_profile(pid)
    if found is None and container.sample.is_sample_profile(pid):
        sample = container.sample.sample_profile
        found = sample.to_dict() if sample is not None else None
    if found is None:
        raise NotFound(PROFILE_NOT_FOUND)
    # A profile is personal data on a public URL: never let a shared cache keep it.
    return JSONResponse(found, headers={"Cache-Control": "no-store"})


__all__ = ["PROFILE_NOT_FOUND", "router"]
