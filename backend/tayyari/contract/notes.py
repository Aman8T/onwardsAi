"""Pydantic mirror of shared/notes.ts: the dashboard's notes that follow the account.

The text is the person's own words: `normalise_notes` trims and caps it and strips NULs, and nothing ever interprets it.
`tests/unit/contract/test_notes_parity.py` fails when this file and the TypeScript drift.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from tayyari.contract.models import _Model

NOTES_MAX: Final = 12
NOTE_TEXT_MAX: Final = 240
NOTE_LABEL_MAX: Final = 60
NOTE_BY_MAX: Final = 120
NOTE_ID_MAX: Final = 64

DeskNoteKind = Literal["own", "pinned"]


class DeskNote(_Model):
    id: str
    kind: DeskNoteKind
    text: str
    label: str | None = None
    by: str | None = None
    at: int = Field(ge=0)


class DeskNotes(_Model):
    notes: list[DeskNote]


def _clean(value: str, cap: int) -> str:
    """NULs out, whitespace runs folded to one space, trimmed, capped."""
    return " ".join(value.replace("\x00", "").split())[:cap]


def normalise_notes(body: DeskNotes) -> DeskNotes:
    """The list as it is stored. Raises `ValueError` (a sentence) for more than `NOTES_MAX` notes, an id that is empty,
    too long or used twice. A note whose text is empty once trimmed is dropped; a blank label or ``by`` is left out."""
    if len(body.notes) > NOTES_MAX:
        raise ValueError(f"at most {NOTES_MAX} notes")
    seen: set[str] = set()
    out: list[DeskNote] = []
    for note in body.notes:
        note_id = note.id.replace("\x00", "").strip()
        if not note_id or len(note_id) > NOTE_ID_MAX:
            raise ValueError("a note id is empty or too long")
        if note_id in seen:
            raise ValueError("a note id is used twice")
        seen.add(note_id)
        text = _clean(note.text, NOTE_TEXT_MAX)
        if not text:
            continue
        out.append(
            DeskNote(
                id=note_id,
                kind=note.kind,
                text=text,
                label=(_clean(note.label, NOTE_LABEL_MAX) or None) if note.label is not None else None,
                by=(_clean(note.by, NOTE_BY_MAX) or None) if note.by is not None else None,
                at=note.at,
            )
        )
    return DeskNotes(notes=out)


__all__ = [
    "NOTES_MAX",
    "NOTE_BY_MAX",
    "NOTE_ID_MAX",
    "NOTE_LABEL_MAX",
    "NOTE_TEXT_MAX",
    "DeskNote",
    "DeskNoteKind",
    "DeskNotes",
    "normalise_notes",
]
