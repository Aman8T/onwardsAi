"""Small JSON request bodies for the account routes: a Content-Type check, a size cap and a pydantic model."""

from __future__ import annotations

from fastapi import Request
from pydantic import BaseModel, ValidationError

from tayyari.api.uploads import read_json_body, require_json_content_type
from tayyari.container import Container
from tayyari.core.errors import BadRequest

BODY_MAX_BYTES = 4 * 1024
"""The largest account request body: an email address, a profile id or a display name."""


def describe(err: ValidationError) -> str:
    """``Invalid request: name cannot be empty``: the first problem only, without the submitted value."""
    first = err.errors()[0]
    path = ".".join(str(part) for part in first["loc"]) or "body"
    message = str(first["msg"]).removeprefix("Value error, ")[:200]
    return f"Invalid request: {path} {message}"


async def read_model[M: BaseModel](
    request: Request, container: Container, model: type[M], *, max_bytes: int = BODY_MAX_BYTES
) -> M:
    """The JSON body as ``model``: 415 for another Content-Type, 413 / 408 / 400 from the reader, 400 when invalid.

    ``max_bytes`` raises the cap for a route whose body is long on purpose (a pasted job posting)."""
    require_json_content_type(request)
    raw = await read_json_body(
        request, max_bytes, read_timeout_s=container.settings.guard.upload_read_timeout_s
    )
    try:
        return model.model_validate(raw)
    except ValidationError as err:
        raise BadRequest(describe(err)) from None


__all__ = ["BODY_MAX_BYTES", "describe", "read_model"]
