"""Identifiers.

Profile and chat-session ids are 128-bit random hex strings: they are the only thing protecting a
profile from being read by someone else, so they must not be guessable (the old ids were a base-36
timestamp plus 24 random bits). Sample runs are prefixed ``sample-`` so retention and evals can tell
them apart. Log-table ids are UUIDv7 (time-ordered, so inserts stay index-local).
"""

from __future__ import annotations

import re
import secrets
import time
import uuid
from typing import TypeGuard

from .limits import ID_MAX_CHARS

SAMPLE_PREFIX = "sample-"

_ID_RE = re.compile(rf"[a-z0-9][a-z0-9_-]{{0,{ID_MAX_CHARS - 1}}}", re.IGNORECASE)
"""Legacy id shape (``^[a-z0-9][a-z0-9_-]{0,80}$``, case-insensitive), so ids minted by older builds stay valid."""

_MASK_48 = (1 << 48) - 1
_MASK_62 = (1 << 62) - 1


def new_profile_id(*, sample: bool) -> str:
    """``token_hex(16)`` (32 hex chars), prefixed ``sample-`` for sample runs."""
    token = secrets.token_hex(16)
    return f"{SAMPLE_PREFIX}{token}" if sample else token


def new_session_id() -> str:
    """A chat session id: ``token_hex(16)``."""
    return secrets.token_hex(16)


def new_match_id() -> str:
    """A posting match's id: ``token_hex(16)``. Like a profile id it is a capability, so it is not guessable (and a
    match is only ever read together with the signed-in account that owns it)."""
    return secrets.token_hex(16)


def new_public_id() -> str:
    """A portfolio's public id: ``token_hex(12)`` (24 hex chars, 96 random bits), never derived from the profile id.

    The profile id is the owner's capability; a share link carries this one instead, so publishing a page never
    reveals (or lets anyone guess) the id that edits it.
    """
    return secrets.token_hex(12)


def is_sample_id(pid: str) -> bool:
    """True for ids minted by `new_profile_id(sample=True)` (and the old ``sample-...`` ids)."""
    return pid.startswith(SAMPLE_PREFIX)


def is_valid_id(s: object) -> TypeGuard[str]:
    """True when ``s`` is a string of the legacy id shape.

    Unlike the legacy ``re.match(r"^...$")`` this uses ``fullmatch``, so a trailing newline is rejected
    (``$`` would have accepted ``"abc\n"``); everything else the old pattern accepted still passes.
    """
    return isinstance(s, str) and _ID_RE.fullmatch(s) is not None


def uuid7(*, timestamp_ms: int | None = None) -> uuid.UUID:
    """A UUID version 7 (RFC 9562): 48-bit Unix milliseconds, then 74 random bits.

    Python 3.12/3.13 have no ``uuid.uuid7``. ``timestamp_ms`` is for tests; it defaults to now.
    """
    ms = (time.time_ns() // 1_000_000) if timestamp_ms is None else timestamp_ms
    if ms < 0:
        raise ValueError("timestamp_ms must be >= 0")
    rand = int.from_bytes(secrets.token_bytes(10), "big")  # 80 random bits; 74 are used
    rand_a = (rand >> 68) & 0xFFF  # 12 bits
    rand_b = rand & _MASK_62  # 62 bits
    value = ((ms & _MASK_48) << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


__all__ = [
    "SAMPLE_PREFIX",
    "is_sample_id",
    "is_valid_id",
    "new_match_id",
    "new_profile_id",
    "new_public_id",
    "new_session_id",
    "uuid7",
]
