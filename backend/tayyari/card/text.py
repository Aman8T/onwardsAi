"""Text on the card: made safe for XML, measured without a font, and clipped to fit.

The card is an image that GitHub's proxy and LinkedIn fetch, so nothing can be measured in a browser: widths come from a table
of Arial's advance widths (the metric twin of Helvetica, close to Segoe UI and San Francisco), github-readme-stats' method.
The table errs wide (`SAFETY`), so a clipped name fits on every system font in the stack. Characters outside ASCII count as
0.6 em, and wide East Asian ones as a full em.
"""

from __future__ import annotations

import unicodedata
from typing import Final
from xml.sax.saxutils import escape

ELLIPSIS: Final = "…"
SAFETY: Final = 1.06
BOLD: Final = 1.07
"""Weight 600 sets about 7 % wider than 400 in the system fonts."""

# Arial advance widths in thousandths of an em, for ASCII 32 (space) to 126 (~).
_ASCII: Final[tuple[int, ...]] = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,  # space ! " # $ % & ' ( ) * + , - . /
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556,  # 0-9
    278, 278, 584, 584, 584, 556, 1015,  # : ; < = > ? @
    667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,  # A-M
    722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611,  # N-Z
    278, 278, 278, 469, 556, 333,  # [ \ ] ^ _ `
    556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833,  # a-m
    556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500,  # n-z
    334, 260, 334, 584,  # { | } ~
)  # fmt: skip


def clean(text: str) -> str:
    """``text`` with what XML 1.0 cannot hold removed (control characters become spaces), runs of space collapsed."""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code < 0x20 or 0x7F <= code < 0xA0:
            out.append(" ")
        elif 0xD800 <= code <= 0xDFFF or code in (0xFFFE, 0xFFFF):
            continue
        else:
            out.append(ch)
    return " ".join("".join(out).split())


def xml_text(text: str) -> str:
    """``text`` cleaned and escaped for a text node or a double-quoted attribute."""
    return escape(clean(text), {'"': "&quot;"})


def _em(ch: str) -> float:
    code = ord(ch)
    if 32 <= code <= 126:
        return _ASCII[code - 32] / 1000
    if unicodedata.combining(ch):
        return 0.0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 1.0
    return 0.6


def measure(text: str, size: float, *, bold: bool = False) -> float:
    """The width of ``text`` set at ``size`` px, erring wide."""
    width = sum(_em(ch) for ch in text) * size * SAFETY
    return width * BOLD if bold else width


def clip(text: str, size: float, budget: float, *, bold: bool = False) -> str:
    """``text`` (cleaned) as it fits in ``budget`` px: whole, or cut at a character with an ellipsis."""
    text = clean(text)
    if measure(text, size, bold=bold) <= budget:
        return text
    room = budget - measure(ELLIPSIS, size, bold=bold)
    kept, used = [], 0.0
    for ch in text:
        step = measure(ch, size, bold=bold)
        if used + step > room:
            break
        kept.append(ch)
        used += step
    head = "".join(kept).rstrip(" -–—,&·/")
    return f"{head}{ELLIPSIS}" if head else ""


def wrap(text: str, size: float, budget: float, lines: int, *, bold: bool = False) -> list[str]:
    """``text`` broken at spaces into at most ``lines`` lines of ``budget`` px; the last one is clipped when it overflows."""
    words = clean(text).split(" ")
    out: list[str] = []
    current = ""
    for i, word in enumerate(words):
        trial = f"{current} {word}" if current else word
        if measure(trial, size, bold=bold) <= budget or not current:
            current = trial
            continue
        out.append(current)
        if len(out) == lines - 1:
            current = " ".join(words[i:])
            break
        current = word
    out.append(current)
    return [clip(line, size, budget, bold=bold) for line in out[:lines]]


__all__ = ["ELLIPSIS", "clean", "clip", "measure", "wrap", "xml_text"]
