"""The Svix webhook signature Resend signs its webhooks with, checked in our own code (no extra dependency).

    secret          the base64 decoding of what follows ``whsec_``
    signed content  ``{svix-id}.{svix-timestamp}.{raw body}``
    signature       base64(HMAC-SHA256(secret, signed content))
    svix-signature  space-separated ``v1,<signature>`` entries (several while a secret is rotated)

Each ``v1`` entry is compared in constant time; a timestamp more than `TOLERANCE_S` from now is refused, so a captured
call cannot be replayed later. Every failure raises `SignatureError` with a reason that holds no secret and no signature.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping
from typing import Final

TOLERANCE_S: Final = 300
SECRET_PREFIX: Final = "whsec_"  # noqa: S105 - the prefix of every Svix secret, not a secret
ID_HEADER: Final = "svix-id"
TIMESTAMP_HEADER: Final = "svix-timestamp"
SIGNATURE_HEADER: Final = "svix-signature"


class SignatureError(ValueError):
    """The webhook's signature does not check out (the reason names what, never a value)."""


def secret_bytes(secret: str) -> bytes:
    """The HMAC key of a ``whsec_...`` secret. Raises `SignatureError` for a secret that is not base64."""
    raw = secret.strip()
    if raw.startswith(SECRET_PREFIX):
        raw = raw[len(SECRET_PREFIX) :]
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError("the webhook secret is not base64") from None
    if not key:
        raise SignatureError("the webhook secret is empty")
    return key


def sign(secret: str, msg_id: str, timestamp: str, body: bytes) -> str:
    """``v1,<signature>`` for a message (what Svix sends; the tests sign with it)."""
    content = msg_id.encode() + b"." + timestamp.encode() + b"." + body
    digest = hmac.new(secret_bytes(secret), content, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def verify(secret: str, headers: Mapping[str, str], body: bytes, *, now: float) -> None:
    """Check a webhook call; returns None when it is genuine, raises `SignatureError` otherwise.

    ``headers`` is looked up by the lower-case names (Starlette's headers are case-insensitive)."""
    msg_id = (headers.get(ID_HEADER) or "").strip()
    stamp = (headers.get(TIMESTAMP_HEADER) or "").strip()
    given = (headers.get(SIGNATURE_HEADER) or "").strip()
    if not msg_id or not stamp or not given:
        raise SignatureError("a signature header is missing")
    try:
        at = int(stamp)
    except ValueError:
        raise SignatureError("the timestamp is not a number") from None
    if abs(now - at) > TOLERANCE_S:
        raise SignatureError("the timestamp is too old or too new")
    expected = sign(secret, msg_id, stamp, body).split(",", 1)[1].encode()
    for entry in given.split():
        version, _, value = entry.partition(",")
        if version != "v1" or not value:
            continue
        if hmac.compare_digest(value.encode(), expected):
            return
    raise SignatureError("no signature matches")


__all__ = [
    "ID_HEADER",
    "SECRET_PREFIX",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "TOLERANCE_S",
    "SignatureError",
    "secret_bytes",
    "sign",
    "verify",
]
