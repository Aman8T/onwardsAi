"""`InterviewService.speech_upcoming`: the lines the voice should have ready for a session that is still going. The line being said
(its lead, then its text), then what may be said next: the current question's follow-up and easier question (only at the
surface question), the questions still to come in plan order, a story's fixed questions, and the sentences the interviewer says
the same way to everyone. Code over the stored session: no model, nothing of another profile's, nothing for a finished interview."""

from __future__ import annotations

from typing import Any

from tayyari.contract.interview import AssistantRequest, WorkSubmit
from tayyari.interview.flow import DISPUTE_TEXT, DONE_TEXT, DONT_KNOW_LEAD, DRIFT_LEAD, UNCLEAR_LEAD
from tayyari.interview.phrase import STORY_PARTS
from tayyari.interview.speech import FIXED_LINES, SpeechUpcoming
from tayyari.interview.tasks import TEMPLATES
from tests.unit.interview.flows import A1, A5, ROLLBACK, end, play_headout, say, start
from tests.unit.interview.support import CTX, OTHER, SAMPLE, judged, make_rig, sample_phrasing

PHRASED: dict[str, dict[str, Any]] = {slot["id"]: slot for slot in sample_phrasing()["slots"]}
Q1, Q2, Q3, Q4, Q5 = (PHRASED[f"s{i}"]["question"] for i in range(1, 6))
P1, F1 = PHRASED["s1"]["push"], PHRASED["s1"]["floor"]
P2, F2 = PHRASED["s2"]["push"], PHRASED["s2"]["floor"]
F5 = PHRASED["s5"]["floor"]


async def upcoming(rig: Any, session_id: str, **options: Any) -> SpeechUpcoming:
    found = await rig.service.speech_upcoming(SAMPLE, session_id, **options)
    assert found is not None
    return found


def test_the_fixed_sentences_are_the_ones_the_interviewer_says_the_same_way_to_everyone() -> None:
    assert FIXED_LINES == (
        "That's fine. I'll note it and we'll move on. You'll get a step to close it.",
        "Let's come back to the question.",
        "I didn't catch that.",
        "That's the last question. Thank you. Your debrief is ready.",
    )
    assert FIXED_LINES == (DONT_KNOW_LEAD, DRIFT_LEAD, UNCLEAR_LEAD, DONE_TEXT)


# ───────────────────────── at the question ─────────────────────────


async def test_at_the_first_question_the_line_comes_first_then_its_follow_ups_then_the_questions_to_come() -> (
    None
):
    rig = make_rig()
    session = await start(rig)
    found = await upcoming(rig, session.id)
    assert found.now == (Q1,)
    assert found.soon == (P1, F1, Q2, Q3, Q4, Q5, *FIXED_LINES)
    assert found.next_turn == (
        P1,
        F1,
        Q2,
    )  # what the judge's verdict may lead to: a follow-up, an easier one, or the next slot


async def test_without_follow_ups_only_the_questions_to_come_and_the_fixed_sentences_remain() -> None:
    rig = make_rig()
    session = await start(rig)
    found = await upcoming(rig, session.id, followups=False)
    assert found.now == (Q1,)
    assert found.soon == (Q2, Q3, Q4, Q5, *FIXED_LINES)
    assert found.next_turn == (Q2,)


async def test_no_model_is_called_and_no_hidden_note_is_among_the_lines() -> None:
    rig = make_rig()
    session = await start(rig)
    before = len(rig.claude.calls)
    found = await upcoming(rig, session.id)
    assert len(rig.claude.calls) == before
    spoken = " ".join((*found.now, *found.soon))
    for hidden in ("Names what changed first", "Says what the person built themselves", "Web performance"):
        assert hidden not in spoken  # the covers and the topics are the judge's, not the interviewer's
    assert A1 not in spoken


async def test_after_a_defended_answer_the_next_question_is_the_line_and_the_old_slot_is_forgotten() -> None:
    rig = make_rig()
    session = await start(rig)
    await say(rig, session.id, A1, verdict=judged(A1, "defended", quote=ROLLBACK), after=20)
    found = await upcoming(rig, session.id)
    assert found.now == (Q2,)
    assert found.soon == (P2, F2, Q3, Q4, Q5, *FIXED_LINES)
    assert found.next_turn == (P2, F2, Q3)
    for old in (Q1, P1, F1):
        assert old not in found.soon


async def test_at_a_follow_up_there_is_no_follow_up_to_the_follow_up() -> None:
    rig = make_rig()
    session = await start(rig)
    await say(rig, session.id, A1, verdict=judged(A1, "shaky", quote=ROLLBACK), after=20)
    found = await upcoming(rig, session.id)
    assert found.now == (P1,)
    assert found.soon == (
        Q2,
        Q3,
        Q4,
        Q5,
        *FIXED_LINES,
    )  # the push is the end of the slot: its floor is never asked
    assert found.next_turn == (Q2,)


async def test_at_the_easier_question_too() -> None:
    rig = make_rig()
    session = await start(rig)
    await say(rig, session.id, A1, verdict=judged(A1, "couldnt", quote=ROLLBACK), after=20)
    found = await upcoming(rig, session.id)
    assert found.now == (F1,)
    assert found.soon == (Q2, Q3, Q4, Q5, *FIXED_LINES)


# ───────────────────────── leads ─────────────────────────


async def test_a_lead_comes_before_the_question_it_leads_into() -> None:
    rig = make_rig()
    session = await start(rig)
    await say(rig, session.id, "Um.", after=5)  # too short to judge
    found = await upcoming(rig, session.id)
    assert found.now == (UNCLEAR_LEAD, Q1)
    assert found.soon[:2] == (P1, F1)  # still at the surface question


async def test_after_i_dont_know_the_reply_leads_into_the_next_question() -> None:
    rig = make_rig()
    session = await start(rig)
    await say(rig, session.id, action="dont_know", after=5)
    found = await upcoming(rig, session.id)
    assert found.now == (DONT_KNOW_LEAD, Q2)


async def test_a_dispute_has_a_lead_that_could_not_be_known_before_and_a_fixed_question() -> None:
    rig = make_rig()
    session = await start(rig)
    before = await upcoming(rig, session.id)
    await say(rig, session.id, "That's not what my resume says", after=9)
    found = await upcoming(rig, session.id)
    assert len(found.now) == 2 and found.now[0].startswith("Here's the line I'm asking about:")
    assert found.now[1] == DISPUTE_TEXT
    assert found.now[0] not in before.soon  # which is why a route asks again once the reply is out
    assert found.soon[:2] == (P1, F1)  # the dispute line is at the surface rung still


# ───────────────────────── stories ─────────────────────────


async def test_a_story_may_be_followed_by_any_of_its_four_fixed_questions_or_the_easier_one() -> None:
    rig = make_rig()
    session = await start(rig, skip=["s1", "s2", "s3", "s4"])
    found = await upcoming(rig, session.id)
    assert found.now == (Q5,)
    assert found.soon == (*STORY_PARTS.values(), F5, *FIXED_LINES)
    assert found.next_turn == (*STORY_PARTS.values(), F5)
    plain = await upcoming(rig, session.id, followups=False)
    assert plain.soon == FIXED_LINES and plain.next_turn == ()


# ───────────────────────── the end ─────────────────────────


async def test_the_closing_line_has_nothing_after_it() -> None:
    rig = make_rig()
    sid, last = await play_headout(rig)
    assert last.current is not None and last.current.kind == "done"
    found = await upcoming(rig, sid)
    assert found.now == (DONE_TEXT,)
    assert found.soon == () and found.next_turn == ()


async def test_a_finished_interview_has_nothing_to_get_ready() -> None:
    rig = make_rig()
    sid, _ = await play_headout(rig)
    await end(rig, sid)
    assert await rig.service.speech_upcoming(SAMPLE, sid) is None


async def test_at_the_follow_up_of_the_last_question_only_the_fixed_sentences_remain() -> None:
    rig = make_rig()
    session = await start(rig, skip=["s1", "s2", "s3", "s4"])
    await say(rig, session.id, A5, verdict=judged(A5, "defended", quote="It usually goes fine"), after=20)
    found = await upcoming(
        rig, session.id
    )  # a story's follow-up (an easier question or a missing part): then it is over
    assert found.now in {(F5,), *((part,) for part in STORY_PARTS.values())}
    assert found.soon == FIXED_LINES  # nothing is still to come


# ───────────────────────── whose session ─────────────────────────


async def test_another_profiles_session_and_an_unknown_one_give_nothing() -> None:
    rig = make_rig()
    session = await start(rig)
    assert await rig.service.speech_upcoming(OTHER, session.id) is None
    assert await rig.service.speech_upcoming(SAMPLE, "f" * 32) is None
    assert await rig.service.speech_upcoming(SAMPLE, "") is None


async def test_the_lines_to_get_ready_are_not_yet_lines_the_voice_may_read() -> None:
    rig = make_rig()
    session = await start(rig)
    found = await upcoming(rig, session.id)
    ctx = await rig.service.speech_context(SAMPLE, session.id)
    assert ctx is not None
    assert all(ctx.allows(text) for text in found.now)
    for later in (P1, F1, Q2, Q5, DONT_KNOW_LEAD, DONE_TEXT):
        assert later in found.soon and not ctx.allows(
            later
        )  # /api/speech/speak still refuses what was not said
    await say(rig, session.id, A1, verdict=judged(A1, "shaky", quote=ROLLBACK), after=20)
    ctx = await rig.service.speech_context(SAMPLE, session.id)
    assert ctx is not None and ctx.allows(P1) and not ctx.allows(F1)  # said now, and so readable now


# ───────────────────────── asking again ─────────────────────────


async def test_asking_one_question_again_gets_that_questions_own_lines_ready() -> None:
    rig = make_rig()
    sid, _ = await play_headout(rig)
    await end(rig, sid)
    again = await rig.service.again(SAMPLE, sid, "s2")
    found = await upcoming(rig, again.id)
    assert found.now == (Q2,)
    assert found.soon == (P2, F2, *FIXED_LINES)
    assert found.next_turn == (P2, F2)


# ───────────────────────── a work sample ─────────────────────────


async def test_a_work_sample_gets_its_questions_ready_but_not_the_one_about_an_assistant_nobody_used() -> (
    None
):
    rig = make_rig()
    session = await start(rig, "work")
    template = TEMPLATES[0]
    first, second, about_assistant = (f.question for f in template.follow_ups)
    found = await upcoming(rig, session.id)
    assert found.now == (template.task,)
    assert found.soon == (first, second, *FIXED_LINES)  # no push or easier question: each is asked once
    assert found.next_turn == (first,)
    assert about_assistant not in found.soon


async def test_the_question_about_the_assistant_is_included_once_it_was_used() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    rig.claude.script_structured("interview.assistant", {"reply": "Try deriving it during render."})
    await rig.service.assistant(
        SAMPLE, session.id, AssistantRequest(profileId=SAMPLE.id, message="why does it flicker"), CTX
    )
    template = TEMPLATES[0]
    first, second, about_assistant = (f.question for f in template.follow_ups)
    found = await upcoming(rig, session.id)
    assert found.soon == (first, second, about_assistant, *FIXED_LINES)


async def test_once_the_work_is_in_the_first_question_is_the_line() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    await rig.service.submit_work(
        SAMPLE, session.id, WorkSubmit(profileId=SAMPLE.id, work="export const x = 1;")
    )
    template = TEMPLATES[0]
    first, second, _ = (f.question for f in template.follow_ups)
    found = await upcoming(rig, session.id)
    assert found.now == (first,)
    assert found.soon == (second, *FIXED_LINES)
    assert found.next_turn == (second,)
