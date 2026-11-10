"""How a draft differs from the uploaded resume: moved, edited, added, and the lines left out."""

from __future__ import annotations

from tayyari.contract.resume import ResumeOp
from tayyari.resume.arrange import path_variant, upload_variant
from tayyari.resume.marks import changes, cited_facts, left_out, left_out_lines, marks, used_blocks
from tayyari.resume.ops import apply_ops
from tests.unit.resume.support import (
    FIGURE_LINE,
    INVENTED_FIGURE,
    lines_of,
    meera,
    riya,
    role_of,
)


def op(kind: str, **fields: str) -> ResumeOp:
    return ResumeOp.model_validate({"op": kind, **fields})


def kinds(variant, upload) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    return [(m.bulletId, m.kind) for m in marks(variant, upload)]


def test_the_uploaded_resume_differs_from_itself_in_nothing() -> None:
    book = meera().book()
    upload = upload_variant(book)
    assert marks(upload, upload) == [] and changes(upload, upload) == 0
    assert left_out(upload, book) == []  # every bullet block she has is on it


def test_a_moved_line_is_marked_and_only_the_line_that_moved() -> None:
    book = meera().book()
    upload = upload_variant(book)
    # the last line of five goes to the top: the other four keep their order
    moved = apply_ops(upload, book, [op("move", bulletId="b-exp-1.5", dir="up")] * 4)
    assert kinds(moved, upload) == [("b-exp-1.5", "moved")]
    assert changes(moved, upload) == 1


def test_an_edited_line_is_marked_edited_even_when_it_also_moved() -> None:
    book = meera().book()
    upload = upload_variant(book)
    draft = apply_ops(
        upload,
        book,
        [
            op("edit", bulletId=FIGURE_LINE, text=INVENTED_FIGURE),
            op("move", bulletId=FIGURE_LINE, dir="up"),
            op("move", bulletId=FIGURE_LINE, dir="up"),
        ],
    )
    assert kinds(draft, upload) == [(FIGURE_LINE, "edited")]  # one mark per line


def test_a_block_the_uploaded_resume_does_not_have_is_marked_added() -> None:
    book = riya().book()
    upload = upload_variant(book)
    draft = apply_ops(upload, book, [op("put_back", factId="proj-5")])
    assert kinds(draft, upload) == [("b-proj-5", "added")]
    assert changes(draft, upload) == 1


def test_a_line_left_out_and_put_back_counts_as_moved_not_added() -> None:
    book = meera().book()
    upload = upload_variant(book)
    draft = apply_ops(upload, book, [op("leave_out", bulletId="b-exp-1.1"), op("put_back", factId="exp-1.1")])
    assert kinds(draft, upload) == [("b-exp-1.1", "moved")]


def test_changes_counts_the_marks_plus_the_lines_left_out() -> None:
    book = meera().book()
    upload = upload_variant(book)
    draft = apply_ops(
        upload,
        book,
        [
            op("leave_out", bulletId="b-exp-2.2"),
            op("leave_out", bulletId="b-exp-3.1"),
            op("edit", bulletId=FIGURE_LINE, text=INVENTED_FIGURE),
        ],
    )
    assert left_out_lines(draft, upload) == 2
    assert changes(draft, upload) == 3
    assert [f.id for f in left_out(draft, book)] == ["exp-2.2", "exp-3.1"]  # offered to put back


def test_a_path_resume_counts_the_lines_it_selected_away() -> None:
    persona = riya()
    book = persona.book()
    upload = upload_variant(book)
    variant = path_variant(book, role_of(persona, "design-engineer"), skills=persona.profile.skills)

    gone = [b.id for b in lines_of(upload) if b.id not in {x.id for x in lines_of(variant)}]
    assert gone and left_out_lines(variant, upload) == len(gone)
    assert changes(variant, upload) == len(marks(variant, upload)) + len(gone)
    assert all(m.kind == "moved" for m in marks(variant, upload))  # same blocks, same words, another order
    # what it leaves out is exactly the blocks no line is written from
    missing = {f.id for f in left_out(variant, book)}
    assert missing.isdisjoint(used_blocks(variant, book))
    assert {b.removeprefix("b-") for b in gone} <= missing and "proj-5" in missing


def test_the_cited_facts_are_those_the_lines_entries_degrees_and_skills_name() -> None:
    book = meera().book()
    upload = upload_variant(book)
    ids = {f.id for f in cited_facts(upload, book)}
    assert {"exp-1", "exp-1.1", "edu-1", "skill-seo"} <= ids
    assert "skill-looker-studio" not in ids  # in the book, not on this resume
    assert [f.id for f in cited_facts(upload, book)] == [
        f.id for f in book.facts if f.id in ids
    ]  # the book's order
