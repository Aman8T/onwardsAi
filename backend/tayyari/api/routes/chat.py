"""POST /api/chat: a JSON `ChatRequestBody` in, an NDJSON stream of `ChatEvent` out.

Guards in order, all answered as JSON before the stream starts: ``shed`` (in-memory bucket, before the body is
read), the body read with its size cap, validation (400 with the legacy text), the shared per-IP counters
(429) and the daily spend ceiling (503).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import ValidationError
from starlette.responses import Response

from tayyari.api.deps import get_request_context
from tayyari.api.lifespan import get_container
from tayyari.api.ndjson import NdjsonResponse
from tayyari.api.uploads import read_json_body
from tayyari.contract.requests import ChatRequestBody
from tayyari.core.errors import BadRequest

router = APIRouter()

TAG = "chat"


def describe_chat_error(err: ValidationError) -> str:
    """``Invalid chat request: message String should have at least 1 character`` (the legacy text).

    Only the first problem is named, and the submitted value is never echoed (it is a chat message).
    """
    first = err.errors()[0]
    path = ".".join(str(part) for part in first["loc"]) or "body"
    return f"Invalid chat request: {path} {first['msg']}"


@router.post("/api/chat")
async def chat(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    limiter = container.rate_limiter
    limiter.shed("chat", rc.ip_hash)
    raw = await read_json_body(request)
    try:
        body = ChatRequestBody.model_validate(raw)
    except ValidationError as err:
        raise BadRequest(describe_chat_error(err)) from None

    await limiter.check("chat", rc.ip_hash, memory=False)
    await container.spend_guard.admit(container.settings.guard.est_chat_usd)
    return NdjsonResponse(request, container.chat.stream_turn(body, rc), TAG)


__all__ = ["describe_chat_error", "router"]
