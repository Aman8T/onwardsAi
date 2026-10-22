"""Re-drill and Fix steps through the service (I2): the Headout interview of the canvas played and ended, then what it leaves
behind. Its claims: s1 an ask Defended, s2 an ask Shaky (the LCP line, after a push), s4 a claim Couldn't defend ("I don't
know"), s5 a story Couldn't defend (after the floor); s3 is a gap and is never a claim.

    marks       written once at the end, one per claim slot; Progress reads them, so they outlive the sessions' 30 days;
                deleting a session takes its marks.
    plan        the claims that wobbled, Couldn't defend first, each opening where it wobbled; picked claims, unknown keys.
    the room    a Shaky claim opens at its push; a Couldn't defend one in new words (a phrased question that repeats the
                last one is refused); a push start that cannot be defended still gets the floor.
    debrief     before → now and "moved up" in code; "Or say less" only for twice Couldn't defend, only from the answer.
    Fix steps   placed in a plan of this profile or kept in Practice, ticked, moved, taken out; a ticked step comes first.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from tayyari.contract.interview import FixAdd, PlanRequest, StartRequest
from tayyari.interview.errors import BadPlan, FixNotFound, FixPlanNotFound
from tayyari.interview.phrase import REDRILL_SURFACES
from tayyari.interview.redrill import NOTHING_TO_REDRILL, REDRILL_TITLE
from tayyari.interview.text import fold
from tayyari.storage.ports import StoredClaimMark, StoredPlan
from tests.unit.interview.flows import end, play_headout, say, start
from tests.unit.interview.support import CTX, OTHER, SAMPLE, START, Rig, judged, lines, make_rig

PLAN_ID = "c" * 32


async def headout_done(rig: Rig) -> str:
    sid, _ = await play_headout(rig)
    await end(rig, sid)
    return sid


def marks_of(rig: Rig) -> dict[str, StoredClaimMark]:
    return {m.slot_id: m for m in rig.marks.rows.values()}


async def redrill_plan(rig: Rig, **over: Any):  # type: ignore[no-untyped-def]
    req = PlanRequest.model_validate({"profileId": SAMPLE.id, "kind": "redrill", **over})
    return await rig.service.plan(SAMPLE, req)


def redrill_phrasing(slots: list[Any]) -> dict[str, Any]:
    """Phrasing for a re-drill plan: every Couldn't defend slot is (wrongly) given its last question again."""
    out = []
    for slot in slots:
        assert slot.last is not None
        out.append(
            lines(
                slot.last.question,
                "Which part of that was yours, and how did you know it worked?",
                "What is one thing you did there, in a sentence?",
                slot=slot.id,
            )
        )
    return {"slots": out}


async def start_redrill(rig: Rig, **over: Any):  # type: ignore[no-untyped-def]
    plan = await redrill_plan(rig, **over)
    rig.claude.script_structured("interview.phrase", redrill_phrasing(plan.slots))
    req = StartRequest.model_validate({"profileId": SAMPLE.id, "kind": "redrill", **over})
    return plan, await rig.service.start(SAMPLE, req, CTX)


def plan_row(plan_id: str = PLAN_ID, profile_id: str = SAMPLE.id) -> StoredPlan:
    return StoredPlan(
        id=plan_id,
        profile_id=profile_id,
        role_id="senior-frontend-engineer",
        requirement=None,
        content={},
        state={},
        version=1,
        created_at=START,
        updated_at=START,
    )


# ───────────────────────── marks ─────────────────────────


async def test_ending_writes_one_mark_per_claim_slot_and_never_twice() -> None:
    rig = make_rig()
    sid = await headout_done(rig)
    marks = marks_of(rig)
    assert set(marks) == {"s1", "s2", "s4", "s5"}  # s3 is a gap: never a claim
    assert {k: (m.slot_kind, m.verdict, m.rung) for k, m in marks.items()} == {
        "s1": ("ask", "defended", "surface"),
        "s2": ("ask", "shaky", "push"),
        "s4": ("claim", "couldnt", "surface"),
        "s5": ("story", "couldnt", "floor"),
    }
    assert marks["s1"].fix_text is None
    assert marks["s2"].fix_text == (
        "Find the pull request or report behind the LCP change and note the one change that moved it most."
    )
    assert marks["s2"].fix_minutes == 20
    assert marks["s4"].fix_text is not None and marks["s5"].fix_text is not None  # the plain step stands in
    assert all(m.session_id == sid and m.marked_on == START.date() for m in marks.values())
    await rig.service.end(SAMPLE, sid, CTX)  # a finished interview ends again: its debrief, no new marks
    assert len(rig.marks.rows) == 4


async def test_progress_keeps_its_claims_after_the_sessions_are_purged() -> None:
    rig = make_rig()
    await headout_done(rig)
    before = await rig.service.history(SAMPLE)
    assert {c.best for c in before.claims} == {"defended", "shaky", "couldnt"}
    await rig.repo.purge(START + timedelta(days=31))
    after = await rig.service.history(SAMPLE)
    assert after.sessions == []
    assert [(c.text, c.best, c.on) for c in after.claims] == [(c.text, c.best, c.on) for c in before.claims]


async def test_a_history_read_writes_the_marks_a_session_is_missing() -> None:
    rig = make_rig()
    await headout_done(rig)
    rig.marks.rows.clear()  # as before marks existed, or after a mark write failed
    history = await rig.service.history(SAMPLE)
    assert len(rig.marks.rows) == 4 and len(history.claims) == 4


async def test_deleting_a_session_takes_its_marks_with_it() -> None:
    rig = make_rig()
    sid = await headout_done(rig)
    assert await rig.service.delete(SAMPLE, sid)
    assert rig.marks.rows == {}
    assert (await rig.service.history(SAMPLE)).claims == []


# ───────────────────────── the plan ─────────────────────────


async def test_the_plan_brings_back_what_wobbled_couldnt_first_each_where_it_wobbled() -> None:
    rig = make_rig()
    await headout_done(rig)
    marks = marks_of(rig)
    plan = await redrill_plan(rig, length="full")
    assert plan.kind == "redrill" and plan.title == REDRILL_TITLE
    by_text = {s.sources[0].text: s for s in plan.slots}
    assert set(by_text) == {marks["s2"].claim_text, marks["s4"].claim_text, marks["s5"].claim_text}
    assert [s.last.verdict for s in plan.slots if s.last] == ["couldnt", "couldnt", "shaky"]
    assert [s.start for s in plan.slots] == ["surface", "surface", "push"]
    shaky = by_text[marks["s2"].claim_text]
    assert shaky.kind == "claim"  # an ask comes back as a claim: the posting is not part of a re-drill
    assert shaky.last is not None and shaky.last.question == marks["s2"].question
    assert by_text[marks["s5"].claim_text].kind == "story"
    assert plan.minutes == 9


async def test_a_short_plan_takes_the_top_three_and_picked_claims_keep_codes_order() -> None:
    rig = make_rig()
    await headout_done(rig)
    marks = marks_of(rig)
    full = await redrill_plan(rig, length="full")
    short = await redrill_plan(rig)
    assert [s.id for s in short.slots] == ["s1", "s2", "s3"] and len(full.slots) == 3
    picked = await redrill_plan(rig, claims=[marks["s2"].claim_key, marks["s4"].claim_key])
    assert [s.sources[0].text for s in picked.slots] == [marks["s4"].claim_text, marks["s2"].claim_text]


async def test_a_claim_that_is_not_one_to_redrill_is_refused() -> None:
    rig = make_rig()
    await headout_done(rig)
    with pytest.raises(BadPlan):
        await redrill_plan(rig, claims=[fold("Not a line of the resume")])
    with pytest.raises(BadPlan):  # Defended: nothing to bring back
        await redrill_plan(rig, claims=[marks_of(rig)["s1"].claim_key])


async def test_with_nothing_that_wobbled_there_is_nothing_to_redrill() -> None:
    rig = make_rig()
    with pytest.raises(BadPlan) as refused:
        await redrill_plan(rig)
    assert refused.value.user_message == NOTHING_TO_REDRILL


async def test_another_profiles_marks_are_never_brought_back() -> None:
    rig = make_rig()
    await headout_done(rig)
    with pytest.raises(BadPlan):
        await rig.service.plan(OTHER, PlanRequest(profileId=OTHER.id, kind="redrill"))


# ───────────────────────── the room ─────────────────────────


async def test_a_shaky_claim_opens_at_its_push_and_a_couldnt_one_in_new_words() -> None:
    rig = make_rig()
    await headout_done(rig)
    plan, session = await start_redrill(rig, length="full")
    assert session.kind == "redrill"
    first = plan.slots[0]
    assert first.last is not None and first.start == "surface"
    # the phrasing gave the last question again: refused, and the new-words template is asked instead
    assert session.current is not None and session.current.rung == "surface"
    assert fold(session.current.text) != fold(first.last.question)
    assert any(session.current.text.startswith(t.split("“")[0]) for t in REDRILL_SURFACES)


async def test_the_push_start_is_the_first_question_and_cannot_defend_gets_the_floor() -> None:
    rig = make_rig()
    sid = await headout_done(rig)
    lcp = marks_of(rig)["s2"].claim_key
    _, session = await start_redrill(rig, claims=[lcp])
    assert session.current is not None
    assert (session.current.kind, session.current.rung) == ("question", "push")
    assert session.current.text == "Which part of that was yours, and how did you know it worked?"
    vague = "We did a few things on that page together as a team."
    after = await say(rig, session.id, vague, verdict=judged(vague, "couldnt"))
    assert after.current is not None and after.current.rung == "floor"
    assert sid != session.id


async def test_a_story_that_opens_at_its_push_asks_for_its_missing_part() -> None:
    rig = make_rig()
    await headout_done(rig)
    story = marks_of(rig)["s5"]
    rig.marks.rows[story.id] = StoredClaimMark(
        **{**{f: getattr(story, f) for f in story.__slots__}, "verdict": "shaky"}  # type: ignore[attr-defined]
    )
    _, session = await start_redrill(rig, claims=[story.claim_key])
    assert session.current is not None and session.current.rung == "push"
    assert session.current.text == "What was going on at the time, and what was at stake?"


# ───────────────────────── the debrief ─────────────────────────


async def test_the_debrief_shows_before_and_now_and_counts_what_moved() -> None:
    rig = make_rig()
    await headout_done(rig)
    marks = marks_of(rig)
    _, session = await start_redrill(rig, claims=[marks["s2"].claim_key, marks["s4"].claim_key])
    # Couldn't defend comes first (the Kite UI claim, from the top, then its floor), then the LCP claim at its push
    vague = "We worked on the components together as a team."
    await say(rig, session.id, vague, verdict=judged(vague, "couldnt"))
    other = "Someone else on the team owned most of that work."
    await say(rig, session.id, other, verdict=judged(other, "couldnt"))
    good = "I loaded the payment script only when the pay step came into view, and checked the traces in the pull request."
    await say(rig, session.id, good, verdict=judged(good, "defended"))
    debrief = await end(rig, session.id, script={"slots": []})
    assert debrief.kind == "redrill" and debrief.redrill is not None
    assert (debrief.redrill.moved, debrief.redrill.of) == (1, 2)
    previous = {r.verdict: r.previous for r in debrief.results}
    assert previous["defended"] is not None and previous["defended"].verdict == "shaky"
    assert previous["couldnt"] is not None and previous["couldnt"].on == START.date().isoformat()
    # the new marks: the LCP claim is now Defended at its best, and the ready list has one fewer
    view = await rig.service.redrill_view(SAMPLE)
    assert marks["s2"].claim_key not in {c.key for c in view.claims}


async def twice_couldnt(rig: Rig, softer: str) -> Any:
    await headout_done(rig)
    kite = marks_of(rig)["s4"]
    _, session = await start_redrill(rig, claims=[kite.claim_key])
    answer = "I ran the visual checks on the Kite UI pull requests, but someone else set up the baselines."
    await say(rig, session.id, answer, verdict=judged(answer, "couldnt"))
    await say(rig, session.id, "That's all I know.", verdict=judged("That's all I know.", "couldnt"))
    script = {
        "slots": [
            {
                "id": "s1",
                "say": {"sources": [], "text": ""},
                "avoid": {"quote": "", "advice": ""},
                "fix": {"text": "", "minutes": 0},
                "softer": softer,
            }
        ]
    }
    return await end(rig, session.id, script=script)


async def test_twice_couldnt_defend_may_say_less_in_the_answers_own_words() -> None:
    debrief = await twice_couldnt(make_rig(), "Ran the visual checks on the Kite UI pull requests")
    softer = debrief.results[0].softer
    assert softer is not None and softer.text == "“Ran the visual checks on the Kite UI pull requests”"
    assert softer.sources == ["Your answer"] and softer.placeholders == []


@pytest.mark.parametrize(
    "bad",
    [
        "Ran visual checks on all 42 Kite UI components",  # a number the answer never said (it is on the resume line)
        "Set up the Chromatic baselines for Kite UI",  # a name the answer never said
        "[What you ran] on the Kite UI pull requests",  # brackets
        "The visual checks on the Kite UI pull requests ran",  # does not start with a verb
    ],
)
async def test_a_softer_line_that_says_more_than_the_answer_is_dropped(bad: str) -> None:
    debrief = await twice_couldnt(make_rig(), bad)
    assert debrief.results[0].softer is None


async def test_softer_is_never_offered_to_a_claim_that_is_not_twice_couldnt() -> None:
    rig = make_rig()
    await headout_done(rig)
    lcp = marks_of(rig)["s2"]
    _, session = await start_redrill(rig, claims=[lcp.claim_key])
    vague = "We did a few things on that page together as a team."
    await say(rig, session.id, vague, verdict=judged(vague, "couldnt"))
    other = "Someone else on the team measured all of that work."
    await say(rig, session.id, other, verdict=judged(other, "couldnt"))
    script = {
        "slots": [{"id": "s1", "say": {}, "avoid": {}, "fix": {}, "softer": "Loaded the payment script late"}]
    }
    debrief = await end(rig, session.id, script=script)
    assert debrief.results[0].softer is None  # it was Shaky before, not Couldn't defend


# ───────────────────────── Fix steps ─────────────────────────


async def test_a_fix_step_goes_into_a_plan_is_ticked_and_its_claim_comes_first() -> None:
    rig = make_rig()
    sid = await headout_done(rig)
    await rig.plans.create(plan_row())
    task = await rig.service.add_fix(
        SAMPLE, FixAdd(profileId=SAMPLE.id, sessionId=sid, slotId="s2", planId=PLAN_ID)
    )
    assert (task.where, task.planId, task.doneAt, task.minutes) == ("plan", PLAN_ID, None, 20)
    assert task.claimText == marks_of(rig)["s2"].claim_text
    assert [t.id for t in await rig.service.plan_fixes(SAMPLE.id, PLAN_ID)] == [task.id]
    view = await rig.service.redrill_view(SAMPLE)
    assert view.claims[0].best == "couldnt"  # nothing ticked yet: Couldn't defend first
    rig.clock.advance(3600)
    ticked = await rig.service.tick_fix(SAMPLE, task.id, done=True)
    assert ticked.doneAt is not None
    view = await rig.service.redrill_view(SAMPLE)
    assert view.claims[0].key == marks_of(rig)["s2"].claim_key  # a ticked Fix step comes first
    assert view.claims[0].fix is not None and view.claims[0].fix.doneAt == ticked.doneAt


async def test_a_fix_step_kept_in_practice_and_one_whose_plan_is_gone_both_read_as_practice() -> None:
    rig = make_rig()
    sid = await headout_done(rig)
    await rig.plans.create(plan_row())
    kept = await rig.service.add_fix(SAMPLE, FixAdd(profileId=SAMPLE.id, sessionId=sid, slotId="s4"))
    placed = await rig.service.add_fix(
        SAMPLE, FixAdd(profileId=SAMPLE.id, sessionId=sid, slotId="s5", planId=PLAN_ID)
    )
    assert kept.where == "practice" and kept.planId is None
    del rig.plans.rows[PLAN_ID]  # the plan is gone
    view = await rig.service.redrill_view(SAMPLE)
    assert {t.id for t in view.kept} == {kept.id, placed.id}
    assert all(t.where == "practice" and t.planId is None for t in view.kept)


async def test_placing_a_step_again_moves_it_and_keeps_its_tick_and_it_can_be_taken_out() -> None:
    rig = make_rig()
    sid = await headout_done(rig)
    await rig.plans.create(plan_row())
    first = await rig.service.add_fix(SAMPLE, FixAdd(profileId=SAMPLE.id, sessionId=sid, slotId="s2"))
    await rig.service.tick_fix(SAMPLE, first.id, done=True)
    moved = await rig.service.add_fix(
        SAMPLE, FixAdd(profileId=SAMPLE.id, sessionId=sid, slotId="s2", planId=PLAN_ID)
    )
    assert moved.id == first.id and moved.where == "plan" and moved.doneAt is not None
    assert await rig.service.remove_fix(SAMPLE, moved.id)
    assert not await rig.service.remove_fix(SAMPLE, moved.id)
    with pytest.raises(FixNotFound):
        await rig.service.tick_fix(SAMPLE, moved.id, done=True)


async def test_a_fix_step_is_refused_for_a_defended_claim_another_profile_or_their_plan() -> None:
    rig = make_rig()
    sid = await headout_done(rig)
    await rig.plans.create(plan_row("d" * 32, profile_id=OTHER.id))
    with pytest.raises(FixNotFound):  # s1 was Defended: it has no Fix step
        await rig.service.add_fix(SAMPLE, FixAdd(profileId=SAMPLE.id, sessionId=sid, slotId="s1"))
    with pytest.raises(FixNotFound):  # another profile's session
        await rig.service.add_fix(OTHER, FixAdd(profileId=OTHER.id, sessionId=sid, slotId="s2"))
    with pytest.raises(FixPlanNotFound):  # another profile's plan
        await rig.service.add_fix(
            SAMPLE, FixAdd(profileId=SAMPLE.id, sessionId=sid, slotId="s2", planId="d" * 32)
        )
    task = await rig.service.add_fix(SAMPLE, FixAdd(profileId=SAMPLE.id, sessionId=sid, slotId="s2"))
    with pytest.raises(FixNotFound):
        await rig.service.tick_fix(OTHER, task.id, done=True)
    assert not await rig.service.remove_fix(OTHER, task.id)


async def test_a_fix_step_can_be_placed_after_the_session_is_purged() -> None:
    rig = make_rig()
    sid = await headout_done(rig)
    await rig.repo.purge(START + timedelta(days=31))
    task = await rig.service.add_fix(SAMPLE, FixAdd(profileId=SAMPLE.id, sessionId=sid, slotId="s4"))
    assert task.text == marks_of(rig)["s4"].fix_text


async def test_a_redrill_counts_as_an_interview_of_the_day() -> None:
    rig = make_rig()
    await headout_done(rig)
    await start_redrill(rig)
    view = await rig.service.redrill_view(SAMPLE, per_day=3)
    assert view.leftToday == 1


async def test_a_role_interview_is_still_planned_as_before() -> None:
    rig = make_rig()
    session = await start(rig)
    assert session.kind == "role" and all(s.start is None and s.last is None for s in session.slots)
