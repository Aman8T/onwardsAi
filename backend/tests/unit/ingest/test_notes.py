"""Pasted notes: normalisation, the character cap, and that ingesting them never fails."""

from __future__ import annotations

import time

import pytest

from tayyari.core import limits
from tayyari.ingest.notes import MAX_NOTES_CHARS, _normalise, ingest_notes


def test_the_notes_source_has_fixed_identity_fields() -> None:
    doc = ingest_notes("I led the Python migration.")
    assert (doc.id, doc.kind, doc.label) == ("notes", "notes", "Notes")
    assert doc.src == "Pasted context"
    assert doc.kind_label == "Extra context"
    assert doc.ok is True
    assert doc.error is None
    assert doc.url is None
    assert doc.pdf_bytes is None
    assert doc.sub == "Extra context"


def test_meta_counts_the_normalised_text_and_quick_signals_are_the_top_terms() -> None:
    doc = ingest_notes("Led the migration to React and TypeScript.\nAlso Python.  \n")
    assert doc.text == "Led the migration to React and TypeScript.\nAlso Python."
    assert doc.meta == {"chars": len(doc.text), "words": 9}
    assert doc.quick_signals[:3] == ["React", "TypeScript", "Python"]


def test_the_cap_is_the_core_limit() -> None:
    assert MAX_NOTES_CHARS == limits.NOTES_MAX_CHARS == 20_000


def test_long_notes_are_cut_to_exactly_the_cap() -> None:
    doc = ingest_notes("word " * 10_000)
    assert len(doc.text) == MAX_NOTES_CHARS
    assert doc.meta["chars"] == MAX_NOTES_CHARS
    assert doc.meta["words"] == len(doc.text.split())


def test_the_cap_counts_normalised_characters_not_pasted_ones() -> None:
    pasted = "line   \r\n\r\n\r\n\r\n" * 2_000  # 32k characters pasted, 12k after blank runs collapse
    assert len(pasted) > MAX_NOTES_CHARS
    doc = ingest_notes(pasted)
    assert len(doc.text) == 2_000 * len("line\n\n") - 2  # all of it kept: nothing was cut
    assert len(doc.text) < MAX_NOTES_CHARS
    assert doc.text.count("\n\n\n") == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a\r\nb\rc\nd", "a\nb\nc\nd"),
        ("trailing spaces   \nand tabs\t\t\nend", "trailing spaces\nand tabs\nend"),
        ("a\n\n\n\n\nb", "a\n\nb"),
        ("a\n   \n \t \n\nb", "a\n\nb"),
        ("\n\n  \n a leading blank run", "a leading blank run"),
        ("a trailing blank run\n\n\n  \n", "a trailing blank run"),
        ("  indented line kept\n    and this one", "indented line kept\n    and this one"),
        ("   ", ""),
        ("", ""),
        ("one line", "one line"),
        ("café • naïve \U0001f680", "café • naïve \U0001f680"),
    ],
)
def test_normalise(raw: str, expected: str) -> None:
    assert _normalise(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "\n\n\n"])
def test_empty_notes_are_an_empty_but_valid_source(raw: str | None) -> None:
    doc = ingest_notes(raw)  # type: ignore[arg-type]
    assert doc.ok is True
    assert doc.text == ""
    assert doc.meta == {"chars": 0, "words": 0}
    assert doc.quick_signals == []


@pytest.mark.parametrize(
    "raw",
    [
        "\n" * 5_000_000,
        " \n" * 2_000_000,
        "\r" * 3_000_000,
        " " * 5_000_000,
        "\t" * 5_000_000,
        "x" * 5_000_000,
        "line\n\n" * 1_000_000,
    ],
    ids=["newlines", "space-newlines", "carriage-returns", "spaces", "tabs", "one-long-line", "blank-lines"],
)
def test_hostile_notes_are_normalised_in_linear_time(raw: str) -> None:
    started = time.perf_counter()
    doc = ingest_notes(raw)
    assert time.perf_counter() - started < 8
    assert len(doc.text) <= MAX_NOTES_CHARS
