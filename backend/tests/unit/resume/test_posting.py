"""Saving a match's tailored resume into the list: what `as_posting_resume` makes of the match's variant and what the
service stores (kind ``posting``, the match id as its role key, finalised when the match exported it)."""

from __future__ import annotations

from typing import Any

import pytest

from tayyari.contract.match import Decision, ResumeVariant
from tayyari.match.models import FactBook
from tayyari.resume.errors import ResumeNotFound
from tayyari.resume.marks import left_out
from tayyari.resume.posting import as_posting_resume
from tests.unit.resume.support import PID, line, lines_of, rig

LINE = "b-exp-1.3"
MATCH = "c" * 32


async def made() -> tuple[Any, ResumeVariant, FactBook]:
    """The service, and the uploaded resume as a stand-in for a match's exported variant (a checklist attached, nothing
    waiting for the person), with the fact book it cites."""
    r = rig()
    stored = await r.stored((await r.listing()).resumes[0].id)
    variant = ResumeVariant.model_validate(stored.draft)
    assert variant.lint is not None
    return r, variant, FactBook.model_validate(stored.facts)


# ───────────────────────── the transformation ─────────────────────────


async def test_a_variant_the_match_exported_is_finalised_as_it_was() -> None:
    _, variant, book = await made()

    saved, finalised = as_posting_resume(variant, book)

    assert finalised is True
    assert saved == variant  # lines, ids, summary and checklist exactly as the match left them
    assert saved is not variant


async def test_a_match_line_takes_the_id_every_resume_gives_its_block() -> None:
    _, variant, book = await made()
    renamed = variant.model_copy(deep=True)
    line(renamed, LINE).id = "b3"

    saved, _ = as_posting_resume(renamed, book)

    assert LINE in [b.id for b in lines_of(saved)] and "b3" not in [b.id for b in lines_of(saved)]


async def test_a_draft_the_match_did_not_export_loses_what_cannot_be_kept() -> None:
    _, variant, book = await made()
    open_ = variant.model_copy(deep=True, update={"lint": None})
    slot = line(open_, LINE)
    slot.text, slot.placeholders, slot.status = (
        "Ran 38 A/B tests, lifting conversion by [x]%.",
        ["[x]%"],
        "needs_user",
    )
    waiting = line(open_, "b-exp-1.4")
    waiting.status = "needs_user"  # a stretch the person has not answered: stays, Finalise checks it
    waiting.verdict = "stretch"
    dropped = line(open_, "b-exp-1.5")
    dropped.status = "dropped"
    open_.decisions = [Decision(bulletId=LINE, kind="number", prompt="?", options=["fill", "soften", "drop"])]
    open_.summary = open_.summary.model_copy(update={"text": "A summary.", "status": "needs_user"})

    saved, finalised = as_posting_resume(open_, book)

    assert finalised is False and saved.lint is None
    assert line(saved, LINE).text == line(variant, LINE).text  # the [x] went back to the block's own wording
    assert line(saved, LINE).status == "verified" and line(saved, LINE).placeholders == []
    assert line(saved, "b-exp-1.4").status == "needs_user"
    assert "b-exp-1.5" not in [b.id for b in lines_of(saved)]
    assert saved.decisions == []
    assert (saved.summary.text, saved.summary.status) == ("", "dropped")
    assert not any("[x]" in b.text for b in lines_of(saved))


async def test_a_line_with_an_open_slot_and_no_block_to_go_back_to_leaves() -> None:
    _, variant, book = await made()
    open_ = variant.model_copy(deep=True, update={"lint": None})
    slot = line(open_, LINE)
    slot.text, slot.factIds, slot.status = "Ran [x] tests.", ["user-zz"], "needs_user"

    saved, _ = as_posting_resume(open_, book)

    assert LINE not in [b.id for b in lines_of(saved)]


async def test_the_input_is_never_changed() -> None:
    _, variant, book = await made()
    open_ = variant.model_copy(deep=True, update={"lint": None})
    line(open_, LINE).placeholders = ["[x]"]
    snapshot = open_.model_copy(deep=True)
    as_posting_resume(open_, book)
    assert open_ == snapshot


# ───────────────────────── through the service ─────────────────────────


async def test_saving_stores_a_finalised_posting_resume_with_the_match_id_as_its_key() -> None:
    r, variant, book = await made()

    detail = await r.service.save_match(
        r.holder, match_id=MATCH, title="Growth Lead at Acme", variant=variant, book=book
    )

    summary = detail.resume
    assert (
        summary.kind,
        summary.title,
        summary.matchId,
        summary.roleId,
        summary.state,
        summary.isDefault,
    ) == (
        "posting",
        "Growth Lead at Acme",
        MATCH,
        None,
        "final",
        False,
    )
    assert detail.canDownload is True and summary.finalisedAt is not None
    stored = await r.stored(summary.id)
    assert (stored.kind, stored.role_key, stored.profile_id, stored.user_id, stored.version) == (
        "posting",
        MATCH,
        PID,
        "u1",
        1,
    )
    assert stored.final == stored.draft and stored.facts == book.model_dump(mode="json", exclude_none=True)


async def test_saving_the_same_match_twice_is_the_same_resume() -> None:
    r, variant, book = await made()
    first = await r.service.save_match(r.holder, match_id=MATCH, title="One", variant=variant, book=book)
    again = await r.service.save_match(r.holder, match_id=MATCH, title="Two", variant=variant, book=book)
    assert again.resume.id == first.resume.id and again.resume.title == "One"
    rows = [x for x in await r.repos.resumes.list_for_profile(PID) if x.kind == "posting"]
    assert len(rows) == 1
    other = await r.service.save_match(r.holder, match_id="d" * 32, title="Three", variant=variant, book=book)
    assert other.resume.id != first.resume.id


async def test_the_saved_resume_is_in_the_list_in_its_own_group_and_may_become_the_default() -> None:
    r, variant, book = await made()
    saved = await r.service.save_match(
        r.holder, match_id=MATCH, title="Growth Lead", variant=variant, book=book
    )

    listing = await r.listing()

    assert [(s.kind, s.matchId) for s in listing.resumes] == [("upload", None), ("posting", MATCH)]
    assert listing.resumes[1].id == saved.resume.id and listing.paths[0].resumeId is None
    assert (await r.service.make_default(await r.stored(saved.resume.id))).resume.isDefault is True


async def test_a_match_resume_that_was_not_exported_is_a_draft_with_no_files_until_finalised() -> None:
    r, variant, book = await made()
    open_ = variant.model_copy(deep=True, update={"lint": None})
    line(open_, LINE).placeholders = ["[x]%"]
    line(open_, LINE).text = "Ran 38 A/B tests, lifting conversion by [x]%."

    detail = await r.service.save_match(
        r.holder, match_id=MATCH, title="Growth Lead", variant=open_, book=book
    )

    assert (
        detail.resume.state == "draft" and detail.canDownload is False and detail.resume.finalisedAt is None
    )
    assert not any("[x]" in b.text for b in lines_of(detail.variant))
    assert left_out(detail.variant, book) == []  # nothing of the book was lost by saving
    stored = await r.stored(detail.resume.id)
    assert stored.final is None


@pytest.mark.parametrize("match_id", ["", "x" * 65])
async def test_a_match_id_that_cannot_be_a_key_is_refused(match_id: str) -> None:
    r, variant, book = await made()
    with pytest.raises(ResumeNotFound):
        await r.service.save_match(r.holder, match_id=match_id, title="t", variant=variant, book=book)


async def test_a_blank_title_gets_a_plain_one() -> None:
    r, variant, book = await made()
    detail = await r.service.save_match(r.holder, match_id=MATCH, title="  \n ", variant=variant, book=book)
    assert detail.resume.title == "Posting resume"
