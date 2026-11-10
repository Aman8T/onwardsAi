"""The seam to the mail provider that receives for the inbound domain, and its Resend implementation (REST, no SDK).

    GET  https://api.resend.com/emails/receiving/{id}       one received email: sender, recipients, subject, text, html,
                                                             message id, headers
    GET  https://api.resend.com/emails/receiving?limit=N    the newest received emails (reconcile: a webhook that never came)
    POST https://api.resend.com/emails                      the copy to the person's own inbox

Resend's field names for received mail are read defensively (they are not verified against a live account): the sender
from ``from`` (a string or ``{email, name}``), the text from ``text`` else the HTML through `mail.html_to_text`, the message
id from ``message_id`` or the ``Message-ID`` header, a list from ``data`` or ``emails``. A failure raises
`InboundMailError` carrying only the HTTP status and Resend's error name: never an address, a subject or a body.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol

import httpx2

from tayyari.inbox.mail import addresses_in, html_to_text, sender_of

RESEND_API: Final = "https://api.resend.com"
TIMEOUT_S: Final = 10.0
LIST_MAX: Final = 100
COPY_MARKER_HEADER: Final = "X-Leafout-Copy"
"""Set on every copy we send, so a copy that comes back to a tracking address (a forwarding rule) is never read again."""


class InboundMailError(RuntimeError):
    """The provider refused or did not answer (the message holds only a status and an error name)."""


@dataclass(frozen=True, slots=True)
class ReceivedEmail:
    """One received email as fetched. The body fields stay in memory only; they are left out of ``repr``."""

    id: str
    sender: str
    to: list[str] = field(default_factory=list)
    subject: str = ""
    text: str = field(default="", repr=False)
    html: str | None = field(default=None, repr=False)
    message_id: str | None = None
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    received_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReceivedSummary:
    """One row of the provider's list of received emails (no body)."""

    id: str
    sender: str = ""
    to: list[str] = field(default_factory=list)
    subject: str = ""
    received_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CopyMessage:
    """The copy sent to the person's own inbox: plain text, reply-to the employer's sender."""

    sender: str
    to: str = field(repr=False)
    reply_to: str | None = field(repr=False)
    subject: str = field(repr=False)
    text: str = field(repr=False)


class InboundMailProvider(Protocol):
    async def get_received(self, email_id: str) -> ReceivedEmail:
        """The received email, or raises `InboundMailError`."""
        ...

    async def list_received(self, limit: int = LIST_MAX) -> list[ReceivedSummary]:
        """The newest received emails (at most ``limit``), or raises `InboundMailError`."""
        ...

    async def send_copy(self, message: CopyMessage) -> None:
        """Send the copy, or raise `InboundMailError`."""
        ...


# ───────────────────────── reading Resend's answers ─────────────────────────


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def parse_time(value: Any) -> datetime | None:
    """An ISO 8601 time (``2026-11-10T09:30:00.123Z``, or Resend's ``2026-11-10 09:30:00.123+00``) as aware UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace(" ", "T", 1)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if len(raw) >= 3 and raw[-3] in "+-" and raw[-2:].isdigit() and ":" not in raw[-6:]:
        raw += ":00"  # "+00" -> "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _headers(value: Any) -> dict[str, str]:
    """Headers as a lower-cased dict, from ``{name: value}`` or ``[{name, value}]``."""
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and isinstance(v, str):
                out[k.lower()] = v
    elif isinstance(value, list):
        for item in value:
            if (
                isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and isinstance(item.get("value"), str)
            ):
                out[item["name"].lower()] = item["value"]
    return out


def parse_received(data: dict[str, Any], fallback_id: str = "") -> ReceivedEmail:
    """A received email from Resend's JSON (``data`` may wrap it)."""
    if isinstance(data.get("data"), dict):
        data = data["data"]
    headers = _headers(data.get("headers"))
    html = data.get("html") if isinstance(data.get("html"), str) else None
    text = _str(data.get("text"))
    if not text.strip() and html:
        text = html_to_text(html)
    message_id = _str(data.get("message_id") or data.get("messageId")) or headers.get("message-id") or None
    return ReceivedEmail(
        id=_str(data.get("id") or data.get("email_id")) or fallback_id,
        sender=sender_of(data.get("from") or data.get("sender") or headers.get("from")),
        to=addresses_in(data.get("to")),
        subject=_str(data.get("subject")) or headers.get("subject", ""),
        text=text,
        html=html,
        message_id=message_id,
        headers=headers,
        received_at=parse_time(data.get("created_at") or data.get("received_at")),
    )


def parse_list(data: Any) -> list[ReceivedSummary]:
    rows = data.get("data") if isinstance(data, dict) else data
    if isinstance(data, dict) and not isinstance(rows, list):
        rows = data.get("emails")
    out: list[ReceivedSummary] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        email_id = _str(row.get("id") or row.get("email_id"))
        if not email_id:
            continue
        out.append(
            ReceivedSummary(
                id=email_id,
                sender=sender_of(row.get("from")),
                to=addresses_in(row.get("to")),
                subject=_str(row.get("subject")),
                received_at=parse_time(row.get("created_at") or row.get("received_at")),
            )
        )
    return out


# ───────────────────────── Resend ─────────────────────────


class ResendInboundProvider:
    def __init__(self, *, api_key: str, client: httpx2.AsyncClient, base_url: str = RESEND_API) -> None:
        self._api_key = api_key
        self._client = client
        self._base = base_url.rstrip("/")

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(
                method, f"{self._base}{path}", headers=self._auth(), timeout=TIMEOUT_S, **kwargs
            )
        except httpx2.HTTPError as err:
            raise InboundMailError(f"the mail provider could not be reached ({type(err).__name__})") from None
        if response.status_code >= 300:
            name = ""
            try:
                body = response.json()
                if isinstance(body, dict) and isinstance(body.get("name"), str):
                    name = f": {body['name'][:64]}"
            except ValueError:
                pass
            raise InboundMailError(f"the mail provider answered {response.status_code}{name}")
        try:
            return response.json()
        except ValueError:
            return {}

    async def get_received(self, email_id: str) -> ReceivedEmail:
        data = await self._call("GET", f"/emails/receiving/{email_id}")
        if not isinstance(data, dict):
            raise InboundMailError("the mail provider answered in an unexpected shape")
        return parse_received(data, fallback_id=email_id)

    async def list_received(self, limit: int = LIST_MAX) -> list[ReceivedSummary]:
        size = min(max(limit, 1), LIST_MAX)
        return parse_list(await self._call("GET", "/emails/receiving", params={"limit": size}))

    async def send_copy(self, message: CopyMessage) -> None:
        payload: dict[str, Any] = {
            "from": message.sender,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
            "headers": {COPY_MARKER_HEADER: "1"},
        }
        if message.reply_to:
            payload["reply_to"] = [message.reply_to]
        await self._call("POST", "/emails", json=payload)


__all__ = [
    "COPY_MARKER_HEADER",
    "LIST_MAX",
    "RESEND_API",
    "CopyMessage",
    "InboundMailError",
    "InboundMailProvider",
    "ReceivedEmail",
    "ReceivedSummary",
    "ResendInboundProvider",
    "parse_list",
    "parse_received",
    "parse_time",
]
