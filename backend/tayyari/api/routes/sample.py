"""GET /api/sample-profile: the pre-computed sample `Profile` (the offline "Try a sample profile" demo)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from tayyari.api.lifespan import get_container
from tayyari.core.errors import NotFound

router = APIRouter()

NO_SAMPLE_MESSAGE = (
    'No pre-computed sample profile yet. Run "uv run python -m tayyari.cli make-sample" on the server.'
)


@router.get("/api/sample-profile")
async def sample_profile(request: Request) -> FileResponse:
    path = get_container(request).sample.sample_profile_path()
    if path is None:
        raise NotFound(NO_SAMPLE_MESSAGE)
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-cache"})


__all__ = ["NO_SAMPLE_MESSAGE", "router"]
