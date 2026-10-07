"""GET /api/profile/{pid}: a saved `Profile` as the web app expects it.

Ids are 128-bit random hex (the only thing protecting a profile), so an id that does not have the id shape is
answered exactly like an unknown one. The bundled sample persona is served from its file when it is not in the
database, so "Try a sample profile" can be reopened and chatted about even before it has been seeded.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tayyari.api.lifespan import get_container
from tayyari.core.errors import NotFound
from tayyari.core.ids import is_valid_id

router = APIRouter()

PROFILE_NOT_FOUND = "Profile not found"


@router.get("/api/profile/{pid}")
async def get_profile(pid: str, request: Request) -> JSONResponse:
    container = get_container(request)
    if not is_valid_id(pid):
        raise NotFound(PROFILE_NOT_FOUND)
    found = await container.repos.profiles.get_profile(pid)
    if found is None and container.sample.is_sample_profile(pid):
        sample = container.sample.sample_profile
        found = sample.to_dict() if sample is not None else None
    if found is None:
        raise NotFound(PROFILE_NOT_FOUND)
    # A profile is personal data on a public URL: never let a shared cache keep it.
    return JSONResponse(found, headers={"Cache-Control": "no-store"})


__all__ = ["PROFILE_NOT_FOUND", "router"]
