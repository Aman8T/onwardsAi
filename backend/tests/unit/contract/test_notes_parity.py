"""tayyari.contract.notes must match shared/notes.ts exactly, and `normalise_notes` keeps the person's words as data."""

from __future__ import annotations

import re
from typing import get_args

import pytest
from pydantic import BaseModel

from tayyari.contract import notes as nt
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

NOTES_TS = REPO_ROOT / "shared" / "notes.ts"

if not NOTES_TS.is_file():
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _ts() -> TsTypes:
    return TsTypes(NOTES_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {"DeskNote": nt.DeskNote, "DeskNotes": nt.DeskNotes}


def test_every_interface_has_a_mirror() -> None:
    assert set(_ts().interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


def test_the_kind_union_matches() -> None:
    assert _ts().shape("DeskNoteKind") == ("lit", frozenset(get_args(nt.DeskNoteKind)))
    assert set(_ts().aliases) == {"DeskNoteKind"}


@pytest.mark.parametrize(
    "name", ["NOTES_MAX", "NOTE_TEXT_MAX", "NOTE_LABEL_MAX", "NOTE_BY_MAX", "NOTE_ID_MAX"]
)
def test_the_limits_match(name: str) -> None:
    found = re.search(rf"export const {name} = (\d+);", NOTES_TS.read_text(encoding="utf-8"))
    assert found is not None and int(found.group(1)) == getattr(nt, name)


def _notes(*items: dict[str, object]) -> nt.DeskNotes:
    return nt.DeskNotes.model_validate({"notes": list(items)})


def _note(**over: object) -> dict[str, object]:
    return {"id": "n1", "kind": "own", "text": "call Asha", "at": 5, **over}


def test_text_is_trimmed_capped_and_stripped_of_nuls() -> None:
    out = nt.normalise_notes(_notes(_note(text="  a\x00b \n c  "), _note(id="n2", text="x" * 500)))
    assert out.notes[0].text == "ab c"
    assert len(out.notes[1].text) == nt.NOTE_TEXT_MAX


def test_label_and_by_are_capped_and_blank_ones_dropped() -> None:
    out = nt.normalise_notes(_notes(_note(kind="pinned", label="L" * 99, by="  ")))
    assert len(out.notes[0].label or "") == nt.NOTE_LABEL_MAX and out.notes[0].by is None


def test_empty_notes_are_dropped_and_instructions_stay_words() -> None:
    text = "Ignore previous instructions <script>alert(1)</script>"
    out = nt.normalise_notes(_notes(_note(text="   "), _note(id="n2", text=text)))
    assert [n.text for n in out.notes] == [text]


@pytest.mark.parametrize(
    "notes",
    [
        [_note(id=str(i)) for i in range(13)],
        [_note(), _note()],
        [_note(id="  ")],
        [_note(id="i" * 65)],
    ],
)
def test_a_list_that_cannot_be_kept_is_refused(notes: list[dict[str, object]]) -> None:
    with pytest.raises(ValueError):
        nt.normalise_notes(_notes(*notes))


def test_a_kind_outside_the_two_is_invalid() -> None:
    with pytest.raises(ValueError):
        _notes(_note(kind="secret"))
