"""POST /api/chat: a JSON `ChatRequestBody` in, an NDJSON stream of `ChatEvent` out.

Guards in order, all answered as JSON before the stream starts: ``shed`` (in-memory bucket, before the body is
read), the Content-Type (415 unless ``application/json``: a cross-site page can send ``text/plain`` without
a CORS preflight, JSON it cannot), the body read with its size cap and a deadline (413 / 408), validation
(400 with the legacy text), the profile's owner (404: a profile an account has claimed is chatted about only by that
account's session, and anyone else gets the sentence an unknown id gets, see `api.deps.require_profile_access`), a
stream slot (429: a few conversations per client and per process at a time, a chat answer streams for many seconds),
the shared per-IP counters (429) and the daily spend ceiling (503). An account on the unlimited list
(``TAYYARI_GUARD__UNLIMITED_EMAILS``) skips ``shed`` and the counters; the slot and the ceiling still apply.

A body that names a resume (``resume: {id}``, the one open beside the coach) gets it shown to the coach only when the caller
may open it by the resume routes' rule (`routes.resume.resume_in_view`); otherwise it is ignored without a word.

The stream slot and the spend reservation (the estimate counts against the ceiling for everyone else while the
turn runs) are held until the response is over, however it ends.
"""

from __future__ import annotations

from contextlib import AsyncExitStack

from fastapi import APIRouter, Request
from pydantic import ValidationError
from starlette.responses import Response

from tayyari.api.deps import get_request_context, require_profile_access, unlimited
from tayyari.api.lifespan import get_container
from tayyari.api.routes.analyze import HeldResponse
from tayyari.api.routes.resume import resume_in_view
from tayyari.api.security import subnet_hash
from tayyari.api.uploads import read_json_body, require_json_content_type
from tayyari.chat.service import PROFILE_NOT_FOUND_MESSAGE
from tayyari.container import Container
from tayyari.contract.requests import ChatRequestBody
from tayyari.core.errors import BadRequest
from tayyari.guard.slots import StreamSlots

router = APIRouter()

TAG = "chat"
BUSY_MESSAGE = "Too many conversations at once. Wait for one to finish and try again."


def describe_chat_error(err: ValidationError) -> str:
    """``Invalid chat request: message String should have at least 1 character`` (the legacy text).

    Only the first problem is named, and the submitted value is never echoed (it is a chat message).
    """
    first = err.errors()[0]
    path = ".".join(str(part) for part in first["loc"]) or "body"
    return f"Invalid chat request: {path} {first['msg']}"


def get_chat_slots(request: Request, container: Container) -> StreamSlots:
    """The stream slots for chat: ``container.chat_slots`` when the container has them, otherwise one set
    built from the guard settings on first use and kept on ``app.state.chat_slots``."""
    slots = getattr(container, "chat_slots", None)
    if isinstance(slots, StreamSlots):
        return slots
    slots = getattr(request.app.state, "chat_slots", None)
    if not isinstance(slots, StreamSlots):
        guard = container.settings.guard
        slots = StreamSlots(
            guard.chat_streams_per_ip, guard.chat_streams_per_process, busy_message=BUSY_MESSAGE
        )
        request.app.state.chat_slots = slots
    return slots


@router.post("/api/chat")
async def chat(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    limiter = container.rate_limiter
    guard = container.settings.guard
    ip48 = subnet_hash(container.ip_hash_key, rc.client_ip)
    free = await unlimited(request, container)
    limiter.shed("chat", rc.ip_hash, ip_hash_48=ip48, exempt=free)
    require_json_content_type(request)
    raw = await read_json_body(request, read_timeout_s=guard.upload_read_timeout_s)
    try:
        body = ChatRequestBody.model_validate(raw)
    except ValidationError as err:
        raise BadRequest(describe_chat_error(err)) from None
    await require_profile_access(request, body.profileId, not_found=PROFILE_NOT_FOUND_MESSAGE)

    held = AsyncExitStack()
    try:
        # The slot first: a refused conversation costs nothing, and does not use up the caller's rate-limit windows.
        await held.enter_async_context(get_chat_slots(request, container).acquire(rc.ip_hash))
        await limiter.check("chat", rc.ip_hash, ip_hash_48=ip48, memory=False, exempt=free)
        await held.enter_async_context(container.spend_guard.reserve(guard.est_chat_usd))
        view = (
            await resume_in_view(request, container, body.resume.id, body.profileId)
            if body.resume is not None
            else None
        )  # a resume the caller may not open is ignored without a word
        stream = (
            container.chat.stream_turn(body, rc)
            if view is None
            else container.chat.stream_turn(body, rc, resume=view)
        )
        return HeldResponse(request, stream, TAG, held)
    except BaseException:
        await held.aclose()
        raise


__all__ = ["BUSY_MESSAGE", "describe_chat_error", "get_chat_slots", "router"]
