"""Sign-in emails, sent through Resend's HTTP API.

    POST https://api.resend.com/emails        Authorization: Bearer <RESEND_API_KEY>
    {"from": ..., "to": [address], "subject": ..., "text": ..., "html": ..., "headers": {...}}   (headers only when given)

`EmailSender` is the seam: the container builds a `ResendEmailSender` when ``RESEND_API_KEY`` is set, tests hand in
a fake, and a local server with no key sends nothing at all (the link is logged and returned instead, see
`magic.MagicLinks`). A failed send raises `EmailError` carrying only the HTTP status and Resend's error name:
never the address, the link or the response body.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import httpx2

from tayyari.core.logging import get_logger

log = get_logger(__name__)

RESEND_URL: Final = "https://api.resend.com/emails"
DEFAULT_SENDER: Final = "Leafout <onboarding@resend.dev>"
"""Resend's shared test sender: it only delivers to the address that owns the Resend account. Set RESEND_FROM
(a sender on a verified domain) to email real people."""
SEND_TIMEOUT_S: Final = 10.0


class EmailError(RuntimeError):
    """The email provider refused or did not answer."""


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str = field(repr=False)
    subject: str
    text: str = field(repr=False)
    html: str = field(repr=False)
    headers: dict[str, str] | None = field(default=None, repr=False)
    """Extra mail headers (The Leafout Weekly's ``List-Unsubscribe``), sent to Resend as its ``headers`` object."""


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None:
        """Deliver ``message`` or raise `EmailError`."""
        ...


class ResendEmailSender:
    def __init__(self, *, api_key: str, sender: str | None, client: httpx2.AsyncClient) -> None:
        self._api_key = api_key
        self._sender = sender or DEFAULT_SENDER
        self._client = client

    async def send(self, message: EmailMessage) -> None:
        payload: dict[str, Any] = {
            "from": self._sender,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
            "html": message.html,
        }
        if message.headers:
            payload["headers"] = dict(message.headers)
        try:
            response = await self._client.post(
                RESEND_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=SEND_TIMEOUT_S,
            )
        except httpx2.HTTPError as err:
            raise EmailError(f"the email provider could not be reached ({type(err).__name__})") from None
        if response.status_code >= 300:
            name = ""
            try:
                body = response.json()
                if isinstance(body, dict) and isinstance(body.get("name"), str):
                    name = f": {body['name'][:64]}"
            except ValueError:
                pass
            raise EmailError(f"the email provider answered {response.status_code}{name}")


def sign_in_message(to: str, link: str, minutes: int) -> EmailMessage:
    """The sign-in email: plain words, one link, no images and no tracking."""
    subject = "Your Leafout sign-in link"
    text = (
        "Here is your link to sign in to Leafout:\n\n"
        f"{link}\n\n"
        f"It works once and expires in {minutes} minutes. "
        "If you did not ask for it, you can ignore this email: nobody can sign in without the link."
    )
    safe_link = html.escape(link, quote=True)
    page = (
        '<div style="font-family: Georgia, serif; font-size: 16px; line-height: 1.5; color: #222;">'
        "<p>Here is your link to sign in to Leafout.</p>"
        f'<p><a href="{safe_link}">Sign in to Leafout</a></p>'
        f"<p>It works once and expires in {minutes} minutes. "
        "If you did not ask for it, you can ignore this email: nobody can sign in without the link.</p>"
        "</div>"
    )
    return EmailMessage(to=to, subject=subject, text=text, html=page)


__all__ = [
    "DEFAULT_SENDER",
    "RESEND_URL",
    "EmailError",
    "EmailMessage",
    "EmailSender",
    "ResendEmailSender",
    "sign_in_message",
]
