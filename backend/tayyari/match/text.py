"""Text helpers shared by the match modules: the one place that decides how two pieces of text are compared.

`fold_text` is the comparison form used for quotes: NFKC, invisible characters gone, quote marks and dashes made
plain, list markers at the start of a line dropped, whitespace collapsed, case folded. A model's quote must be a
passage of its source *after the same folding*, so a curly quote, an en dash or a wrapped line never decides it.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from typing import Final

from tayyari.match.aliases import term_variants
from tayyari.portfolio.ground import Corpus, measures_of, tokens_of

_QUOTES: Final = str.maketrans(
    {
        0x2018: "'",
        0x2019: "'",
        0x201A: "'",
        0x201B: "'",
        0x2032: "'",
        0x00B4: "'",
        0x0060: "'",
        0x201C: '"',
        0x201D: '"',
        0x201E: '"',
        0x201F: '"',
        0x2033: '"',
        0x00AB: '"',
        0x00BB: '"',
    }
)
_DASHES: Final = str.maketrans(
    dict.fromkeys(
        (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE58, 0xFE63, 0xFF0D),
        "-",
    )
)
_INVISIBLE: Final = re.compile("[​‌‍⁠﻿­]")
_SPACE: Final = re.compile(r"\s+")
_LIST_MARK: Final = re.compile(r"^[ \t]*(?:[-*+•·▪●◦‣>]|\d{1,2}[.)])[ \t]+", re.MULTILINE)
_URL: Final = re.compile(r"https?://[^\s<>\"'`)\]]+|www\.[^\s<>\"'`)\]]+", re.IGNORECASE)
_WORD: Final = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")
_TRAILING_URL_PUNCT: Final = ".,;:!?)]}'\""

MIN_QUOTE_CHARS: Final = 4
"""A quote shorter than this (once folded) proves nothing."""


def fold_text(text: str) -> str:
    """The comparison form of ``text`` (see the module docstring)."""
    plain = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text)).translate(_QUOTES).translate(_DASHES)
    plain = _LIST_MARK.sub("", plain)
    return _SPACE.sub(" ", plain).strip().casefold()


def quote_in(quote: str, folded_haystack: str) -> bool:
    """Is ``quote`` (folded here) a passage of ``folded_haystack`` (already folded with `fold_text`)?"""
    needle = fold_text(quote).strip("\"' ")  # a model may wrap a quote in quote marks of its own
    return len(needle) >= MIN_QUOTE_CHARS and needle in folded_haystack


def posting_hash(text: str) -> str:
    """sha256 over the folded posting text: the cache key of a read (the same posting pasted twice agrees)."""
    return hashlib.sha256(fold_text(text).encode("utf-8")).hexdigest()


def short_hash(*parts: str, length: int = 10) -> str:
    """A short stable id from parts (their order matters)."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()[:length]


def word_count(text: str) -> int:
    """How many words ``text`` has (letters and digits, with inner hyphens and apostrophes)."""
    return len(_WORD.findall(text))


def urls_in(text: str, *, limit: int = 20) -> list[str]:
    """Web addresses written in ``text``, in order, without repeats. They are listed, never opened."""
    out: dict[str, None] = {}
    for m in _URL.finditer(text):
        url = m.group().rstrip(_TRAILING_URL_PUNCT)
        if url:
            out.setdefault(url, None)
        if len(out) >= limit:
            break
    return list(out)


def tidy(text: str) -> str:
    """Whitespace collapsed to single spaces."""
    return " ".join(text.split())


def clip(text: str, limit: int) -> str:
    """``text`` tidied and cut to ``limit`` characters at a word boundary (an ellipsis marks the cut)."""
    one = tidy(text)
    if len(one) <= limit:
        return one
    head = one[: max(0, limit - 1)]
    space = head.rfind(" ")
    return f"{(head[:space] if space > limit * 0.5 else head).rstrip(' ,;:-')}…"


def _short_word(word: str, raw: str, *, ignore_case: bool) -> bool:
    flags = re.IGNORECASE if ignore_case else 0
    return re.search(rf"(?<![\w+#.&]){re.escape(word)}(?![\w+#&])", raw, flags) is not None


def term_in_corpus(term: str, corpus: Corpus, raw: str = "") -> bool:
    """Does ``corpus`` (the words of some facts) contain ``term``, or an orthographic alias of it?

    A one- or two-letter word (``R``, ``Go``, ``JS``, ``C#``) is looked up as a whole word in ``raw``, since folded
    tokens of that size are everywhere (``R&D`` and ``Next.js`` hold an ``r`` and a ``js``): case-sensitively when the
    term has no alias, case-insensitively for the short spelling of an alias group.
    """
    cleaned = term.strip()
    if not cleaned:
        return False
    variants = term_variants(cleaned)
    for variant in variants:
        if len(variant) <= 2:
            if raw and _short_word(
                cleaned if len(variants) == 1 else variant, raw, ignore_case=len(variants) > 1
            ):
                return True
            continue
        parts = tokens_of(variant)
        if not parts:
            continue
        if len(parts) == 1:
            if corpus.has_word(parts[0]):
                return True
        elif corpus.has_sequence(parts) or corpus.has_sequence([*parts[:-1], _other_number(parts[-1])]):
            return True
    return False


def _other_number(word: str) -> str:
    """The plural of a singular word and the singular of a plural one (``system`` / ``systems``)."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return f"{word}s"


def terms_in_text(text: str, terms: Iterable[str]) -> list[str]:
    """The ``terms`` that ``text`` itself uses (alias-aware), in the order given and without repeats."""
    corpus = Corpus.of(text)
    seen: dict[str, None] = {}
    for term in terms:
        if term and term not in seen and term_in_corpus(term, corpus, text):
            seen[term] = None
    return list(seen)


__all__ = [
    "MIN_QUOTE_CHARS",
    "clip",
    "fold_text",
    "measures_of",
    "posting_hash",
    "quote_in",
    "short_hash",
    "term_in_corpus",
    "terms_in_text",
    "tidy",
    "tokens_of",
    "urls_in",
    "word_count",
]
