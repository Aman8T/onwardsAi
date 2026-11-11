"""The resume source of "Update my graph from all my blocks": every stored resume file of a profile and the lines the
person said in their own words, as one text the analysis reads exactly as it reads an uploaded resume.

Pure code, no model call. The text is data for the readers (fenced as untrusted like any resume text); the only words
written here are two headings: a plain one for each file (its name and date) and one for the person's own statements,
so a citation keeps its origin visible.

    files      oldest first, de-duplicated by the sha256 of their text (the newest copy stays), empty texts skipped
    the cap    `limit` characters in all (the analysis reads at most `RESUME_MAX_CHARS` of a resume). The own-words
               section is reserved first, then files are taken newest first and kept whole; what does not fit is named
               in `RereadSource.note`, never dropped silently
    fallback   a profile analysed before files were kept has no stored file: the resume text its stored context keeps
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from tayyari.contract.match import Fact
from tayyari.core.limits import RESUME_MAX_CHARS
from tayyari.core.text import plural
from tayyari.resume.files import ResumeText
from tayyari.resume.service import MINE_KEY

OWN_WORDS_HEADING = "In your own words (stated by you, not from a document)"
FALLBACK_HEADING = "Resume (the text your last analysis kept)"
OWN_WORDS_MAX_CHARS = 4_000
"""At most this much of the text goes to the person's own lines (the newest are kept)."""
SOURCE_NAME = "All your resume files.txt"
"""The name the combined text goes by: a text file, so the resume reader takes it as it is."""


def file_heading(filename: str, day: str) -> str:
    return f"Resume file: {filename} ({day})"


@dataclass(frozen=True, slots=True)
class RereadSource:
    """The built text and what it holds."""

    text: str
    files: int
    own_words: int
    left_out: tuple[str, ...]
    duplicates: int
    from_stored_context: bool
    note: str


def _flat(text: str) -> str:
    return " ".join(text.replace("\x00", "").split())


def _digest(text: str) -> str:
    return hashlib.sha256(_flat(text).casefold().encode()).hexdigest()


def statements(facts: Sequence[Fact]) -> list[str]:
    """The text of the facts the person typed on a resume and vouched for (`resume.service._own_words`), as given."""
    return [
        f.text
        for f in facts
        if f.refs and all(r.source == "user" and r.docId.startswith(MINE_KEY) for r in f.refs)
    ]


def own_words_lines(statements: Sequence[str]) -> list[str]:
    """The person's statements, one line each, de-duplicated (case aside), in the order given."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in statements:
        line = _flat(raw)
        key = line.casefold()
        if line and key not in seen:
            seen.add(key)
            out.append(line)
    return out


def _own_section(lines: Sequence[str]) -> tuple[str, int, int]:
    """The section's text, how many lines it holds and how many (the oldest) did not fit."""
    kept = list(lines)
    while kept and len(OWN_WORDS_HEADING) + sum(len(x) + 3 for x in kept) > OWN_WORDS_MAX_CHARS:
        kept.pop(0)
    if not kept:
        return "", 0, len(lines)
    body = "\n".join(f"- {x}" for x in kept)
    return f"{OWN_WORDS_HEADING}\n{body}", len(kept), len(lines) - len(kept)


def _clip_note(left_out: Sequence[str], own_dropped: int, limit: int) -> str:
    parts: list[str] = []
    if left_out:
        parts.append(f"left out, over the {limit:,}-character limit: {', '.join(left_out)}")
    if own_dropped:
        parts.append(f"left out, the oldest {plural(own_dropped, 'line')} in your own words")
    return "; ".join(parts)


def build_source(
    texts: Sequence[ResumeText],
    own_words: Sequence[str],
    stored_resume: str | None,
    *,
    limit: int = RESUME_MAX_CHARS,
) -> RereadSource | None:
    """The text of the re-read, or None when there is nothing to read (no file with text and no stored resume text).

    ``texts`` is `ResumeService.all_resume_texts` (newest first); ``own_words`` the person's statements (oldest first);
    ``stored_resume`` the resume text the profile's stored context keeps, used only when no file has text.
    """
    section, own_count, own_dropped = _own_section(own_words_lines(own_words))
    reserve = len(section) + (2 if section else 0)

    chosen: list[tuple[str, str]] = []  # (heading, text), newest first
    left_out: list[str] = []
    duplicates = 0
    seen: set[str] = set()
    budget = limit - reserve
    for item in texts:
        body = item.text.replace("\x00", "").strip()
        if not body:
            continue
        digest = _digest(body)
        if digest in seen:
            duplicates += 1
            continue
        seen.add(digest)
        heading = file_heading(_flat(item.filename) or "resume", item.created_at.date().isoformat())
        cost = len(heading) + len(body) + 3
        if cost <= budget:
            chosen.append((heading, body))
            budget -= cost
        elif not chosen:
            # The newest file alone is longer than the cap: it is kept as far as it goes, and the cut is said.
            room = max(budget - len(heading) - 3, 0)
            if room > 0:
                chosen.append((heading, body[:room].rstrip()))
                left_out.append(f"the end of {_flat(item.filename) or 'resume'}")
                budget = 0
            else:
                left_out.append(_flat(item.filename) or "resume")
        else:
            left_out.append(f"{_flat(item.filename) or 'resume'} ({item.created_at.date().isoformat()})")

    from_context = False
    if not chosen and not left_out:
        stored = (stored_resume or "").replace("\x00", "").strip()
        if not stored:
            return None
        from_context = True
        chosen.append((FALLBACK_HEADING, stored[: max(budget - len(FALLBACK_HEADING) - 3, 0)].rstrip()))
        if len(stored) > len(chosen[0][1]):
            left_out.append("the end of the stored resume text")

    blocks = [f"{heading}\n{body}" for heading, body in reversed(chosen)]
    if section:
        blocks.append(section)
    text = "\n\n".join(blocks)[:limit]

    reading = (
        "the resume text kept from your last analysis" if from_context else plural(len(chosen), "resume file")
    )
    note = f"Reading {reading}"
    if own_count:
        note += f" and {plural(own_count, 'line')} in your own words"
    if duplicates:
        note += f" ({plural(duplicates, 'copy', 'copies')} of a file skipped)"
    clip = _clip_note(left_out, own_dropped, limit)
    note += f"; {clip}" if clip else ""
    return RereadSource(
        text=text,
        files=0 if from_context else len(chosen),
        own_words=own_count,
        left_out=tuple(left_out),
        duplicates=duplicates,
        from_stored_context=from_context,
        note=note,
    )


__all__ = [
    "FALLBACK_HEADING",
    "OWN_WORDS_HEADING",
    "OWN_WORDS_MAX_CHARS",
    "SOURCE_NAME",
    "RereadSource",
    "build_source",
    "file_heading",
    "own_words_lines",
    "statements",
]
