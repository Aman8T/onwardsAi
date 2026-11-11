"""A wording finalised in one resume, offered to the others: pure code over stored variants, no model call."""

from __future__ import annotations

from typing import Any

from tayyari.contract.match import ResumeVariant
from tayyari.contract.resume import MAX_WORDINGS_PER_LINE, FinaliseRequest, ResumeOp
from tayyari.match.models import FactBook
from tayyari.resume.wordings import WordingSource, blocks_of, offered
from tests.unit.resume.support import line, lines_of, rig

ROLE = "senior-growth"
RELEVANCE: dict[str, Any] = {"scores": [{"factId": "exp-1.3", "score": 96, "requirementIds": ["r1"]}]}
LINE = "b-exp-1.3"
REWORDED = "Ran 38 A/B tests across landing pages and checkout nudges; five winners shipped, adding 6.4% to site conversion."


def edit(text: str, line_id: str = LINE) -> ResumeOp:
    return ResumeOp(op="edit", bulletId=line_id, text=text)


async def finalised_path(r: Any, text: str = REWORDED) -> Any:
    """The resume for the next role with ``LINE`` in the person's other words, finalised (it passes every check)."""
    made = await r.path(ROLE)
    stored = await r.stored(made.resume.id)
    await r.service.edit(stored, [edit(text)])
    done = await r.service.finalise(await r.stored(made.resume.id), r.holder, FinaliseRequest())
    assert done.report.done, done.report.findings
    return done.detail


async def offers(r: Any, resume_id: str) -> list[Any]:
    return (await r.service.wordings(await r.stored(resume_id))).wordings


# ───────────────────────── through the service ─────────────────────────


async def test_a_finalised_wording_in_one_resume_is_offered_to_the_uploaded_resume() -> None:
    r = rig(relevance=RELEVANCE)
    upload_id = (await r.listing()).resumes[0].id
    path = await finalised_path(r)

    [wording] = await offers(r, upload_id)

    assert (wording.lineId, wording.text, wording.fromResumeId, wording.fromTitle) == (
        LINE,
        REWORDED,
        path.resume.id,
        "Senior Growth Marketing Manager",
    )
    # the path resume is not offered its own current text, nor the block's own wording (reset is the way back to it)
    assert await offers(r, path.resume.id) == []
    assert line(path.variant, LINE).text == REWORDED


async def test_only_a_finalised_wording_is_offered_not_a_draft() -> None:
    r = rig(relevance=RELEVANCE)
    upload_id = (await r.listing()).resumes[0].id
    made = await r.path(ROLE)
    await r.service.edit(await r.stored(made.resume.id), [edit(REWORDED)])  # typed, not finalised

    assert await offers(r, upload_id) == []


async def test_a_wording_edited_after_finalising_is_still_the_finalised_one_that_is_offered() -> None:
    r = rig(relevance=RELEVANCE)
    upload_id = (await r.listing()).resumes[0].id
    path = await finalised_path(r)
    await r.service.edit(await r.stored(path.resume.id), [edit("A newer draft wording of the same block.")])

    assert [w.text for w in await offers(r, upload_id)] == [REWORDED]


async def test_taking_a_wording_is_the_ordinary_edit_and_the_line_then_checks_like_any_typed_line() -> None:
    r = rig(relevance=RELEVANCE)
    upload_id = (await r.listing()).resumes[0].id
    await finalised_path(r)
    [wording] = await offers(r, upload_id)

    taken = await r.service.edit(await r.stored(upload_id), [edit(wording.text, wording.lineId)])

    assert line(taken.variant, LINE).text == REWORDED and line(taken.variant, LINE).status == "needs_user"
    assert taken.resume.state == "draft"
    assert await offers(r, upload_id) == []  # it is the line's own text now


async def test_the_wordings_call_no_model() -> None:
    r = rig(relevance=RELEVANCE)
    upload_id = (await r.listing()).resumes[0].id
    await finalised_path(r)
    before = len(r.claude.structured_calls)
    await offers(r, upload_id)
    assert len(r.claude.structured_calls) == before


# ───────────────────────── the rules, on stored variants ─────────────────────────


async def sources_and_draft() -> tuple[ResumeVariant, FactBook, Any]:
    r = rig()
    stored = await r.stored((await r.listing()).resumes[0].id)
    return ResumeVariant.model_validate(stored.draft), FactBook.model_validate(stored.facts), stored


def reworded(
    variant: ResumeVariant, text: str, *, line_id: str = LINE, fact_ids: list[str] | None = None
) -> ResumeVariant:
    copy = variant.model_copy(deep=True)
    target = line(copy, line_id)
    target.text = text
    if fact_ids is not None:
        target.factIds = fact_ids
    return copy


def source(n: int, final: ResumeVariant, book: FactBook) -> WordingSource:
    return WordingSource(f"r{n}", f"Resume {n}", final, book)


async def test_at_most_three_per_line_without_repeats_newest_first_and_never_the_lines_own_text() -> None:
    draft, book, _ = await sources_and_draft()
    own = line(draft, LINE).text
    sources = [
        source(1, reworded(draft, "Wording one."), book),
        source(2, reworded(draft, "  wording   ONE. "), book),  # the same, up to case and spacing
        source(3, reworded(draft, own.upper()), book),  # the line's own text, up to case
        source(4, reworded(draft, "Wording two."), book),
        source(5, reworded(draft, "Wording three."), book),
        source(6, reworded(draft, "Wording four."), book),
    ]

    got = offered(draft, book, sources)

    assert [(w.lineId, w.text, w.fromResumeId) for w in got] == [
        (LINE, "Wording one.", "r1"),
        (LINE, "Wording two.", "r4"),
        (LINE, "Wording three.", "r5"),
    ]
    assert len(got) == MAX_WORDINGS_PER_LINE


async def test_the_blocks_own_wording_is_not_a_wording_to_offer() -> None:
    draft, book, _ = await sources_and_draft()
    edited = reworded(draft, "Something else entirely.")
    assert offered(edited, book, [source(1, draft, book)]) == []  # the other resume says what the fact says


async def test_a_line_the_person_vouched_for_still_counts_as_a_wording_of_its_block() -> None:
    draft, book, _ = await sources_and_draft()
    mine = reworded(draft, "In my own words.", fact_ids=["exp-1.3", "user-1a2b"])
    got = offered(draft, book, [source(1, mine, book)])
    assert [(w.lineId, w.text) for w in got] == [(LINE, "In my own words.")]
    assert blocks_of(line(mine, LINE)) == frozenset({"exp-1.3"})


async def test_a_wording_of_another_block_is_not_offered() -> None:
    draft, book, _ = await sources_and_draft()
    other = reworded(draft, "Words for a different block.", line_id="b-exp-1.4")
    assert [w.lineId for w in offered(draft, book, [source(1, other, book)])] == ["b-exp-1.4"]
    assert not any(w.lineId == LINE for w in offered(draft, book, [source(1, other, book)]))


async def test_a_line_that_is_not_verified_in_the_other_resume_is_never_offered() -> None:
    draft, book, _ = await sources_and_draft()
    pending = reworded(draft, "Not checked yet.")
    line(pending, LINE).status = "needs_user"
    assert offered(draft, book, [source(1, pending, book)]) == []


async def test_every_line_of_the_draft_is_looked_at_on_its_own() -> None:
    draft, book, _ = await sources_and_draft()
    other = reworded(reworded(draft, "Words for one."), "Words for four.", line_id="b-exp-1.4")
    got = offered(draft, book, [source(1, other, book)])
    assert {(w.lineId, w.text) for w in got} == {(LINE, "Words for one."), ("b-exp-1.4", "Words for four.")}
    assert [b.id for b in lines_of(draft)].index(LINE) < [b.id for b in lines_of(draft)].index("b-exp-1.4")
    assert [w.lineId for w in got] == [LINE, "b-exp-1.4"]  # in the order of the draft's lines
