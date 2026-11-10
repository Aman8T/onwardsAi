"""Pydantic mirror of shared/inbox.ts: the application inbox (one tracking address per person).

Same rules as the other contracts: camelCase field names that are the JSON keys, a field is optional (``X | None = None``)
exactly when the TypeScript field is ``x?:`` (absent on output, never ``null``), and
`tests/unit/contract/test_inbox_parity.py` fails when the two drift. `EmailStage` is owned by `tayyari.contract.apply`.

The whole feature sits behind ``INBOUND_EMAIL_ENABLED`` plus both Resend secrets. An address exists only after the person
opted in (`CreateInboxAddressRequest.consent` is true). We keep the sender, the subject (cut to
``INBOX_SUBJECT_MAX_CHARS``) and the stage an email set, never the body; a verification code never reaches the web app.

Signed-in only: an address and its emails belong to one account.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/inbox.ts)

from __future__ import annotations

from typing import Final, Literal, Self

from pydantic import field_validator, model_validator

from tayyari.contract.apply import EmailStage
from tayyari.contract.models import _Model

INBOX_CONSENT_VERSION: Final = "inbox-v1"
"""The version of the consent text the person agreed to; stored with the address as ``consent_version``."""

INBOX_SUBJECT_MAX_CHARS: Final = 200
INBOX_EMAILS_DEFAULT_LIMIT: Final = 50
INBOX_EMAILS_MAX_LIMIT: Final = 200

InboxEmailStatus = Literal[
    "pending",
    "processed",
    "unmatched",
    "verification",
    "quarantined",
    "ignored",
    "capped",
    "failed",
]
"""What happened to one received email. ``pending``: received, not read yet. ``processed``: classified and matched to an
application. ``unmatched``: classified, no application fits. ``verification``: an ATS verification code or link (kept for
auto-apply, never shown). ``quarantined``: sent to an address nobody has. ``ignored``: the address was off. ``capped``:
over the daily cap for the address. ``failed``: could not be read."""

CopyStatus = Literal["sent", "skipped", "failed"]
"""The copy to the person's real inbox: ``skipped`` when copies are off or the email is not copied."""


class InboxAddress(_Model):
    """The person's tracking address. One per person, never reused or given to anyone else."""

    email: str
    active: bool
    """Off: mail to it is noted (status ``ignored``) but not read."""
    copies: bool
    """Send the person a copy of each employer email."""
    createdAt: str
    """ISO 8601."""


class InboxStatus(_Model):
    """``GET /api/inbox/status``; works with the feature off (``enabled: false``, no address)."""

    enabled: bool
    address: InboxAddress | None = None
    """Absent until the person asked for one (and always while the feature is off)."""


class CreateInboxAddressRequest(_Model):
    """ "Get my address" (``POST /api/inbox/address``). Refused unless ``consent`` is true."""

    consent: bool

    @field_validator("consent")
    @classmethod
    def _consent_is_given(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("an address is created only with consent")
        return value


class UpdateInboxAddressRequest(_Model):
    """``PATCH /api/inbox/address``. At least one of the two is required."""

    active: bool | None = None
    copies: bool | None = None

    @model_validator(mode="after")
    def _something_changes(self) -> Self:
        if self.active is None and self.copies is None:
            raise ValueError("set active, copies or both")
        return self


class InboxEmail(_Model):
    """One received email, as listed. Never the body, never a verification code."""

    id: str
    receivedAt: str
    """ISO 8601."""
    sender: str
    """The sender's address."""
    senderDomain: str
    subject: str
    status: InboxEmailStatus
    stage: EmailStage | None = None
    """The stage the classifier read (absent until classified, and for verification codes)."""
    applicationId: str | None = None
    """The application the email was matched to."""
    quote: str | None = None
    """The sentence of the email the classification rests on."""
    copyStatus: CopyStatus | None = None


class InboxEmailList(_Model):
    """``GET /api/inbox/emails``, newest first."""

    emails: list[InboxEmail]


__all__ = [
    "INBOX_CONSENT_VERSION",
    "INBOX_EMAILS_DEFAULT_LIMIT",
    "INBOX_EMAILS_MAX_LIMIT",
    "INBOX_SUBJECT_MAX_CHARS",
    "CopyStatus",
    "CreateInboxAddressRequest",
    "EmailStage",
    "InboxAddress",
    "InboxEmail",
    "InboxEmailList",
    "InboxEmailStatus",
    "InboxStatus",
    "UpdateInboxAddressRequest",
]
