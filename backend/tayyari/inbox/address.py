"""Tracking addresses: ``<first name>.<last initial><5 random>@<inbound domain>`` (``jordan.k7m4q@apply.leafout.in``).

The first name is folded to plain ASCII lower-case letters (``Zoë`` -> ``zoe``, ``Ana-María`` -> ``anamaria``), at most 20,
``me`` when nothing is left; the last-name initial is added when there is a last name; the five characters come from an
alphabet with no look-alikes (no ``i``, ``l``, ``o``, ``0`` or ``1``), drawn with `secrets`. No plus-tags, ever.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from collections.abc import Callable
from typing import Final

ALPHABET: Final = "abcdefghjkmnpqrstuvwxyz23456789"
RANDOM_CHARS: Final = 5
FIRST_MAX: Final = 20
FALLBACK_FIRST: Final = "me"
LOCAL_PART_RE: Final = re.compile(r"^[a-z]{1,20}\.(?:[a-z])?[" + ALPHABET + r"]{5}$")
"""What a local part we made looks like."""


def fold(text: str | None) -> str:
    """``text`` as plain lower-case ASCII letters only (accents dropped, everything else removed)."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z]", "", ascii_text)


def name_parts(full_name: str | None) -> tuple[str | None, str | None]:
    """The first and last word of a full name (the last is None for a one-word name)."""
    words = (full_name or "").split()
    if not words:
        return None, None
    return words[0], (words[-1] if len(words) > 1 else None)


def random_suffix(choice: Callable[[str], str] = secrets.choice) -> str:
    return "".join(choice(ALPHABET) for _ in range(RANDOM_CHARS))


def make_local_part(
    first_name: str | None, last_name: str | None, *, choice: Callable[[str], str] = secrets.choice
) -> str:
    """A fresh local part for a person; a new draw each call (the caller retries on a collision)."""
    first = fold(first_name)[:FIRST_MAX] or FALLBACK_FIRST
    initial = fold(last_name)[:1]
    return f"{first}.{initial}{random_suffix(choice)}"


def address_of(local_part: str, domain: str) -> str:
    return f"{local_part}@{domain}"


__all__ = [
    "ALPHABET",
    "FALLBACK_FIRST",
    "FIRST_MAX",
    "LOCAL_PART_RE",
    "RANDOM_CHARS",
    "address_of",
    "fold",
    "make_local_part",
    "name_parts",
    "random_suffix",
]
