"""The edits of a draft: every operation, each refusal, and all-or-none."""

from __future__ import annotations

import pytest

from tayyari.contract.resume import MAX_LINE_CHARS, ResumeOp
from tayyari.match.bullets import export_blockers
from tayyari.match.verify import exact_wording
from tayyari.resume.arrange import path_variant, upload_variant
from tayyari.resume.errors import OpRefused
from tayyari.resume.ops import apply_ops
from tests.unit.resume.support import (
    FIGURE_LINE,
    INVENTED_FIGURE,
    entry_of,
    line,
    line_ids,
    meera,
    riya,
    role_of,
)


def op(kind: str, **fields: str) -> ResumeOp:
    return ResumeOp.model_validate({"op": kind, **fields})


def refusal(variant, book, *ops: ResumeOp) -> OpRefused:  # type: ignore[no-untyped-def]
    with pytest.raises(OpRefused) as caught:
        apply_ops(variant, book, list(ops))
    return caught.value


# ───────────────────────── each operation ─────────────────────────


def test_move_swaps_a_line_with_its_neighbour_inside_its_job() -> None:
    book = meera().book()
    upload = upload_variant(book)

    down = apply_ops(upload, book, [op("move", bulletId="b-exp-1.1", dir="down")])
    back = apply_ops(down, book, [op("move", bulletId="b-exp-1.1", dir="up")])

    assert line_ids(down, "exp-1")[:3] == ["b-exp-1.2", "b-exp-1.1", "b-exp-1.3"]
    assert line_ids(down, "exp-2") == line_ids(upload, "exp-2")  # the other jobs are untouched
    assert back == upload
    assert line_ids(upload, "exp-1")[0] == "b-exp-1.1"  # the variant handed in is never changed


def test_leave_out_takes_the_line_off_and_put_back_appends_the_block_in_its_own_words() -> None:
    book = meera().book()
    upload = upload_variant(book)

    without = apply_ops(upload, book, [op("leave_out", bulletId="b-exp-1.2")])
    again = apply_ops(without, book, [op("put_back", factId="exp-1.2")])

    assert "b-exp-1.2" not in line_ids(without, "exp-1") and len(line_ids(without, "exp-1")) == 4
    assert line_ids(again, "exp-1")[-1] == "b-exp-1.2"  # appended to its own job
    returned = line(again, "b-exp-1.2")
    assert returned.text == exact_wording(book.by_id()["exp-1.2"])
    assert (returned.factIds, returned.verdict, returned.status) == (["exp-1.2"], "exact", "verified")


def test_a_job_emptied_of_its_lines_keeps_its_heading() -> None:
    book = meera().book()
    upload = upload_variant(book)
    emptied = apply_ops(
        upload, book, [op("leave_out", bulletId="b-exp-3.1"), op("leave_out", bulletId="b-exp-3.2")]
    )
    job = entry_of(emptied, "exp-3")
    assert job.bullets == [] and (job.title, job.org, job.dates) == (
        "Marketing Associate",
        "Kitchen Table Co",
        "Jul 2018 - May 2020",
    )


def test_put_back_brings_a_job_the_resume_had_left_out_back_with_its_heading() -> None:
    persona = riya()
    book = persona.book()
    variant = path_variant(book, role_of(persona, "design-engineer"), skills=persona.profile.skills)
    assert "exp-4" not in [e.entryFactId for e in variant.experience]  # the arrangement left this job out

    again = apply_ops(variant, book, [op("put_back", factId="exp-4.1")])

    known = book.entry("exp-4")
    assert known is not None
    job = entry_of(again, "exp-4")
    assert (job.title, job.org, job.dates) == (known.title, known.org, known.dates)
    assert [b.id for b in job.bullets] == ["b-exp-4.1"]
    recency = {e.factId: e.recency for e in book.entries}
    order = [recency[e.entryFactId] for e in again.experience]
    assert order == sorted(order, reverse=True)  # newest first, as before


def test_put_back_of_a_github_project_adds_it_to_the_projects() -> None:
    book = riya().book()
    upload = upload_variant(book)
    again = apply_ops(upload, book, [op("put_back", factId="proj-5")])
    assert [e.entryFactId for e in again.projects][-1] == "proj-5"
    assert line_ids(again, "proj-5") == ["b-proj-5"]


def test_edit_sets_the_text_and_marks_the_line_as_waiting_for_finalise() -> None:
    book = meera().book()
    upload = upload_variant(book)

    edited = apply_ops(upload, book, [op("edit", bulletId=FIGURE_LINE, text=f"  {INVENTED_FIGURE}   ")])

    typed = line(edited, FIGURE_LINE)
    assert typed.text == INVENTED_FIGURE  # trimmed
    assert (typed.status, typed.verdict, typed.factIds) == ("needs_user", "reframed", ["exp-1.3"])
    assert export_blockers(edited) == [f"{FIGURE_LINE}: waiting for your answer"]  # it can not be exported
    assert edited.lint is None


def test_typing_the_facts_own_words_back_is_a_reset() -> None:
    book = meera().book()
    upload = upload_variant(book)
    edited = apply_ops(upload, book, [op("edit", bulletId=FIGURE_LINE, text=INVENTED_FIGURE)])
    own = exact_wording(book.by_id()["exp-1.3"])

    retyped = apply_ops(edited, book, [op("edit", bulletId=FIGURE_LINE, text=own.upper().lower())])
    reset = apply_ops(edited, book, [op("reset", bulletId=FIGURE_LINE)])

    assert reset == upload
    assert line(retyped, FIGURE_LINE).status == "verified" and line(retyped, FIGURE_LINE).text == own


def test_reset_of_a_line_nobody_edited_changes_nothing() -> None:
    book = meera().book()
    upload = upload_variant(book)
    assert apply_ops(upload, book, [op("reset", bulletId="b-exp-2.1")]) == upload


def test_operations_apply_in_order() -> None:
    book = meera().book()
    upload = upload_variant(book)
    out = apply_ops(
        upload,
        book,
        [
            op("leave_out", bulletId="b-exp-1.5"),
            op("move", bulletId="b-exp-1.4", dir="up"),
            op("put_back", factId="exp-1.5"),
            op("move", bulletId="b-exp-1.5", dir="up"),
        ],
    )
    assert line_ids(out, "exp-1") == ["b-exp-1.1", "b-exp-1.2", "b-exp-1.4", "b-exp-1.5", "b-exp-1.3"]


# ───────────────────────── each refusal ─────────────────────────


@pytest.mark.parametrize(
    ("bad", "says"),
    [
        (op("move", bulletId="b-exp-1.1", dir="up"), "already the first"),
        (op("move", bulletId="b-exp-1.5", dir="down"), "already the last"),
        (op("move", bulletId="b-exp-1.2"), "needs a direction"),
        (op("move", bulletId="b-nothing", dir="up"), "not on this resume"),
        (op("move", dir="up"), "not on this resume"),
        (op("leave_out", bulletId="b-nothing"), "not on this resume"),
        (op("leave_out", bulletId="summary"), "not on this resume"),
        (op("leave_out", bulletId="exp-1"), "not on this resume"),
        (op("put_back", factId="nothing"), "not one of your blocks"),
        (op("put_back"), "not one of your blocks"),
        (op("put_back", factId="exp-1"), "not a line of a job or project"),
        (op("put_back", factId="edu-1"), "not a line of a job or project"),
        (op("put_back", factId="skill-seo"), "not a line of a job or project"),
        (op("put_back", factId="exp-1.1"), "already on this resume"),
        (op("edit", bulletId="b-exp-1.1"), "needs some text"),
        (op("edit", bulletId="b-exp-1.1", text="   "), "needs some text"),
        (op("edit", bulletId="b-exp-1.1", text="one line\nand another"), "one line of text"),
        (op("edit", bulletId="b-exp-1.1", text="x" * (MAX_LINE_CHARS + 1)), f"at most {MAX_LINE_CHARS}"),
        (op("edit", bulletId="b-exp-1.1", text="Grew repeat purchases by [x]%"), "cannot hold a [x]"),
        (op("edit", bulletId="b-nothing", text="Anything"), "not on this resume"),
        (op("edit", bulletId="summary", text="A summary nobody sourced"), "not on this resume"),
        (op("edit", bulletId="exp-1", text="Chief Marketing Officer"), "not on this resume"),
        (op("reset", bulletId="b-nothing"), "not on this resume"),
    ],
)
def test_an_operation_that_does_not_fit_is_refused(bad: ResumeOp, says: str) -> None:
    book = meera().book()
    upload = upload_variant(book)
    error = refusal(upload, book, bad)
    assert says in error.user_message and error.user_message.startswith("Change 1 does not fit: ")
    assert (error.status, error.code, error.index) == (422, "op_refused", 0)
    assert error.to_body() == {"error": error.user_message, "code": "op_refused"}


def test_a_line_of_exactly_the_longest_length_is_taken() -> None:
    book = meera().book()
    upload = upload_variant(book)
    edited = apply_ops(upload, book, [op("edit", bulletId="b-exp-3.1", text="x" * MAX_LINE_CHARS)])
    assert len(line(edited, "b-exp-3.1").text) == MAX_LINE_CHARS


def test_titles_employers_and_dates_cannot_be_reached_by_any_operation() -> None:
    book = meera().book()
    upload = upload_variant(book)
    for kind in ("edit", "reset", "leave_out", "move"):
        for target in ("exp-1", "edu-1", "b-exp-1", "b-edu-1"):
            error = refusal(upload, book, op(kind, bulletId=target, text="Head of Growth", dir="up"))
            assert "not on this resume" in error.user_message


def test_all_operations_apply_or_none_and_the_refusal_names_the_first_that_does_not_fit() -> None:
    book = meera().book()
    upload = upload_variant(book)
    before = upload.model_copy(deep=True)

    error = refusal(
        upload,
        book,
        op("leave_out", bulletId="b-exp-1.5"),
        op("move", bulletId="b-exp-1.1", dir="up"),  # the first that does not fit
        op("edit", bulletId="b-nothing", text="Also wrong"),
    )

    assert error.index == 1 and error.user_message.startswith("Change 2 does not fit: ")
    assert upload == before  # nothing was applied, not even the first operation
