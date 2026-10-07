"""What the HTTP layer hands to the services: the parsed analyze request, the chat body and the request context.

`AnalyzeRequest` is built by the multipart upload code after every size cap has been applied. The chat
bodies are validated pydantic models with the limits of the Node server (the numbers live in
`tayyari.core.limits`, so the prompt, the schema and the HTTP layer agree on them).
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/types.ts)

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated

from pydantic import Field, StringConstraints

from tayyari.contract.models import ChatOrigin, _Model
from tayyari.core.limits import (
    CHAT_MESSAGE_MAX_CHARS,
    CHAT_ORIGIN_CAPTION_MAX_CHARS,
    CHAT_ORIGIN_REF_ID_MAX_CHARS,
    CHAT_ORIGIN_TITLE_MAX_CHARS,
    CHAT_PROFILE_ID_MAX_CHARS,
    CHAT_SESSION_ID_MAX_CHARS,
)


@dataclass(frozen=True, slots=True)
class AnalyzeRequest:
    """One POST /api/analyze after multipart parsing. The content fields stay out of ``repr`` (logs)."""

    resume_bytes: bytes | None = field(default=None, repr=False)
    resume_name: str | None = None
    links: tuple[str, ...] = field(default=(), repr=False)
    goal: str | None = field(default=None, repr=False)
    notes: str | None = field(default=None, repr=False)
    sample: bool = False


class ChatOriginBody(ChatOrigin):
    """`ChatOrigin` as received over HTTP: same fields, but ``title`` and ``caption`` may be left out and every
    string is length-capped. It is a `ChatOrigin`, so it can be stored or rendered as one directly."""

    refId: str | None = Field(default=None, max_length=CHAT_ORIGIN_REF_ID_MAX_CHARS)
    title: str = Field(default="", max_length=CHAT_ORIGIN_TITLE_MAX_CHARS)
    caption: str = Field(default="", max_length=CHAT_ORIGIN_CAPTION_MAX_CHARS)


class ChatRequestBody(_Model):
    """`ChatRequest` from shared/types.ts, with the same limits as the Node server."""

    profileId: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=CHAT_PROFILE_ID_MAX_CHARS)
    ]
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=CHAT_MESSAGE_MAX_CHARS)
    ]
    origin: ChatOriginBody | None = None
    sessionId: str | None = Field(default=None, max_length=CHAT_SESSION_ID_MAX_CHARS)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Who is asking and since when, passed from the API layer to a service for logs and usage records.

    Only the hash of the client IP is ever stored or logged; the raw address (``client_ip``) exists so the
    guard can compute it, and is kept out of ``repr``.
    """

    request_id: str
    ip_hash: str | None
    client_ip: str | None = field(repr=False)
    started_at: datetime


__all__ = [
    "AnalyzeRequest",
    "ChatOriginBody",
    "ChatRequestBody",
    "RequestContext",
]
