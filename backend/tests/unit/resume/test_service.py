"""`ResumeService` over the fakes: the uploaded resume made once and finalised, a path resume per role, drafts and their
versions, Finalise and what it stores, the default, and files that only a finalised version has."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from tayyari.contract.match import ResumeVariant
from tayyari.contract.resume import FinaliseRequest, ResumeOp
from tayyari.llm.errors import ClaudeOverloaded
from tayyari.match.lint import failing, read_pdf
from tayyari.match.verify import exact_wording
from tayyari.resume.errors import NotFinalised, OpRefused, ResumeChanged, ResumeNotFound, RoleNotFound
from tayyari.resume.service import MINE_QUESTION, Holder
from tests.unit.resume.support import FIGURE_LINE, INVENTED_FIGURE, PID, line, meera, rig, riya

ROLE = "senior-growth"
RELEVANCE: dict[str, Any] = {"scores": [{"factId": "exp-1.3", "score": 96, "requirementIds": ["r1"]}]}


def edit(line_id: str, text: str) -> ResumeOp:
    return ResumeOp(op="edit", bulletId=line_id, text=text)


def finalise_with(**choices: str) -> FinaliseRequest:
    return FinaliseRequest.model_validate(
        {
            "answers": [
                {"bulletId": k.replace("_", "-", 2).replace("_", "."), "choice": v}
                for k, v in choices.items()
            ]
        }
    )


# ───────────────────────── the list and the uploaded resume ─────────────────────────


async def test_the_first_listing_makes_the_uploaded_resume_finalised_and_default() -> None:
    r = rig()

    listing = await r.listing()

    [summary] = listing.resumes
    assert (summary.kind, summary.title, summary.state, summary.isDefault, summary.changes) == (
        "upload",
        "Resume",
        "final",
        True,
        0,
    )
    assert summary.roleId is None and summary.finalisedAt is not None
    assert listing.profileId == PID
    assert [(p.roleId, p.title, p.alignment, p.resumeId) for p in listing.paths] == [
        (ROLE, "Senior Growth Marketing Manager", 68, None)
    ]
    stored = await r.stored(summary.id)
    assert (stored.kind, stored.role_key, stored.user_id, stored.profile_id, stored.version) == (
        "upload",
        "",
        "u1",
        PID,
        1,
    )
    assert stored.final is not None and stored.finalised_at is not None
    final = ResumeVariant.model_validate(stored.final)
    assert final.lint is not None and failing(final.lint) == []  # the files were made and read back
    # 3 jobs and a degree, their 10 bullets
    assert listing.blocks == 14


async def test_the_uploaded_resume_is_made_once() -> None:
    r = rig()
    first = await r.listing()
    extractions = r.calls("match.facts")
    second = await r.listing()
    assert [s.id for s in second.resumes] == [s.id for s in first.resumes]
    assert r.calls("match.facts") == extractions and len(r.repos.resumes.rows) == 1
    assert r.calls("match.relevance") == 0  # the uploaded resume never asks for relevance


async def test_the_detail_of_the_uploaded_resume_cites_its_facts_and_leaves_nothing_out() -> None:
    r = rig()
    [summary] = (await r.listing()).resumes

    detail = r.service.detail(await r.stored(summary.id))

    assert detail.resume == summary and detail.canDownload and detail.marks == [] and detail.leftOut == []
    assert detail.variant.lint is not None  # final: the checklist of the finalised version shows
    cited = {fid for entry in detail.variant.experience for b in entry.bullets for fid in b.factIds}
    assert cited <= {f.id for f in detail.facts} and "exp-1" in {f.id for f in detail.facts}


async def test_a_row_this_build_cannot_read_is_not_served() -> None:
    r = rig()
    [summary] = (await r.listing()).resumes
    broken = replace(await r.stored(summary.id), draft={"header": "not a resume"})
    with pytest.raises(ResumeNotFound):
        r.service.detail(broken)


# ───────────────────────── a resume for a next role ─────────────────────────


async def test_a_path_resume_is_made_with_one_relevance_call_and_then_returned_as_it_is() -> None:
    r = rig(relevance=RELEVANCE)

    made = await r.path(ROLE)
    again = await r.path(ROLE)

    assert r.calls("match.relevance") == 1
    assert again.resume.id == made.resume.id and len(r.repos.resumes.rows) == 2  # the upload and this one
    assert (made.resume.kind, made.resume.title, made.resume.roleId) == (
        "path",
        "Senior Growth Marketing Manager",
        ROLE,
    )
    assert (made.resume.state, made.resume.isDefault, made.canDownload) == ("draft", False, False)
    assert made.variant.experience[0].bullets[0].id == FIGURE_LINE  # the model's most relevant line leads
    listing = await r.listing()
    assert [s.kind for s in listing.resumes] == ["upload", "path"]
    assert [p.resumeId for p in listing.paths] == [made.resume.id]
    assert [s.isDefault for s in listing.resumes] == [True, False]  # the uploaded resume stays the default


async def test_a_path_resume_is_still_made_when_the_model_cannot_be_asked() -> None:
    r = rig(relevance=ClaudeOverloaded())
    made = await r.path(ROLE)
    assert r.calls("match.relevance") == 1
    assert made.variant.experience and all(
        b.status == "verified" and b.verdict == "exact" for e in made.variant.experience for b in e.bullets
    )


async def test_a_role_the_graph_does_not_have_is_refused_before_anything_is_made() -> None:
    r = rig()
    with pytest.raises(RoleNotFound):
        await r.path("chief-of-everything")
    assert r.repos.resumes.rows == {} and r.claude.structured_calls == []


async def test_a_path_resume_shares_the_block_ids_of_the_uploaded_resume() -> None:
    r = rig(riya(), relevance=ClaudeOverloaded())
    made = await r.path("design-engineer")
    upload = r.service.detail(await r.stored((await r.listing()).resumes[0].id))
    upload_lines = {b.id for e in upload.variant.experience for b in e.bullets}
    path_lines = {b.id for e in made.variant.experience for b in e.bullets}
    assert path_lines <= upload_lines
    assert {m.kind for m in made.marks} <= {"moved", "added"}
    assert made.resume.changes == len(made.marks) + len(upload_lines - path_lines) + sum(
        1
        for e in upload.variant.projects
        for b in e.bullets
        if b.id not in {x.id for p in made.variant.projects for x in p.bullets}
    )
    assert {f.id for f in made.leftOut} >= {b.removeprefix("b-") for b in upload_lines - path_lines}


# ───────────────────────── drafts ─────────────────────────


async def test_an_edit_makes_the_resume_a_draft_and_bumps_its_version() -> None:
    r = rig()
    [summary] = (await r.listing()).resumes
    before = await r.stored(summary.id)

    detail = await r.service.edit(before, [edit(FIGURE_LINE, INVENTED_FIGURE)])

    after = await r.stored(summary.id)
    assert after.version == before.version + 1 and after.updated_at > before.updated_at
    assert (detail.resume.state, detail.resume.changes) == ("draft", 1)
    assert [(m.bulletId, m.kind) for m in detail.marks] == [(FIGURE_LINE, "edited")]
    assert detail.canDownload  # the version finalised before is still there
    assert detail.variant.lint is None and line(detail.variant, FIGURE_LINE).status == "needs_user"
    assert after.final == before.final  # an edit never touches the finalised version


async def test_editing_back_to_what_was_finalised_is_final_again() -> None:
    r = rig()
    [summary] = (await r.listing()).resumes
    await r.service.edit(await r.stored(summary.id), [edit(FIGURE_LINE, INVENTED_FIGURE)])
    detail = await r.service.edit(await r.stored(summary.id), [ResumeOp(op="reset", bulletId=FIGURE_LINE)])
    assert (detail.resume.state, detail.resume.changes, detail.marks) == ("final", 0, [])
    assert detail.variant.lint is not None


async def test_a_refused_operation_writes_nothing() -> None:
    r = rig()
    [summary] = (await r.listing()).resumes
    before = await r.stored(summary.id)
    with pytest.raises(OpRefused):
        await r.service.edit(
            before, [edit(FIGURE_LINE, INVENTED_FIGURE), ResumeOp(op="leave_out", bulletId="b-nothing")]
        )
    assert await r.stored(summary.id) == before


async def test_a_write_from_a_stale_read_is_refused() -> None:
    r = rig()
    [summary] = (await r.listing()).resumes
    stale = await r.stored(summary.id)
    await r.service.edit(stale, [edit(FIGURE_LINE, INVENTED_FIGURE)])
    with pytest.raises(ResumeChanged):
        await r.service.edit(stale, [ResumeOp(op="leave_out", bulletId="b-exp-3.1")])
    assert (await r.stored(summary.id)).version == stale.version + 1  # only the first write landed


# ───────────────────────── finalise ─────────────────────────


async def test_finalise_reports_the_figure_no_fact_has_and_the_resume_stays_a_draft() -> None:
    r = rig()
    [summary] = (await r.listing()).resumes
    await r.service.edit(await r.stored(summary.id), [edit(FIGURE_LINE, INVENTED_FIGURE)])
    before = await r.stored(summary.id)

    answer = await r.service.finalise(before, r.holder, FinaliseRequest())

    assert not answer.report.done and answer.report.lint is None and answer.report.fixed == []
    assert [(f.bulletId, f.kind, f.what) for f in answer.report.findings] == [(FIGURE_LINE, "number", "40%")]
    assert answer.detail.resume.state == "draft"
    after = await r.stored(summary.id)
    assert after == before  # nothing settled, nothing written
    assert r.repos.user_facts.rows() == []


async def test_mine_finalises_the_resume_and_keeps_the_line_as_a_fact_of_the_profile() -> None:
    r = rig()
    [summary] = (await r.listing()).resumes
    await r.service.edit(await r.stored(summary.id), [edit(FIGURE_LINE, INVENTED_FIGURE)])
    first_final = (await r.stored(summary.id)).finalised_at

    answer = await r.service.finalise(await r.stored(summary.id), r.holder, finalise_with(b_exp_1_3="mine"))

    assert answer.report.done and answer.report.findings == []
    assert answer.report.lint is not None and failing(answer.report.lint) == []
    assert answer.detail.resume.state == "final" and answer.detail.canDownload
    typed = line(answer.detail.variant, FIGURE_LINE)
    assert typed.text == INVENTED_FIGURE and typed.status == "verified"
    said = next(f for f in answer.detail.facts if f.id == typed.factIds[-1])
    assert said.text == INVENTED_FIGURE and said.refs[0].source == "user"
    assert said.refs[0].docId == f"resume:{summary.id}/{FIGURE_LINE}"
    # stored the way the match stores what a person says, so later resumes and matches can cite it
    [row] = r.repos.user_facts.rows()
    assert (row.user_id, row.profile_id, row.question, row.answer, row.match_id) == (
        "u1",
        PID,
        MINE_QUESTION,
        INVENTED_FIGURE,
        None,
    )
    assert [f.id for f in await r.repos.user_facts.list_for_profile(PID)] == [said.id]
    stored = await r.stored(summary.id)
    assert stored.final == stored.draft and stored.finalised_at is not None
    assert first_final is not None and stored.finalised_at > first_final
    # the export notes say what the files changed by themselves: Meera's rupee sign is written in plain form
    assert len(answer.report.fixed) == 1 and "plain form" in answer.report.fixed[0]


async def test_undo_finalises_with_the_facts_own_wording_and_stores_no_fact() -> None:
    r = rig()
    [summary] = (await r.listing()).resumes
    await r.service.edit(await r.stored(summary.id), [edit(FIGURE_LINE, INVENTED_FIGURE)])
    answer = await r.service.finalise(await r.stored(summary.id), r.holder, finalise_with(b_exp_1_3="undo"))
    assert answer.report.done and answer.detail.resume.changes == 0
    assert line(answer.detail.variant, FIGURE_LINE).status == "verified"
    assert r.repos.user_facts.rows() == []


async def test_on_a_sample_run_what_the_person_says_stays_on_that_resume() -> None:
    r = rig(user_id=None, shares_facts=False)
    [summary] = (await r.listing()).resumes
    await r.service.edit(await r.stored(summary.id), [edit(FIGURE_LINE, INVENTED_FIGURE)])
    answer = await r.service.finalise(await r.stored(summary.id), r.holder, finalise_with(b_exp_1_3="mine"))
    assert answer.report.done and line(answer.detail.variant, FIGURE_LINE).text == INVENTED_FIGURE
    assert r.repos.user_facts.rows() == []  # everyone who holds a sample's id shares it


async def test_a_later_path_resume_can_cite_what_the_person_said() -> None:
    r = rig(relevance=ClaudeOverloaded())
    [summary] = (await r.listing()).resumes
    await r.service.edit(await r.stored(summary.id), [edit(FIGURE_LINE, INVENTED_FIGURE)])
    done = await r.service.finalise(await r.stored(summary.id), r.holder, finalise_with(b_exp_1_3="mine"))
    said_id = line(done.detail.variant, FIGURE_LINE).factIds[-1]

    made = await r.path(ROLE)

    book = (await r.stored(made.resume.id)).facts
    assert said_id in {f["id"] for f in book["facts"]}
    # the path resume is written from the blocks' own words: the typed wording is offered, not copied in
    block = next(f for f in book["facts"] if f["id"] == "exp-1.3")
    assert line(made.variant, FIGURE_LINE).text == block["text"]


async def test_partial_answers_are_kept_and_the_rest_keeps_waiting() -> None:
    r = rig()
    [summary] = (await r.listing()).resumes
    await r.service.edit(
        await r.stored(summary.id),
        [
            edit(FIGURE_LINE, INVENTED_FIGURE),
            edit("b-exp-2.2", "Built the weekly acquisition report for the founders in Tableau"),
        ],
    )
    before = await r.stored(summary.id)

    answer = await r.service.finalise(before, r.holder, finalise_with(b_exp_2_2="undo"))

    assert not answer.report.done
    assert [(f.bulletId, f.kind) for f in answer.report.findings] == [(FIGURE_LINE, "number")]
    after = await r.stored(summary.id)
    assert after.version == before.version + 1 and after.final == before.final
    assert line(answer.detail.variant, "b-exp-2.2").status == "verified"
    assert line(answer.detail.variant, FIGURE_LINE).status == "needs_user"


# ───────────────────────── the default and the files ─────────────────────────


async def test_only_a_finalised_resume_can_be_made_the_default_or_downloaded() -> None:
    r = rig(relevance=RELEVANCE)
    made = await r.path(ROLE)
    stored = await r.stored(made.resume.id)

    with pytest.raises(NotFinalised) as no_default:
        await r.service.make_default(stored)
    with pytest.raises(NotFinalised) as no_files:
        await r.service.files(stored)

    assert (no_default.value.status, no_default.value.code) == (409, "not_finalised")
    assert no_files.value.to_body()["code"] == "not_finalised"
    assert [s.isDefault for s in (await r.listing()).resumes] == [True, False]


async def test_the_default_moves_from_one_resume_to_another() -> None:
    r = rig(relevance=RELEVANCE)
    made = await r.path(ROLE)
    await r.service.finalise(await r.stored(made.resume.id), r.holder, FinaliseRequest())

    detail = await r.service.make_default(await r.stored(made.resume.id))

    assert detail.resume.isDefault and detail.resume.state == "final"
    listing = await r.listing()
    assert [(s.kind, s.isDefault) for s in listing.resumes] == [("upload", False), ("path", True)]
    upload_id = listing.resumes[0].id
    await r.service.make_default(await r.stored(upload_id))
    assert [(s.kind, s.isDefault) for s in (await r.listing()).resumes] == [("upload", True), ("path", False)]


async def test_finalise_can_make_the_resume_the_default_in_the_same_call() -> None:
    r = rig(relevance=RELEVANCE)
    made = await r.path(ROLE)
    answer = await r.service.finalise(
        await r.stored(made.resume.id), r.holder, FinaliseRequest(makeDefault=True)
    )
    assert answer.report.done and answer.detail.resume.isDefault
    assert [(s.kind, s.isDefault) for s in (await r.listing()).resumes] == [("upload", False), ("path", True)]


async def test_finalise_does_not_move_the_default_while_findings_wait() -> None:
    r = rig(relevance=RELEVANCE)
    made = await r.path(ROLE)
    await r.service.edit(await r.stored(made.resume.id), [edit(FIGURE_LINE, INVENTED_FIGURE)])
    answer = await r.service.finalise(
        await r.stored(made.resume.id), r.holder, FinaliseRequest(makeDefault=True)
    )
    assert not answer.report.done and not answer.detail.resume.isDefault and not answer.detail.canDownload
    assert [s.isDefault for s in (await r.listing()).resumes] == [True, False]


async def test_the_files_are_those_of_the_finalised_version_never_of_the_draft() -> None:
    r = rig()
    [summary] = (await r.listing()).resumes
    # a typed line waits for Finalise: the draft has it, the files must not
    await r.service.edit(await r.stored(summary.id), [edit(FIGURE_LINE, INVENTED_FIGURE)])
    stored = await r.stored(summary.id)

    files, person = await r.service.files(stored)

    text = " ".join(read_pdf(files.pdf).text.split())
    own = exact_wording(meera().book().by_id()["exp-1.3"])
    assert person == "Meera Nair" and files.docx[:2] == b"PK"
    assert "lifting conversion by 40%" not in text
    assert " ".join(own.split())[:60] in text


# ───────────────────────── the blocks ─────────────────────────


async def test_the_blocks_are_every_job_degree_and_bullet_with_how_many_resumes_use_each() -> None:
    r = rig(relevance=RELEVANCE)
    made = await r.path(ROLE)
    await r.service.edit(await r.stored(made.resume.id), [ResumeOp(op="leave_out", bulletId="b-exp-3.2")])

    blocks = await r.service.blocks(r.holder, r.persona.profile, r.persona.context, None)  # type: ignore[arg-type]

    assert blocks.profileId == PID and blocks.resumes == 2
    assert [(e.factId, e.kind, e.title) for e in blocks.entries] == [
        ("exp-1", "experience", "Growth Marketing Manager"),
        ("exp-2", "experience", "Performance Marketing Specialist"),
        ("exp-3", "experience", "Marketing Associate"),
        ("edu-1", "education", "B.Com"),
    ]
    by_id = {b.fact.id: b for b in blocks.blocks}
    assert len(blocks.blocks) == 14 == (await r.listing()).blocks
    assert (by_id["exp-1"].entryFactId, by_id["exp-1"].usedIn) == (None, 2)
    assert (by_id["exp-1.1"].entryFactId, by_id["exp-1.1"].usedIn) == ("exp-1", 2)
    assert (by_id["exp-3.2"].entryFactId, by_id["exp-3.2"].usedIn) == ("exp-3", 1)  # left out of one
    assert by_id["edu-1"].usedIn == 2
    assert not any(b.fact.kind == "skill" for b in blocks.blocks)


async def test_a_line_the_person_vouched_for_is_a_block_in_their_own_words() -> None:
    r = rig()
    [summary] = (await r.listing()).resumes
    await r.service.edit(await r.stored(summary.id), [edit(FIGURE_LINE, INVENTED_FIGURE)])
    await r.service.finalise(await r.stored(summary.id), r.holder, finalise_with(b_exp_1_3="mine"))

    blocks = await r.service.blocks(r.holder, r.persona.profile, r.persona.context, None)  # type: ignore[arg-type]

    own = [b for b in blocks.blocks if b.fact.refs[0].source == "user"]
    assert [(b.fact.text, b.entryFactId, b.usedIn) for b in own] == [(INVENTED_FIGURE, "exp-1", 1)]
    assert len(blocks.blocks) == 15


# ───────────────────────── a profile that was claimed ─────────────────────────


async def test_resumes_made_without_an_account_go_to_the_account_that_claimed_the_profile() -> None:
    r = rig(user_id=None)
    [summary] = (await r.listing()).resumes
    assert (await r.stored(summary.id)).user_id is None

    owner = Holder(PID, "u7")
    listing = await r.service.listing(owner, r.persona.profile, r.persona.context, None)  # type: ignore[arg-type]

    assert [s.id for s in listing.resumes] == [summary.id]
    assert (await r.stored(summary.id)).user_id == "u7"
