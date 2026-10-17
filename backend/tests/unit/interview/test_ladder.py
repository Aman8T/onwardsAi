"""The rungs, in code: what the interviewer asks next after an answer is decided by fixed rules over the checked verdict, never by
the model. A story asks for the part it is missing; a failed judge records nothing; two tabs cannot overwrite each other."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from tayyari.core.settings import InterviewSettings
from tayyari.interview.errors import InterviewEnded, InterviewMovedOn, ModelUnavailable
from tayyari.interview.phrase import STORY_PARTS
from tayyari.llm.errors import ClaudeOverloaded, SpendCeilingReached
from tests.unit.interview.flows import A1, ROLLBACK, end, say, start
from tests.unit.interview.support import CTX, SAMPLE, judged, make_rig

STORY_A = (
    "Last spring our designer drew a sticky footer that Safari could not render. I rebuilt it with safe-area "
    "insets after talking to her, and we shipped it that week. Next time I would test on a phone first."
)
SITUATION = "our designer drew a sticky footer that Safari could not render"
ACTION = "I rebuilt it with safe-area insets after talking to her"
RESULT = "we shipped it that week"
REFLECTION = "Next time I would test on a phone first"
PARTS = {"situation": SITUATION, "action": ACTION, "result": RESULT}


def story_judged(verdict: str, parts: dict[str, str], answer: str = STORY_A) -> dict[str, Any]:
    return judged(answer, verdict, quote=ACTION, story=parts)


# ───────────────────────── a story asks for the part it is missing ─────────────────────────


async def test_a_full_story_without_a_looking_back_is_pushed_for_it() -> None:
    rig = make_rig()
    session = await start(rig, "stories", phrase="fails")
    assert session.current is not None and session.current.text.startswith(
        "Tell me about this: “Pairs with designers"
    )
    got = await say(rig, session.id, STORY_A, verdict=story_judged("defended", PARTS))
    assert got.current is not None
    assert (got.current.kind, got.current.rung, got.current.slotId) == ("followup", "push", "s1")
    assert got.current.text == "Looking back, what would you do differently?" == STORY_PARTS["reflection"]
    reflection = "Next time I would test on a phone before the design is signed off"
    nxt = await say(
        rig,
        session.id,
        reflection,
        verdict=judged(
            reflection,
            "defended",
            quote="I would test on a phone",
            story={"reflection": "I would test on a phone"},
        ),
    )
    assert nxt.current is not None and (nxt.current.slotId, nxt.current.rung) == (
        "s2",
        "surface",
    )  # a push is asked once


async def test_a_story_with_all_four_parts_goes_straight_on() -> None:
    rig = make_rig()
    session = await start(rig, "stories", phrase="fails")
    got = await say(
        rig, session.id, STORY_A, verdict=story_judged("defended", {**PARTS, "reflection": REFLECTION})
    )
    assert got.current is not None and (got.current.slotId, got.current.kind) == ("s2", "question")


@pytest.mark.parametrize(
    ("parts", "asked"),
    [
        ({"situation": SITUATION, "action": ACTION}, "result"),
        ({"action": ACTION, "result": RESULT}, "situation"),
        ({"action": ACTION}, "situation"),
    ],
)
async def test_a_story_missing_a_part_is_pushed_with_that_parts_question(
    parts: dict[str, str], asked: str
) -> None:
    rig = make_rig()
    session = await start(rig, "stories", phrase="fails")
    got = await say(
        rig, session.id, STORY_A, verdict=story_judged("defended", parts)
    )  # capped at Shaky by the missing part
    assert got.current is not None and (got.current.kind, got.current.rung) == ("followup", "push")
    assert got.current.text == STORY_PARTS[asked]
    assert STORY_PARTS == {
        "situation": "What was going on at the time, and what was at stake?",
        "action": "What did you do yourself?",
        "result": "What happened in the end?",
        "reflection": "Looking back, what would you do differently?",
    }


async def test_the_part_the_push_supplies_counts_for_the_story() -> None:
    rig = make_rig()
    session = await start(rig, "stories", phrase="fails")
    await say(
        rig, session.id, STORY_A, verdict=story_judged("shaky", {"situation": SITUATION, "action": ACTION})
    )
    await say(rig, session.id, STORY_A, verdict=story_judged("shaky", {"result": RESULT}))
    debrief = await end(rig, session.id, script={"slots": []})
    story = debrief.results[0]
    assert story.verdict == "shaky" and story.story is not None
    assert (story.story.situation, story.story.action, story.story.result, story.story.reflection) == (
        True,
        True,
        True,
        False,
    )


async def test_a_story_that_could_not_be_told_gets_the_easier_question() -> None:
    rig = make_rig()
    session = await start(rig, "stories", phrase="fails")
    vague = "I don't remember a specific time. It usually goes fine and nobody complains."
    got = await say(
        rig, session.id, vague, verdict=judged(vague, "couldnt", quote="I don't remember a specific time")
    )
    assert got.current is not None and (got.current.kind, got.current.rung) == ("floor", "floor")
    assert got.current.text == "Who else was involved?"
    nxt = await say(
        rig,
        session.id,
        "Mostly the designer and one other engineer",
        verdict=judged(
            "Mostly the designer and one other engineer", "shaky", quote="the designer and one other engineer"
        ),
    )
    assert nxt.current is not None and nxt.current.slotId == "s2"


async def test_a_story_with_nothing_to_quote_is_pushed_for_its_situation() -> None:
    rig = make_rig()
    session = await start(rig, "stories", phrase="fails")
    got = await say(
        rig,
        session.id,
        STORY_A,
        verdict=story_judged("defended", {}) | {"quotes": ["never said this at all"]},
    )
    assert (
        got.current is not None
        and got.current.text == STORY_PARTS["situation"]
        and got.current.rung == "push"
    )


# ───────────────────────── the other kinds ─────────────────────────


async def test_shaky_is_pushed_once_and_then_the_next_slot() -> None:
    rig = make_rig()
    session = await start(rig, "resume", phrase="fails")
    first = session.current
    assert first is not None and first.text.startswith("Your resume says “")
    pushed = await say(rig, session.id, A1, verdict=judged(A1, "shaky", quote=ROLLBACK))
    assert pushed.current is not None and (
        pushed.current.kind,
        pushed.current.rung,
        pushed.current.slotId,
    ) == ("followup", "push", "s1")
    assert pushed.current.text == "What did you do yourself, and how was it measured?"
    nxt = await say(rig, session.id, A1, verdict=judged(A1, "shaky", quote=ROLLBACK))
    assert nxt.current is not None and (nxt.current.kind, nxt.current.slotId) == (
        "question",
        "s2",
    )  # never a second push


async def test_couldnt_defend_gets_the_easier_question_and_then_the_next_slot() -> None:
    rig = make_rig()
    session = await start(rig, "resume", phrase="fails")
    floor = await say(rig, session.id, A1, verdict=judged(A1, "couldnt", quote=ROLLBACK))
    assert floor.current is not None and (floor.current.kind, floor.current.rung) == ("floor", "floor")
    assert floor.current.text == "What was your part, in one sentence?"
    nxt = await say(rig, session.id, A1, verdict=judged(A1, "defended", quote=ROLLBACK))
    assert nxt.current is not None and nxt.current.slotId == "s2"
    debrief = await end(rig, session.id, script={"slots": []})
    assert debrief.results[0].verdict == "shaky"  # an answer to the easier question is at most Shaky


async def test_defended_goes_straight_to_the_next_slot() -> None:
    rig = make_rig()
    session = await start(rig, "resume", phrase="fails")
    got = await say(rig, session.id, A1, verdict=judged(A1, "defended", quote=ROLLBACK))
    assert got.current is not None and (got.current.kind, got.current.slotId) == ("question", "s2")


async def test_a_slots_verdict_is_its_best_rung() -> None:
    rig = make_rig()
    session = await start(rig, "resume", phrase="fails")
    await say(rig, session.id, A1, verdict=judged(A1, "shaky", quote=ROLLBACK), after=10)
    better = "I measured it with the Lighthouse report before and after the change"
    await say(
        rig,
        session.id,
        better,
        verdict=judged(better, "defended", quote="the Lighthouse report before and after"),
        after=20,
    )
    debrief = await end(rig, session.id, script={"slots": []})
    first = debrief.results[
        0
    ]  # the push was better than the first answer, and the first answer cannot take that back
    assert first.verdict == "defended" and first.quote is not None
    assert (first.quote.text, first.quote.at) == ("the Lighthouse report before and after", 30)


# ───────────────────────── a judge that fails, and two tabs ─────────────────────────


async def test_a_judge_that_fails_records_nothing_and_the_person_can_answer_again() -> None:
    rig = make_rig()
    session = await start(rig)
    before = rig.repo.rows[session.id]
    rig.claude.script_structured("interview.judge", ClaudeOverloaded(retry_after_s=20))
    with pytest.raises(
        ModelUnavailable
    ) as failed:  # a 503 the person can read, with the wait the model asked for
        await say(rig, session.id, A1)
    assert failed.value.status == 503 and failed.value.retry_after_s == 20
    assert (
        failed.value.user_message
        == "Claude is busy right now (rate limited or overloaded). Try again in a minute."
    )
    assert rig.repo.rows[session.id] == before  # no turn, no version bump
    got = await say(rig, session.id, A1, verdict=judged(A1, "defended", quote=ROLLBACK))
    assert (
        got.current is not None
        and got.current.slotId == "s2"
        and [t.speaker for t in got.turns] == ["interviewer", "you", "interviewer"]
    )


async def test_the_spend_ceiling_stops_an_answer_without_recording_it() -> None:
    rig = make_rig()
    session = await start(rig)
    before = rig.repo.rows[session.id]
    rig.claude.script_structured("interview.judge", SpendCeilingReached())
    with pytest.raises(SpendCeilingReached):
        await say(rig, session.id, A1)
    assert rig.repo.rows[session.id] == before


async def test_two_tabs_cannot_overwrite_each_other() -> None:
    rig = make_rig()
    session = await start(rig)

    def other_tab(_call: Any) -> dict[str, Any]:
        row = rig.repo.rows[session.id]
        rig.repo.rows[session.id] = replace(row, version=row.version + 1)  # the other tab saved first
        return judged(A1, "defended", quote=ROLLBACK)

    rig.claude.script_structured("interview.judge", other_tab)
    with pytest.raises(InterviewMovedOn) as moved:
        await say(rig, session.id, A1)
    assert moved.value.user_message == "This interview moved on in another tab. Reload to carry on."
    assert moved.value.status == 409
    assert len(rig.repo.rows[session.id].state["turns"]) == 1  # our answer was not written


async def test_a_button_that_loses_the_race_is_refused_too() -> None:
    rig = make_rig()
    session = await start(rig)
    original = rig.repo.save

    async def lose(item: Any) -> bool:
        await original(
            replace(item, version=item.version)
        )  # someone else's save lands first (and fails the check itself)
        return False

    rig.repo.save = lose  # type: ignore[method-assign]
    with pytest.raises(InterviewMovedOn):
        await say(rig, session.id, action="skip")


async def test_two_tabs_ending_at_once_both_get_the_one_debrief() -> None:
    rig = make_rig()
    session = await start(rig)
    await say(rig, session.id, A1, verdict=judged(A1, "defended", quote=ROLLBACK))
    winner = await end(rig, session.id, script={"slots": []})

    original = rig.repo.get
    served = {"first": True}

    async def stale(profile_id: str, session_id: str) -> Any:
        row = await original(profile_id, session_id)
        if served["first"] and row is not None:
            served["first"] = False
            return replace(
                row, status="active", version=row.version - 1
            )  # a tab that read it before the other ended
        return row

    rig.repo.get = stale  # type: ignore[method-assign]
    rig.claude.script_structured("interview.debrief", {"slots": []})
    loser = await rig.service.end(SAMPLE, session.id, CTX)
    assert loser == winner


# ───────────────────────── the longest an interview can run ─────────────────────────


async def test_past_the_most_answers_the_interview_goes_straight_to_its_end() -> None:
    rig = make_rig(settings=InterviewSettings(max_turns=10))
    session = await start(rig)
    for turn in range(9):
        got = await say(rig, session.id, "yes", after=1)
        assert got.current is not None and got.current.kind == "question", turn
    last = await say(rig, session.id, "yes", after=1)
    assert last.current is not None and last.current.kind == "done" and last.slotIndex == 5
    assert last.current.text == "That's the last question. Thank you. Your debrief is ready."
    with pytest.raises(InterviewEnded):
        await say(rig, session.id, "yes")
    debrief = await end(rig, session.id, script={"slots": []})
    assert debrief.counts.notAssessed == 5 and debrief.firstThing is None


async def test_buttons_count_towards_the_most_answers_but_say_it_again_does_not() -> None:
    rig = make_rig(settings=InterviewSettings(max_turns=10))
    session = await start(rig, "resume", phrase="fails")
    for _ in range(30):
        await say(rig, session.id, "say that again")
    assert (await rig.service.view(SAMPLE, session.id)) is not None  # still on its first question
    got = None
    for _ in range(5):  # five slots: five skips
        got = await say(rig, session.id, action="skip")
    assert got is not None and got.current is not None and got.current.kind == "done"
