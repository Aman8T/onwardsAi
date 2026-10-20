"""`HandoffService`: what the marketing site may hand to the app, and the one-time pick-up.

The site posts a form (a resume file, links, a goal, notes) to ``POST /api/handoff`` and the visitor's browser lands on the
app with ``?handoff=<id>``; the app then asks ``GET /api/handoff/{id}`` once and the row is gone.

Trust: the request comes from a browser, so ``Origin`` (or ``Referer`` when a browser sent no ``Origin``) must be one of
``HandoffSettings.origins``; ``Origin: null`` (a privacy-stripped request) never passes. Everything posted is data for the
analysis, never an instruction: it is stored as it was cleaned here and handed back as it is; the links are not fetched here.

Nothing here logs the file, the links, the goal, the notes or the id.
"""

from __future__ import annotations

import base64
import re
import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlsplit

from tayyari.contract.handoff import (
    HANDOFF_GOAL_MAX_CHARS,
    HANDOFF_LINK_MAX_CHARS,
    HANDOFF_LINKS_MAX,
    HANDOFF_NOTES_MAX_CHARS,
    HandoffErrorCode,
    HandoffPayload,
    HandoffResume,
)
from tayyari.core.clock import Clock
from tayyari.core.errors import NotFound
from tayyari.core.files import resume_media_type
from tayyari.core.logging import get_logger
from tayyari.core.settings import Env, HandoffSettings
from tayyari.storage.ports import HandoffRepo, StoredHandoff

log = get_logger(__name__)

NOT_FOUND_MESSAGE = "That hand-off has expired or was already used."
ID_BYTES = 24
"""``secrets.token_urlsafe(24)``: 192 random bits, 32 characters."""

_ID_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_SCHEME_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_BARE_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}(?:[/?#]|$)")
"""A link typed without its scheme (``github.com/you``): a dotted host, then a path, query or nothing."""
_TRIM = "<>()[]{}\"'`,;"
"""What a pasted link often carries around it."""


class HandoffRejected(Exception):
    """A hand-off was refused. ``code`` is the `HandoffErrorCode` the route puts in the redirect."""

    def __init__(self, code: HandoffErrorCode) -> None:
        super().__init__(code)
        self.code: HandoffErrorCode = code


@dataclass(frozen=True, slots=True)
class ResumeFile:
    """The resume file of a hand-off, in memory. ``content_type`` is the client's label (used only when the name has
    no extension)."""

    name: str
    content_type: str | None
    data: bytes = field(repr=False)


def origin_of(value: str | None) -> str | None:
    """``scheme://host[:port]`` in lower case without a default port, for the origin of an ``Origin`` value or a ``Referer``
    URL. None for ``null``, anything that is not http(s) with a host, and anything malformed."""
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
        scheme = parts.scheme.lower()
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    shown = f"[{host}]" if ":" in host else host
    return (
        f"{scheme}://{shown}"
        if port is None or port == _DEFAULT_PORTS[scheme]
        else f"{scheme}://{shown}:{port}"
    )


def _valid_link(token: str) -> str | None:
    """``token`` as a checked http(s) address, or None. A link without a scheme gets ``https://``."""
    if not token or len(token) > HANDOFF_LINK_MAX_CHARS or not token.isprintable():
        return None
    if _SCHEME_PREFIX.match(token) is None:
        if not _BARE_HOST.match(token):
            return None
        token = "https://" + token
    if len(token) > HANDOFF_LINK_MAX_CHARS:
        return None
    try:
        parts = urlsplit(token)
        _ = parts.port  # a bad port raises
    except ValueError:
        return None
    host = parts.hostname
    if parts.scheme.lower() not in _DEFAULT_PORTS or not host or "." not in host or "@" in parts.netloc:
        return None
    return token


def parse_handoff_links(values: Iterable[str]) -> list[str]:
    """The links of the ``links`` field(s): addresses separated by newlines or other whitespace, with the brackets, quotes and
    trailing commas a paste carries trimmed off. Only http and https are kept (an address without a scheme gets ``https://``),
    none with a password in it, each at most `HANDOFF_LINK_MAX_CHARS` characters (a longer one is dropped, not cut), without
    repeats, in order, at most `HANDOFF_LINKS_MAX`."""
    out: list[str] = []
    for value in values:
        for raw in value.split():
            link = _valid_link(raw.strip(_TRIM))
            if link is not None and link not in out:
                out.append(link)
                if len(out) >= HANDOFF_LINKS_MAX:
                    return out
    return out


def _clean(value: str | None, limit: int) -> str | None:
    text = (value or "").strip()
    return text[:limit] if text else None


class HandoffService:
    def __init__(self, repo: HandoffRepo, settings: HandoffSettings, *, env: Env, clock: Clock) -> None:
        self._repo = repo
        self._settings = settings
        self._clock = clock
        self._origins = frozenset(o for o in (origin_of(item) for item in settings.origins) if o is not None)
        self._localhost = settings.localhost_allowed(env)

    # ── who may hand something over ──

    def origin_allowed(self, origin: str | None, referer: str | None) -> bool:
        """The ``Origin`` header decides; only when a browser sent none does the ``Referer``'s origin. ``null`` is refused."""
        found = origin_of(origin) if origin is not None else origin_of(referer)
        if found is None:
            return False
        if found in self._origins:
            return True
        if self._localhost:
            parts = urlsplit(found)
            return parts.scheme == "http" and parts.hostname == "localhost"
        return False

    def check_origin(self, origin: str | None, referer: str | None) -> None:
        """Raise `HandoffRejected` (``origin``) unless `origin_allowed`."""
        if not self.origin_allowed(origin, referer):
            raise HandoffRejected("origin")

    # ── store ──

    async def submit(
        self,
        *,
        links: Sequence[str] = (),
        goal: str | None = None,
        notes: str | None = None,
        resume: ResumeFile | None = None,
    ) -> str:
        """Check and store a hand-off; its id. Raises `HandoffRejected` with ``too_large``, ``type`` or ``empty``.

        ``links`` are the raw ``links`` form values. An empty resume file counts as none. Expired hand-offs are deleted first
        (a failure there is logged and never stops this one).
        """
        media_type: str | None = None
        if resume is not None and not resume.data:
            resume = None
        if resume is not None:
            if len(resume.data) > self._settings.max_bytes:
                raise HandoffRejected("too_large")
            media_type = resume_media_type(resume.name, resume.content_type)
            if media_type is None:
                raise HandoffRejected("type")
        checked = parse_handoff_links(links)
        if resume is None and not checked:
            raise HandoffRejected("empty")

        now = self._clock.now()
        try:
            await self._repo.purge_expired(now)
        except Exception:
            log.warning("handoff_purge_failed", exc_info=True)
        handoff_id = secrets.token_urlsafe(ID_BYTES)
        await self._repo.create(
            StoredHandoff(
                id=handoff_id,
                created_at=now,
                expires_at=now + timedelta(seconds=self._settings.ttl_seconds),
                links=checked,
                goal=_clean(goal, HANDOFF_GOAL_MAX_CHARS),
                notes=_clean(notes, HANDOFF_NOTES_MAX_CHARS),
                resume_name=resume.name[:255] if resume is not None else None,
                resume_type=media_type,
                resume_bytes=resume.data if resume is not None else None,
            )
        )
        return handoff_id

    async def take(self, handoff_id: str) -> HandoffPayload:
        """The hand-off, once: it is deleted as it is read. `NotFound` (404) when there is none, it was taken already or it
        has expired."""
        if not _ID_RE.fullmatch(handoff_id):
            raise NotFound(NOT_FOUND_MESSAGE, code="handoff_not_found")
        item = await self._repo.take(handoff_id)
        if item is None or item.expires_at <= self._clock.now():
            raise NotFound(NOT_FOUND_MESSAGE, code="handoff_not_found")
        resume = None
        if item.resume_bytes is not None and item.resume_name and item.resume_type:
            resume = HandoffResume(
                name=item.resume_name,
                type=item.resume_type,
                dataBase64=base64.b64encode(item.resume_bytes).decode("ascii"),
            )
        return HandoffPayload(links=list(item.links), goal=item.goal, notes=item.notes, resume=resume)


__all__ = [
    "ID_BYTES",
    "NOT_FOUND_MESSAGE",
    "HandoffRejected",
    "HandoffService",
    "ResumeFile",
    "origin_of",
    "parse_handoff_links",
]
