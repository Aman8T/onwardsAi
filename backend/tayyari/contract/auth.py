"""Pydantic mirror of shared/auth.ts: accounts, sign-in and ``/api/me``.

Same rules as `tayyari.contract.models`: camelCase field names that are the JSON keys, a field is optional
(``X | None = None``) exactly when the TypeScript field is ``x?:``, and
`tests/unit/contract/test_auth_parity.py` fails when the two drift. Optional fields left unset disappear from
the JSON (``to_dict``), so an anonymous visitor's ``/api/me`` has no ``user`` key at all.

``entitlements`` is part of the shape from the start so the web app can render it; until payments exist
(wave F2) every value is the empty one.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/auth.ts)

from __future__ import annotations

import re
from typing import Final, Literal

from pydantic import Field, field_validator

from tayyari.contract.models import _Model

Currency = Literal["INR", "USD"]

NAME_MAX_CHARS: Final = 80
"""The longest display name a person may set."""
EMAIL_MAX_CHARS: Final = 254
"""The longest valid email address (RFC 5321 path limit)."""

_EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)


def normalise_email(raw: str) -> str | None:
    """``raw`` trimmed and lower-cased when it is a plain ASCII address, else None.

    Accounts are keyed by the lower-cased address, so ``Ana@Example.com`` and ``ana@example.com`` are one person.
    """
    value = raw.strip().lower()
    if len(value) > EMAIL_MAX_CHARS or _EMAIL.fullmatch(value) is None:
        return None
    return value


class MeUser(_Model):
    id: str
    email: str
    name: str | None = None
    pictureUrl: str | None = None
    currency: Currency | None = None


class MeProfile(_Model):
    """One profile the account owns, as the "Your profiles" list shows it."""

    id: str
    createdAt: str
    goal: str | None = None
    personName: str | None = None


class MeEntitlements(_Model):
    credits: int
    passUntil: str | None = None
    freeMatchAvailable: bool


class MeProviders(_Model):
    """Which ways of signing in this server offers (the sign-in sheet hides the others)."""

    google: bool
    magic: bool


class MeResponse(_Model):
    """``GET /api/me``, for anonymous visitors (no ``user``, no profiles) and signed-in people alike."""

    user: MeUser | None = None
    profiles: list[MeProfile]
    entitlements: MeEntitlements
    providers: MeProviders


class MagicLinkRequest(_Model):
    """``POST /api/auth/magic``."""

    email: str = Field(max_length=EMAIL_MAX_CHARS + 64)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        email = normalise_email(v)
        if email is None:
            raise ValueError("is not a valid email address")
        return email


class MagicLinkResponse(_Model):
    """The 202 body of ``POST /api/auth/magic``. ``devLink`` is only ever present on a local, unconfigured server."""

    ok: bool
    devLink: str | None = None


class ClaimRequest(_Model):
    """``POST /api/me/claim``."""

    profileId: str = Field(min_length=1, max_length=96)


class MePatch(_Model):
    """``PATCH /api/me``."""

    currency: Currency | None = None
    name: str | None = Field(default=None, max_length=NAME_MAX_CHARS * 2)

    @field_validator("name")
    @classmethod
    def _tidy_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        name = " ".join(v.replace("\x00", "").split())
        if not name:
            raise ValueError("cannot be empty")
        if len(name) > NAME_MAX_CHARS:
            raise ValueError(f"is longer than {NAME_MAX_CHARS} characters")
        return name


__all__ = [
    "EMAIL_MAX_CHARS",
    "NAME_MAX_CHARS",
    "ClaimRequest",
    "Currency",
    "MagicLinkRequest",
    "MagicLinkResponse",
    "MeEntitlements",
    "MePatch",
    "MeProfile",
    "MeProviders",
    "MeResponse",
    "MeUser",
    "normalise_email",
]
