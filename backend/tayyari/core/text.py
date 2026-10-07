"""String helpers that used to be copied between the legacy modules.

Each function keeps the exact behaviour of its legacy original (the unit tests compare them against the
legacy functions on a table of inputs), because the output feeds the UI, ids and the golden baselines.
Do not "improve" them without regenerating those baselines.

    tidy / squash / one_line   whitespace collapsing          clip                  prompt-text cut
    clip_words / short_label   UI-string cuts                 plural                "1 repo", "2 repos"
    loose_key / slug           comparison keys and ids        js_round              JavaScript Math.round
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from .limits import SLUG_MAX_CHARS

_TRAILING_PUNCT = re.compile(r"[\s,;:·-]+$")
"""Separators left dangling where `clip_words` cut the text (the input is at most a few hundred characters)."""
_LABEL_TRAILING = re.compile(r"[\s&,;:·-]+$")
_COMBINING = re.compile("[\u0300-\u036f]")
_LOOSE_DROP = re.compile(r"[^a-z0-9+#]+")
_SLUG_DROP = re.compile(r"[^a-z0-9]+")

_CLIP_MARKER = "\n…[trimmed]"
_CLIP_MARKER_RESERVE = 20
"""Characters kept free for the marker when `clip` cuts (the legacy value; the marker itself is shorter)."""
_CLIP_MIN_FOR_MARKER = 40


def squash(s: str) -> str:
    """Collapse every run of whitespace to one space and trim both ends.

    Legacy: ``ingest.web._squash``. Strings only; use `tidy` for untrusted values.
    """
    return " ".join(s.split())


def one_line(s: str) -> str:
    """The same text on a single line: whitespace runs (newlines included) become one space.

    Legacy: ``ingest.github._one_line``. Identical to `squash`; the name says what the caller wants.
    """
    return " ".join(s.split())


def tidy(s: Any) -> str:
    """`squash` for any value: a string is collapsed, anything else (None, numbers, lists) becomes ``""``.

    Legacy: ``legacy.pipeline.tidy``.
    """
    return " ".join(s.split()) if isinstance(s, str) else ""


def clip(text: str, max_len: int) -> str:
    """Cut prompt text to ``max_len`` characters and mark the cut. Never use it for UI strings.

    Legacy: ``legacy.prompts.clip``. Under 40 characters of room the text is cut without a marker.
    """
    if len(text) <= max_len:
        return text
    if max_len < _CLIP_MIN_FOR_MARKER:
        return text[: max(0, max_len)]
    return f"{text[: max_len - _CLIP_MARKER_RESERVE].rstrip()}{_CLIP_MARKER}"


def clip_words(s: Any, max_len: int) -> str:
    """Tidy ``s`` and cut it at a word boundary to at most ``max_len`` characters, adding ``…`` when cut.

    Legacy: ``legacy.pipeline.clip_words`` (the UI-string clip, unlike `clip` it leaves no marker).
    """
    t = tidy(s)
    if len(t) <= max_len:
        return t
    cut = t[: max_len - 1]
    sp = cut.rfind(" ")
    head = cut[:sp] if sp > max_len * 0.5 else cut
    return f"{_TRAILING_PUNCT.sub('', head)}…"


def short_label(s: Any, max_len: int) -> str:
    """A node label: whole words up to ``max_len`` characters ("Frontend Engineering" becomes "Frontend").

    No ellipsis unless a single word is already too long. Legacy: ``legacy.pipeline.short_label``.
    """
    t = tidy(s)
    if len(t) <= max_len:
        return t
    out = ""
    for w in t.split(" "):
        nxt = f"{out} {w}" if out else w
        if len(nxt) > max_len:
            break
        out = nxt
    out = _LABEL_TRAILING.sub("", out)
    return out or f"{t[: max_len - 1]}…"


def plural(n: int, word: str, plural_word: str | None = None) -> str:
    """``"1 repo"``, ``"2 repos"``; pass ``plural_word`` for irregular nouns ("case study", "case studies").

    Legacy: ``ingest.classify.plural`` (``legacy.pipeline.plural`` is the same without ``plural_word``).
    """
    return f"{n} {word if n == 1 else (plural_word or word + 's')}"


def loose_key(s: Any) -> str:
    """A loose comparison key: lower-cased, everything except letters, digits, ``+`` and ``#`` dropped.

    ``"REST & GraphQL"`` and ``"rest-graphql"`` share a key. Legacy: ``legacy.pipeline.loose_key``.
    """
    return _LOOSE_DROP.sub("", tidy(s).lower())


def slug(s: str) -> str:
    """A kebab-case id of at most `SLUG_MAX_CHARS` (40) characters ("C++ & Rust" becomes "cpp-and-rust"); ``"item"`` if empty.

    Accents are stripped (NFKD), ``&`` reads as "and", ``++`` as "pp" and ``#`` as "sharp".
    Legacy: ``legacy.pipeline.slug``.
    """
    t = unicodedata.normalize("NFKD", tidy(s).lower())
    t = _COMBINING.sub("", t)
    t = t.replace("&", " and ").replace("++", "pp").replace("#", "sharp")
    t = _SLUG_DROP.sub("-", t).strip("-")[:SLUG_MAX_CHARS].rstrip("-")
    return t or "item"


def js_round(v: float) -> int:
    """JavaScript's ``Math.round``: halves round towards +infinity (Python's ``round`` is banker's rounding).

    ``js_round(2.5) == 3``, ``js_round(-2.5) == -2``. Legacy: ``legacy.pipeline._js_round`` and
    ``ingest.github._js_round``. Raises for NaN and infinity, as the originals did.
    """
    return math.floor(v + 0.5)


__all__ = [
    "clip",
    "clip_words",
    "js_round",
    "loose_key",
    "one_line",
    "plural",
    "short_label",
    "slug",
    "squash",
    "tidy",
]
