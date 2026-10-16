"""Small, pure normalisers: titles, apply links, ids and hashes.

Nothing here touches the network. An apply link is the employer's own posting with the tracking parameters taken out and
nothing added; a listing without a usable link is not shown (no reliable link, no card).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final
from urllib.parse import SplitResult, unquote_plus, urlsplit, urlunsplit

from tayyari.core.hashing import sha256_hex

TITLE_NORM_MAX_CHARS: Final = 300
LISTING_ID_CHARS: Final = 32
TRACKING_KEYS: Final = frozenset({"gclid", "fbclid", "mc_cid", "mc_eid"})
"""Query parameters that only measure where a click came from (``utm_*`` and ``lever-source`` are matched by prefix)."""
_LISTING_ID = re.compile(r"[0-9a-f]{32}")

_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}")
_NON_WORD = re.compile(r"[^a-z0-9+#]+")
_TITLE_REWRITES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"\bfront[\s-]+end\b"), "frontend"),
    (re.compile(r"\bback[\s-]+end\b"), "backend"),
    (re.compile(r"\bfull[\s-]+stack\b"), "fullstack"),
    (re.compile(r"\bsr\b\.?"), "senior"),
    (re.compile(r"\bjr\b\.?"), "junior"),
    (re.compile(r"\bmgr\b\.?"), "manager"),
    (re.compile(r"\bdev\b"), "developer"),
    (re.compile(r"\bui\s*/\s*ux\b"), "ui ux"),
)
_TITLE_STOP: Final = frozenset({"and", "of", "the", "for", "with", "in", "at", "a", "an", "to", "on", "or"})
_LEVELS: Final = frozenset({"i", "ii", "iii", "iv", "v"})
_ENTRY_WORDS = re.compile(
    r"\b(?:interns?|internship|trainees?|freshers?|junior|jr|entry[\s-]?level|apprentice(?:ship)?|early[\s-]career|"
    r"new[\s-]grad|campus|graduate\s+(?:engineer|trainee|program\w*|associate))\b",
    re.IGNORECASE,
)
_ENTRY_LEVEL_I = re.compile(
    r"\b(?:engineer|analyst|developer|associate|specialist|scientist|executive|designer|accountant|officer)\s+(?:i|1)\s*$",
    re.IGNORECASE,
)
_ACRONYM = re.compile(r"^[A-Z][A-Z0-9&/+.-]{1,}$")


def normalise_title(title: str) -> str:
    """A comparison form of a job title: lower case, brackets and punctuation gone, ``front end`` read as ``frontend``,
    ``Sr.`` as ``senior``. Seniority words stay (``staff`` and ``senior`` are different roles). At most 300 characters."""
    text = unicodedata.normalize("NFKC", title).lower().replace("&", " and ")
    text = _BRACKETS.sub(" ", text)
    for pattern, replacement in _TITLE_REWRITES:
        text = pattern.sub(replacement, text)
    return " ".join(_NON_WORD.sub(" ", text).split())[:TITLE_NORM_MAX_CHARS]


_TOKEN_SYNONYMS: Final[dict[str, tuple[str, ...]]] = {
    "developer": ("engineer",),
    "programmer": ("engineer",),
    "ui": ("frontend",),
    "sde": ("software", "engineer"),
    "swe": ("software", "engineer"),
}


def title_tokens(title_norm: str) -> frozenset[str]:
    """The words of a normalised title that carry meaning: no filler words, no level numerals (``II``, ``3``), and the
    everyday synonyms read as one (a developer is an engineer, a UI engineer builds the frontend)."""
    tokens: set[str] = set()
    for token in title_norm.split():
        if token in _TITLE_STOP or token in _LEVELS or token.isdigit():
            continue
        tokens.update(_TOKEN_SYNONYMS.get(token, (token,)))
    return frozenset(tokens)


def title_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Token-set (Jaccard) similarity of two titles, from 0 to 1; 0 when either has no words."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_entry_level_title(title: str) -> bool:
    """Whether a title says it is for someone starting out (intern, trainee, graduate, junior, fresher, ``Engineer I``)."""
    return _ENTRY_WORDS.search(title) is not None or _ENTRY_LEVEL_I.search(title.strip()) is not None


def sentence_case(title: str) -> str:
    """``Senior Frontend Engineer`` as ``Senior frontend engineer``; an acronym such as ``UI`` or ``QA`` keeps its capitals."""
    words = title.split()
    out: list[str] = []
    for index, word in enumerate(words):
        if len(word) > 1 and _ACRONYM.match(word):
            out.append(word)
        elif index == 0:
            out.append(word[:1].upper() + word[1:].lower())
        else:
            out.append(word.lower())
    return " ".join(out)


# ───────────────────────── links and ids ─────────────────────────


def _is_tracking(piece: str) -> bool:
    key = unquote_plus(piece.split("=", 1)[0]).strip().lower()
    return key.startswith(("utm_", "lever-source")) or key in TRACKING_KEYS


def clean_apply_url(url: str | None) -> str | None:
    """The link with tracking parameters removed (``utm_*``, ``gclid``, ``fbclid``, ``mc_cid``, ``mc_eid``, ``lever-source``),
    everything else kept (``gh_jid`` is needed). None when it is not an ``http(s)`` link with a host."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    query = "&".join(piece for piece in parts.query.split("&") if piece and not _is_tracking(piece))
    cleaned = SplitResult(parts.scheme, parts.netloc, parts.path, query, parts.fragment)
    return urlunsplit(cleaned)


def apply_host(url: str) -> str:
    """The host of the link without ``www.`` (``jobs.lever.co``)."""
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def listing_id(source: str, board: str, external_id: str) -> str:
    """A stable id for a listing: 32 hex characters of the hash of its source, board and the employer's own id."""
    return sha256_hex(f"{source}:{board}:{external_id}")[:LISTING_ID_CHARS]


def is_listing_id(value: object) -> bool:
    return isinstance(value, str) and _LISTING_ID.fullmatch(value) is not None


def content_hash(*parts: object) -> str:
    """A hash of what a refresh would rewrite if it changed (title, place, link, salary, text)."""
    return sha256_hex("\x1f".join("" if part is None else str(part) for part in parts))


__all__ = [
    "LISTING_ID_CHARS",
    "TITLE_NORM_MAX_CHARS",
    "TRACKING_KEYS",
    "apply_host",
    "clean_apply_url",
    "content_hash",
    "is_entry_level_title",
    "is_listing_id",
    "listing_id",
    "normalise_title",
    "sentence_case",
    "title_similarity",
    "title_tokens",
]
