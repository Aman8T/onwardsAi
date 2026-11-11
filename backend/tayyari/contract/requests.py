"""What the HTTP layer hands to the services: the parsed analyze request, the chat body and the request context.

`AnalyzeRequest` is built by the multipart upload code after every size cap has been applied. The chat
bodies are validated pydantic models with the limits of the Node server (the numbers live in
`tayyari.core.limits`, so the prompt, the schema and the HTTP layer agree on them).
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/types.ts)

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from tayyari.contract.models import ChatOrigin, _Model
from tayyari.core.limits import (
    CHAT_DESK_DATE_MAX_CHARS,
    CHAT_DESK_EMPLOYER_MAX_CHARS,
    CHAT_DESK_ID_MAX_CHARS,
    CHAT_DESK_NOTE_MAX_CHARS,
    CHAT_DESK_STAGE_MAX_CHARS,
    CHAT_DESK_TITLE_MAX_CHARS,
    CHAT_DESK_TRACKED_MAX,
    CHAT_MESSAGE_MAX_CHARS,
    CHAT_ORIGIN_CAPTION_MAX_CHARS,
    CHAT_ORIGIN_REF_ID_MAX_CHARS,
    CHAT_ORIGIN_TITLE_MAX_CHARS,
    CHAT_PROFILE_ID_MAX_CHARS,
    CHAT_SESSION_ID_MAX_CHARS,
)


@dataclass(frozen=True, slots=True)
class Reread:
    """An analysis that starts from an existing profile (``fromProfile``) instead of a new upload: whose profile it is
    (``user_id`` None for one nobody has claimed) and, once the route has built the inputs, one plain sentence that says
    what is read and what was left out (it goes into the run's log)."""

    profile_id: str
    user_id: str | None = None
    note: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class AnalyzeRequest:
    """One POST /api/analyze after multipart parsing. The content fields stay out of ``repr`` (logs)."""

    resume_bytes: bytes | None = field(default=None, repr=False)
    resume_name: str | None = None
    links: tuple[str, ...] = field(default=(), repr=False)
    goal: str | None = field(default=None, repr=False)
    notes: str | None = field(default=None, repr=False)
    sample: bool = False
    reread: Reread | None = None


def _strip_nul(value: object) -> object:
    """Postgres rejects NUL in TEXT and JSONB, so a stray NUL would make the turn unsavable after it streamed."""
    return value.replace("\x00", "") if isinstance(value, str) else value


class ChatOriginBody(ChatOrigin):
    """`ChatOrigin` as received over HTTP: same fields, but ``title`` and ``caption`` may be left out and every
    string is length-capped. It is a `ChatOrigin`, so it can be stored or rendered as one directly."""

    refId: str | None = Field(default=None, max_length=CHAT_ORIGIN_REF_ID_MAX_CHARS)
    title: str = Field(default="", max_length=CHAT_ORIGIN_TITLE_MAX_CHARS)
    caption: str = Field(default="", max_length=CHAT_ORIGIN_CAPTION_MAX_CHARS)

    @field_validator("refId", "title", "caption", mode="before")
    @classmethod
    def _drop_nul(cls, v: object) -> object:
        return _strip_nul(v)


_DESK_ID_CHARS = re.compile(r"[^A-Za-z0-9_.:-]")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _clean_line(value: object, limit: int) -> str:
    """One line of text from the browser: control characters (NULs included) gone, whitespace collapsed, clipped."""
    if not isinstance(value, str):
        return ""
    flat = " ".join(_CONTROL_CHARS.sub(" ", value).split())
    return flat[:limit]


class ChatDeskTracked(_Model):
    """One tracked role from the person's browser. Its ``id`` can only become a button target; the rest is shown to the
    coach as data, never as instructions."""

    id: str
    title: str
    employer: str
    stage: str
    note: str | None = None
    appliedAt: str | None = None


class ChatDesk(_Model):
    """What only the browser knows (tracked roles live there). Read leniently: an entry without a usable id or title is
    dropped, strings are cleaned and clipped, and only the first `CHAT_DESK_TRACKED_MAX` entries are kept."""

    tracked: list[ChatDeskTracked]

    @field_validator("tracked", mode="before")
    @classmethod
    def _clean(cls, raw: object) -> object:
        if not isinstance(raw, list):
            return raw
        out: list[dict[str, str]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            ident = _DESK_ID_CHARS.sub("", _clean_line(entry.get("id"), CHAT_DESK_ID_MAX_CHARS))
            title = _clean_line(entry.get("title"), CHAT_DESK_TITLE_MAX_CHARS)
            if not ident or not title:
                continue
            item = {
                "id": ident,
                "title": title,
                "employer": _clean_line(entry.get("employer"), CHAT_DESK_EMPLOYER_MAX_CHARS),
                "stage": _clean_line(entry.get("stage"), CHAT_DESK_STAGE_MAX_CHARS),
            }
            if note := _clean_line(entry.get("note"), CHAT_DESK_NOTE_MAX_CHARS):
                item["note"] = note
            if applied := _clean_line(entry.get("appliedAt"), CHAT_DESK_DATE_MAX_CHARS):
                item["appliedAt"] = applied
            out.append(item)
            if len(out) >= CHAT_DESK_TRACKED_MAX:
                break
        return out


class ChatResume(_Model):
    """The resume the person has open beside the coach. It only names the resume: whether the caller may open it is the
    server's call (the rule of the resume routes), and one they may not open is ignored without a word."""

    id: str = Field(min_length=1, max_length=CHAT_PROFILE_ID_MAX_CHARS)

    @field_validator("id", mode="before")
    @classmethod
    def _drop_nul(cls, v: object) -> object:
        return _strip_nul(v)


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
    desk: ChatDesk | None = None
    resume: ChatResume | None = None

    @field_validator("profileId", "message", "sessionId", mode="before")
    @classmethod
    def _drop_nul(cls, v: object) -> object:
        return _strip_nul(v)


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
    "ChatDesk",
    "ChatDeskTracked",
    "ChatOriginBody",
    "ChatRequestBody",
    "ChatResume",
    "RequestContext",
    "Reread",
]
