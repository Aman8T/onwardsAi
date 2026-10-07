"""Free-text context the user pasted (LinkedIn About section, goals, anything)."""

from __future__ import annotations

from ..core import limits
from .classify import SourceDoc
from .terms import find_terms

MAX_NOTES_CHARS = limits.NOTES_MAX_CHARS
"""Pasted notes are cut to this many characters (the form field cap is the same number)."""


def _normalise(text: str) -> str:
    """Unify line endings, drop trailing blanks and collapse runs of blank lines (linear time)."""
    out: list[str] = []
    blank = 0
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.rstrip(" \t")
        blank = 0 if line else blank + 1
        if line or blank == 1:
            out.append(line)
    return "\n".join(out).strip()


def ingest_notes(text: str) -> SourceDoc:
    """Pasted notes as a source. Never fails."""
    pasted = (text or "").replace("\x00", "")  # Postgres refuses a NUL in TEXT and JSONB
    content = _normalise(pasted)[:MAX_NOTES_CHARS]
    return SourceDoc(
        id="notes",
        kind="notes",
        label="Notes",
        src="Pasted context",
        kind_label="Extra context",
        text=content,
        meta={"chars": len(content), "words": len(content.split())},
        quick_signals=find_terms(content, 8),
    )
