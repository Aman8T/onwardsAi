"""Hashing helpers: canonical JSON, sha256 and keyed HMAC digests.

The decision log deduplicates Jev states by the sha256 of their canonical JSON, and request logs store
only a keyed hash of the client IP, so both must be stable across processes and releases.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

_MAX_HEX = 64


def canonical_json(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON: sorted keys, no whitespace, non-ASCII kept as is.

    Raises ``ValueError`` for NaN and infinity and ``TypeError`` for values JSON cannot represent, rather
    than hashing something that would not round-trip.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )


def sha256_hex(data: bytes | str) -> str:
    """Hex sha256 digest (64 characters); a ``str`` is hashed as UTF-8."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def hmac_hex(key: bytes, value: str, length: int = 16) -> str:
    """The first ``length`` hex characters (1 to 64) of HMAC-SHA256(key, value)."""
    if not 1 <= length <= _MAX_HEX:
        raise ValueError(f"length must be between 1 and {_MAX_HEX}")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:length]


__all__ = ["canonical_json", "hmac_hex", "sha256_hex"]
