"""The dashboard's notes follow the account (`shared/notes.ts`).

    GET /api/notes?profile=<id>   `DeskNotes`: the notes of that profile, newest first
    PUT /api/notes?profile=<id>   `DeskNotes` -> `DeskNotes`: replaces the whole list for that profile. At most 12 notes,
                                  text trimmed and capped at 240 characters, label and ``by`` capped, NULs stripped (400
                                  for more, or for a bad or repeated id). The text is the person's own words: stored as
                                  data, never interpreted and never given to a model.

Signed-in callers only (401). The profile must be the caller's own, like the resume and match routes: another account's
profile, an unclaimed one and an unknown id are the same 404. Both routes shed in the in-memory ``chat`` bucket first (the
light routes' window); no model is called and no spend is reserved. Every answer is ``Cache-Control: no-store``.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tayyari.api.deps import get_request_context, require_user, unlimited
from tayyari.api.lifespan import get_container
from tayyari.api.routes._body import read_model
from tayyari.api.security import subnet_hash
from tayyari.container import Container
from tayyari.contract.notes import DeskNote, DeskNotes, normalise_notes
from tayyari.core.errors import BadRequest, NotFound
from tayyari.core.ids import is_valid_id
from tayyari.storage.ports import StoredDeskNote

router = APIRouter()

NO_STORE: Final = {"Cache-Control": "no-store"}
BODY_MAX_BYTES: Final = 16 * 1024
"""Twelve notes of at most 240 characters (four bytes each when escaped) plus their labels."""
NO_PROFILE: Final = "Say which profile: add ?profile=<id> to the address."
PROFILE_NOT_FOUND: Final = "We couldn't find that profile."


async def _scope(request: Request) -> tuple[Container, str, str]:
    """Shed, then the sign-in (401), then the profile the caller owns (404): the container, user id and profile id."""
    container = get_container(request)
    rc = await get_request_context(request)
    container.rate_limiter.shed(
        "chat",
        rc.ip_hash,
        ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip),
        exempt=await unlimited(request, container),
    )
    user = await require_user(request)
    profile_id = request.query_params.get("profile", "").strip()
    if not profile_id:
        raise BadRequest(NO_PROFILE)
    if not is_valid_id(profile_id):
        raise NotFound(PROFILE_NOT_FOUND)
    if await container.repos.profiles.get_owned_for_chat(user.id, profile_id) is None:
        raise NotFound(PROFILE_NOT_FOUND)
    return container, user.id, profile_id


async def _view(container: Container, user_id: str, profile_id: str) -> JSONResponse:
    stored = await container.repos.desk_notes.list(user_id, profile_id)
    notes = [DeskNote(id=n.id, kind=n.kind, text=n.text, label=n.label, by=n.by, at=n.at) for n in stored]  # type: ignore[arg-type]
    return JSONResponse(DeskNotes(notes=notes).to_dict(), headers=NO_STORE)


@router.get("/api/notes")
async def get_notes(request: Request) -> JSONResponse:
    container, user_id, profile_id = await _scope(request)
    return await _view(container, user_id, profile_id)


@router.put("/api/notes")
async def put_notes(request: Request) -> JSONResponse:
    container, user_id, profile_id = await _scope(request)
    body = await read_model(request, container, DeskNotes, max_bytes=BODY_MAX_BYTES)
    try:
        clean = normalise_notes(body)
    except ValueError as err:
        raise BadRequest(f"Invalid request: {err}") from None
    await container.repos.desk_notes.replace(
        user_id,
        profile_id,
        [
            StoredDeskNote(id=n.id, kind=n.kind, text=n.text, at=n.at, label=n.label, by=n.by)
            for n in clean.notes
        ],
        container.clock.now(),
    )
    return await _view(container, user_id, profile_id)
