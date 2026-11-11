"""`resume.reread.build_source`: the text a re-read of the graph is made from. Order, de-duplication, the cap and what it
left out, the own-words section, the fallback to the stored context's resume text, and nothing to read."""

from __future__ import annotations

from datetime import UTC, datetime

from tayyari.contract.match import Fact, FactRef
from tayyari.match.facts import user_fact
from tayyari.resume.files import ResumeText
from tayyari.resume.reread import (
    FALLBACK_HEADING,
    OWN_WORDS_HEADING,
    build_source,
    file_heading,
    statements,
)
from tayyari.resume.service import MINE_KEY


def text(name: str, day: int, body: str, fid: str | None = None) -> ResumeText:
    return ResumeText(
        file_id=fid or name,
        filename=name,
        text=body,
        created_at=datetime(2026, 11, day, tzinfo=UTC),
        origin="upload",
    )


NEWEST = text("c.pdf", 9, "Third resume. Led a team of nine.")
MIDDLE = text("b.docx", 5, "Second resume. Shipped the checkout.")
OLDEST = text("a.txt", 1, "First resume. Ran paid search.")


def test_files_come_oldest_first_each_under_a_heading_with_name_and_date() -> None:
    source = build_source([NEWEST, MIDDLE, OLDEST], [], None)  # all_resume_texts is newest first
    assert source is not None
    assert source.text == "\n\n".join(
        [
            f"{file_heading('a.txt', '2026-11-01')}\nFirst resume. Ran paid search.",
            f"{file_heading('b.docx', '2026-11-05')}\nSecond resume. Shipped the checkout.",
            f"{file_heading('c.pdf', '2026-11-09')}\nThird resume. Led a team of nine.",
        ]
    )
    assert (source.files, source.own_words, source.left_out, source.duplicates) == (3, 0, (), 0)
    assert source.note == "Reading 3 resume files"


def test_a_copy_of_a_file_is_read_once_and_the_newest_copy_stays() -> None:
    copy = text("c-copy.pdf", 2, "  Third resume.\nLed a team of nine.  ")  # same words, other spacing, older
    source = build_source([NEWEST, copy], [], None)
    assert source is not None
    assert source.files == 1 and source.duplicates == 1
    assert "c.pdf" in source.text and "c-copy.pdf" not in source.text
    assert "1 copy of a file skipped" in source.note


def test_the_own_words_are_their_own_section_one_line_each_and_marked_as_not_from_a_document() -> None:
    source = build_source(
        [NEWEST], ["Ran the budget alone", "  Ran   the budget alone ", "Hired two analysts"], None
    )
    assert source is not None
    head, _, tail = source.text.partition(f"\n\n{OWN_WORDS_HEADING}\n")
    assert head.startswith("Resume file: c.pdf")
    assert tail == "- Ran the budget alone\n- Hired two analysts"  # the repeat is one line
    assert OWN_WORDS_HEADING == "In your own words (stated by you, not from a document)"
    assert source.own_words == 2
    assert source.note == "Reading 1 resume file and 2 lines in your own words"


def test_statements_are_only_the_lines_typed_on_a_resume() -> None:
    mine = user_fact("Ran the budget alone", key=f"{MINE_KEY}r1/b-exp-1.1")
    from_a_match = user_fact("Led a launch", key="question-7")
    from_a_file = Fact(
        id="f1",
        text="Built a thing",
        kind="accomplishment",
        refs=[FactRef(source="resume", docId="resume", lines=None, quote="x")],
    )
    assert statements([mine, from_a_match, from_a_file]) == ["Ran the budget alone"]


def test_when_the_cap_cuts_the_newest_files_stay_whole_and_what_was_left_out_is_named() -> None:
    newest, middle, oldest = (
        text("c.pdf", 9, "c" * 400),
        text("b.docx", 5, "b" * 400),
        text("a.txt", 1, "a" * 400),
    )
    source = build_source([newest, middle, oldest], [], None, limit=1000)
    assert source is not None
    assert source.files == 2 and source.left_out == ("a.txt (2026-11-01)",)
    assert "c" * 400 in source.text and "b" * 400 in source.text and "a" * 400 not in source.text
    assert len(source.text) <= 1000
    assert "a.txt (2026-11-01)" in source.note and "1,000-character limit" in source.note
    assert source.text.index("b.docx") < source.text.index("c.pdf")  # still oldest first


def test_a_newest_file_longer_than_the_cap_is_cut_at_the_end_and_it_says_so() -> None:
    source = build_source([text("c.pdf", 9, "y" * 900)], [], None, limit=500)
    assert source is not None and len(source.text) <= 500
    assert source.left_out == ("the end of c.pdf",) and "the end of c.pdf" in source.note


def test_the_own_words_keep_their_room_when_the_files_are_too_long() -> None:
    source = build_source([text("c.pdf", 9, "z" * 900)], ["Ran the budget alone"], None, limit=600)
    assert source is not None
    assert source.text.endswith("- Ran the budget alone") and len(source.text) <= 600
    assert source.own_words == 1


def test_a_profile_with_no_stored_file_reads_the_resume_text_its_context_kept() -> None:
    source = build_source([], ["Hired two analysts"], "Old resume text.")
    assert source is not None
    assert source.from_stored_context and source.files == 0
    assert source.text.startswith(f"{FALLBACK_HEADING}\nOld resume text.")
    assert source.text.endswith("- Hired two analysts")
    assert source.note.startswith("Reading the resume text kept from your last analysis")


def test_files_without_text_fall_back_too_and_a_file_with_text_beats_the_fallback() -> None:
    empty = text("scan.pdf", 3, "   ")
    assert build_source([empty], [], "Kept text.") is not None
    kept = build_source([empty, MIDDLE], [], "Kept text.")
    assert kept is not None and not kept.from_stored_context and "Kept text." not in kept.text


def test_nothing_to_read_is_none() -> None:
    assert build_source([], [], None) is None
    assert build_source([text("scan.pdf", 3, "")], ["a line"], "  ") is None
