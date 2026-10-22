"""The debrief: counts, the first thing to work on and the summary are code; Say, Avoid and Fix are the model's words, checked against
what the person said and the sources, and replaced by a plain line when a check fails. Known-bad lines prove each check fires."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tayyari.contract.interview import (
    InterviewLine,
    PlanSlot,
    Quote,
    SlotResult,
    SlotSource,
    StoryParts,
    Turn,
)
from tayyari.interview.debrief import (
    DEBRIEF_SCHEMA,
    SAY_TEMPLATES,
    Outcome,
    Written,
    assemble,
    check_avoid,
    check_fix,
    check_say,
    check_written,
    counts_of,
    default_fix,
    fallback_say,
    first_thing,
    kept_until,
    minutes_of,
    outcomes,
    summary_of,
    write_debrief,
)
from tayyari.interview.state import RungResult, SessionState, SlotState
from tayyari.llm.errors import ClaudeOverloaded, SpendCeilingReached
from tayyari.prompts.interview import DEBRIEF_SYSTEM
from tests.fakes.claude import FakeClaudeClient
from tests.unit.interview.support import CTX, MODEL, evidence_source, posting_source, slot_of

LCP = "Checkout LCP 4.1s to 1.9s at Kitebird"
VAGUE = "We, um, did a lot of things. Images, the bundle, some fonts. And it got a lot faster."
PUSHED = "I think the biggest one was the images? I'm not sure how we measured it, the PM had a dashboard."
GOOD_SAY = (
    "The payment step's LCP was 4.1s. I found [what was slow] with [the tool you used], and [the change] moved it most. "
    "It ended at 1.9s."
)


def you(text: str, at: int = 150, slot: str = "s1") -> Turn:
    return Turn(speaker="you", text=text, at=at, slotId=slot, rung="surface", mode="speech")


def outcome(
    kind: str = "ask",
    verdict: str = "shaky",
    *,
    sources: list[SlotSource] | None = None,
    answers: list[str] | None = None,
    topic: str = "Web performance",
    why: str = "The number is on your resume, but the answer didn't say how it was found.",
    new_fact: str | None = None,
    story: StoryParts | None = None,
    slot_id: str = "s1",
) -> Outcome:
    slot = slot_of(
        kind,
        topic,
        *(
            sources
            if sources is not None
            else [posting_source("Experience with basic performance optimization"), evidence_source(LCP)]
        ),
        slot_id=slot_id,
    )
    texts = answers if answers is not None else [VAGUE, PUSHED]
    turns = tuple(you(t, 150 + 70 * i, slot_id) for i, t in enumerate(texts))
    quote = Quote(text="I'm not sure how we measured it", at=220) if verdict != "not_assessed" else None
    return Outcome(
        slot,
        "Your resume says LCP dropped. How did you find what was slow?",
        verdict,
        quote,
        why,
        story,
        new_fact,
        turns,
    )  # type: ignore[arg-type]


# ───────────────────────── Say ─────────────────────────


def say_raw(text: str = GOOD_SAY, sources: list[str] | None = None) -> dict[str, Any]:
    return {"sources": ["Resume", "Your answer"] if sources is None else sources, "text": text}


def test_a_good_say_line_passes_with_its_placeholders_in_order() -> None:
    got = check_say(say_raw(), outcome())
    assert got is not None
    assert got.text == f"“{GOOD_SAY}”" and got.sources == ["Resume", "Your answer"]
    assert got.placeholders == ["what was slow", "the tool you used", "the change"]


def test_a_say_line_wrapped_in_straight_quotes_or_none_is_written_in_curly_quotes() -> None:
    for text in (f'"{GOOD_SAY}"', GOOD_SAY, f"“{GOOD_SAY}”"):
        got = check_say(say_raw(text), outcome())
        assert got is not None and got.text == f"“{GOOD_SAY}”"


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (GOOD_SAY.replace("4.1s", "5.1s"), "an invented number"),
        (GOOD_SAY.replace("1.9s", "1.9s and 40 percent more orders"), "a second invented number"),
        (GOOD_SAY.replace("The payment step's", "Zomato's payment step's"), "an invented company"),
        (GOOD_SAY + " I used Kubernetes for it.", "an invented tool"),
        (GOOD_SAY + " I led the whole performance team.", "a seniority claim the sources do not make"),
        (GOOD_SAY.replace("[the tool you used]", "[x]"), "a placeholder under two characters"),
        (
            GOOD_SAY.replace("[the tool you used]", "[" + "tool " * 13 + "]"),
            "a placeholder over sixty characters",
        ),
        (GOOD_SAY.replace("[what was slow]", "[what was [really] slow]"), "nested brackets"),
        (GOOD_SAY.replace("[the change]", "[the change"), "a stray bracket"),
        (GOOD_SAY + " I stayed confident throughout.", "a personality word"),
        ("I [did it]", "fewer than four words"),
        (GOOD_SAY + " " + "I also [did more things] and [more again]. " * 8, "over 260 characters"),
        ("", "empty"),
    ],
)
def test_a_say_line_that_fails_a_check_is_not_used(text: str, why: str) -> None:
    assert check_say(say_raw(text), outcome()) is None, why


def test_a_say_line_may_use_a_number_that_is_in_the_answer_or_the_sources() -> None:
    answer = "We cut the bundle by 30 percent by removing moment and lodash."
    text = "I cut the bundle by 30 percent by [what you removed], and LCP went from 4.1s to [the new number]."
    got = check_say(say_raw(text), outcome(answers=[answer]))
    assert got is not None and got.placeholders == ["what you removed", "the new number"]


def test_a_seniority_verb_is_fine_when_the_sources_or_the_answer_use_it() -> None:
    sources = [evidence_source("Led the storefront migration to React 18 and Vite")]
    text = (
        "I led the storefront migration to React 18, and [what changed, with a number you can stand behind]."
    )
    assert (
        check_say(
            say_raw(text, ["Resume"]), outcome("claim", sources=sources, answers=["I did the cutover."])
        )
        is not None
    )
    assert (
        check_say(
            say_raw(text, ["Resume"]),
            outcome(
                "claim",
                sources=[evidence_source("Moved the storefront to React 18")],
                answers=["I did the cutover."],
            ),
        )
        is None
    )


def test_the_sources_of_a_say_line_must_be_the_slots_own_labels_or_your_answer() -> None:
    got = check_say(say_raw(sources=["Resume", "LinkedIn", "Resume", "Your answer"]), outcome())
    assert got is not None and got.sources == ["Resume", "Your answer"]
    assert check_say(say_raw(sources=["LinkedIn"]), outcome()) is None
    assert check_say(say_raw(sources=[]), outcome()) is None
    assert check_say(say_raw(sources=["GitHub"]), outcome()) is None  # the slot has no GitHub source


@pytest.mark.parametrize("raw", [None, "text", [], 5, {}, {"text": 7, "sources": "Resume"}])
def test_a_malformed_say_is_not_used(raw: Any) -> None:
    assert check_say(raw, outcome()) is None


def test_a_defended_slot_without_a_usable_say_leads_with_the_result_it_already_has() -> None:
    got = fallback_say(
        outcome("ask", "defended", answers=["I merged the steps and rolled the cart back on failure"])
    )
    assert got is not None
    assert got.text == f"Lead with the result you already have: “{LCP}”."
    assert (got.placeholders, got.sources) == ([], ["Resume"])


def test_a_defended_slot_whose_numbers_the_answer_used_gets_no_say_at_all() -> None:
    assert (
        fallback_say(
            outcome("ask", "defended", answers=["LCP went from 4.1s to 1.9s after I split the bundle"])
        )
        is None
    )
    assert (
        fallback_say(
            outcome(
                "claim",
                "defended",
                sources=[evidence_source("Mentored interns")],
                answers=["I paired with them"],
            )
        )
        is None
    )
    assert (
        fallback_say(outcome("ask", "defended", sources=[posting_source("Experience with Git 2")])) is None
    )  # only evidence counts


def test_a_long_evidence_line_is_clipped_in_the_lead_line() -> None:
    line = "Rebuilt the three-step checkout as a single-page flow in React and TypeScript with optimistic cart updates; checkout conversion rose 9.4% in the A/B test"
    got = fallback_say(
        outcome("claim", "defended", sources=[evidence_source(line)], answers=["I rebuilt it"])
    )
    assert got is not None and len(got.text) <= 200 and got.text.endswith("…”.")


@pytest.mark.parametrize("kind", ["ask", "claim", "story", "gap", "task"])
@pytest.mark.parametrize("verdict", ["shaky", "couldnt"])
def test_a_slot_that_was_not_defended_gets_its_kinds_template(kind: str, verdict: str) -> None:
    got = fallback_say(outcome(kind, verdict))
    assert got is not None and got.text == SAY_TEMPLATES[kind] and got.sources == []
    assert got.text.startswith("“") and got.text.endswith("”")
    assert got.placeholders and all(2 <= len(p) <= 60 for p in got.placeholders)
    assert all(f"[{p}]" in got.text for p in got.placeholders)


def test_the_templates_are_the_briefs_words() -> None:
    assert (
        SAY_TEMPLATES["ask"]
        == SAY_TEMPLATES["claim"]
        == (
            "“[What the situation was]. I [what you did myself], and [what changed, with a number you can stand behind].”"
        )
    )
    assert (
        SAY_TEMPLATES["story"] == "“On [the project], [what happened]. I [what you did], and [how it ended].”"
    )
    assert SAY_TEMPLATES["gap"] == (
        "“I haven't done [the ask] at work yet. The closest is [what you did], and I'd start with [your first step].”"
    )
    assert SAY_TEMPLATES["task"] == "“I [what you did first] because [why]. I'd test it by [how].”"


# ───────────────────────── Avoid ─────────────────────────


def test_an_avoid_line_quotes_the_persons_words_with_short_advice() -> None:
    got = check_avoid(
        {"quote": "We, um, did a lot of things", "advice": "Say which change was yours."}, outcome()
    )
    assert got is not None and (got.quote, got.advice) == (
        "We, um, did a lot of things",
        "Say which change was yours.",
    )


def test_an_avoid_quote_comes_back_in_the_wording_of_the_answer() -> None:
    got = check_avoid(
        {"quote": "WE, um, did a lot of  things", "advice": "Say which change was yours."}, outcome()
    )
    assert got is not None and got.quote == "We, um, did a lot of things"


@pytest.mark.parametrize(
    "raw",
    [
        {
            "quote": "We rewrote everything in Rust last year",
            "advice": "Say which change was yours.",
        },  # not in the answers
        {"quote": "We, um, did a lot of things", "advice": "Sound more confident."},  # personality
        {
            "quote": "We, um, did a lot of things",
            "advice": "Say it with " + "much more detail " * 10,
        },  # too long
        {
            "quote": "We, um, did a lot of things",
            "advice": "Say that LCP fell by 70% in 2019.",
        },  # invented figures
        {"quote": "We, um", "advice": "Say which change was yours."},  # fewer than three words
        {"quote": "", "advice": "Say which change was yours."},
        {"quote": "We, um, did a lot of things", "advice": ""},
        {"quote": "We, um, did a lot of things"},
        None,
        "text",
    ],
)
def test_an_avoid_line_that_fails_a_check_is_dropped(raw: Any) -> None:
    assert check_avoid(raw, outcome()) is None


def test_an_avoid_quote_must_be_a_passage_of_one_answer_not_of_two_joined() -> None:
    joined = "a lot faster. I think the biggest one was the images"
    assert check_avoid({"quote": joined, "advice": "Pick one thing."}, outcome()) is None


# ───────────────────────── Fix ─────────────────────────


GOOD_FIX = "Find the pull request or report behind the LCP change and note the one change that moved it most."


def test_a_good_fix_step_passes_with_its_minutes() -> None:
    got = check_fix({"text": GOOD_FIX, "minutes": 20}, outcome())
    assert (got.text, got.minutes) == (GOOD_FIX, 20)


@pytest.mark.parametrize(
    "text",
    [
        "The LCP change is what to look for.",  # does not start with a verb
        "You have built a fast checkout, so write it down.",  # a claim about the person
        "Your experience shows speed, so find the report.",
        "Write down what Zomato did for LCP in 2019.",  # a name and a year that are nowhere
        "Practise sounding confident when you answer.",  # personality
        "Find the report. " * 20,  # over 200 characters
        "",
        "Finding the report behind the LCP change.",  # a gerund
    ],
)
def test_a_fix_step_that_fails_a_check_is_the_plain_one(text: str) -> None:
    got = check_fix({"text": text, "minutes": 30}, outcome())
    assert got == default_fix("Web performance")
    assert (
        got.text == "Write down one real example for Web performance: what you did and what changed."
        and got.minutes == 15
    )


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (2, 5),
        (0, 5),
        (-4, 5),
        (5, 5),
        (45, 45),
        (120, 120),
        (500, 120),
        (12.6, 13),
        ("ten", 15),
        (None, 15),
        (True, 15),
    ],
)
def test_the_minutes_are_clamped_between_five_and_a_hundred_and_twenty(minutes: Any, expected: int) -> None:
    assert check_fix({"text": GOOD_FIX, "minutes": minutes}, outcome()).minutes == expected


def test_a_missing_fix_is_the_plain_one() -> None:
    assert check_fix(None, outcome()) == default_fix("Web performance")
    assert check_fix({}, outcome(topic="Mentoring")).text.startswith(
        "Write down one real example for Mentoring:"
    )


# ───────────────────────── the model's lines, slot by slot ─────────────────────────


def written_raw(slot_id: str = "s1") -> dict[str, Any]:
    return {
        "slots": [
            {
                "id": slot_id,
                "say": say_raw(),
                "avoid": {"quote": "We, um, did a lot of things", "advice": "Say which change was yours."},
                "fix": {"text": GOOD_FIX, "minutes": 20},
            }
        ]
    }


def test_each_assessed_slot_gets_its_lines_a_defended_one_gets_no_fix() -> None:
    shaky = outcome("ask", "shaky", slot_id="s1")
    defended = outcome(
        "claim",
        "defended",
        sources=[evidence_source("Owns Kite UI: 42 components")],
        answers=["I own 42 components"],
        slot_id="s2",
    )
    skipped = outcome("gap", "not_assessed", slot_id="s3")
    raw = written_raw("s1")
    raw["slots"].append(
        {
            "id": "s2",
            "say": say_raw("I own [what owning means week to week], with 42 components", ["Resume"]),
            "avoid": {"quote": "", "advice": ""},
            "fix": {"text": GOOD_FIX, "minutes": 20},
        }
    )
    got = check_written(raw, [shaky, defended, skipped])
    assert set(got) == {"s1", "s2"}  # a slot that was not assessed has no lines
    assert got["s1"].say is not None and got["s1"].avoid is not None and got["s1"].fix is not None
    assert (
        got["s2"].fix is None and got["s2"].avoid is None
    )  # defended: nothing to fix; an empty avoid is dropped
    assert got["s2"].say is not None and got["s2"].say.placeholders == ["what owning means week to week"]


def test_a_model_that_returned_nothing_gives_the_code_only_lines() -> None:
    shaky = outcome("ask", "shaky", slot_id="s1")
    defended = outcome("ask", "defended", answers=["I changed the images"], slot_id="s2")
    for raw in (None, {}, {"slots": "no"}, {"slots": [None, {"id": 3}]}):
        got = check_written(raw, [shaky, defended])
        assert got["s1"] == Written(say=fallback_say(shaky), avoid=None, fix=default_fix("Web performance"))
        assert got["s2"] == Written(say=fallback_say(defended), avoid=None, fix=None)
        assert got["s2"].say is not None and got["s2"].say.text.startswith("Lead with the result")


def test_a_known_bad_say_is_swapped_for_the_template_but_a_good_avoid_and_fix_stay() -> None:
    raw = written_raw()
    raw["slots"][0]["say"] = say_raw(GOOD_SAY.replace("4.1s", "9.9s"))
    got = check_written(raw, [outcome()])["s1"]
    assert got.say == fallback_say(outcome()) and got.say is not None and got.say.text == SAY_TEMPLATES["ask"]
    assert got.avoid is not None and got.fix is not None and got.fix.minutes == 20


# ───────────────────────── the call ─────────────────────────


async def call_debrief(claude: FakeClaudeClient, outs: list[Outcome]):
    return await write_debrief(
        claude=claude, model=MODEL, outs=outs, title="Frontend Engineer at Headout", ctx=CTX
    )


async def test_one_call_over_the_assessed_slots_with_their_answers_and_times() -> None:
    outs = [
        outcome(slot_id="s1"),
        outcome("gap", "not_assessed", slot_id="s2"),
        outcome("claim", "defended", slot_id="s3"),
    ]
    claude = FakeClaudeClient().script_structured("interview.debrief", written_raw("s1"))
    got = await call_debrief(claude, outs)
    (call,) = claude.structured_calls
    assert (call.ctx.purpose, call.model, call.effort, call.schema) == (
        "interview.debrief",
        MODEL,
        None,
        DEBRIEF_SCHEMA,
    )
    assert call.system_text == DEBRIEF_SYSTEM
    assert "s1 | kind ask | Web performance | verdict shaky" in call.user_text
    assert "s3 | kind claim" in call.user_text and "s2 |" not in call.user_text  # not assessed: not sent
    assert f'answer at 2:30: "{VAGUE}"' in call.user_text and f'answer at 3:40: "{PUSHED}"' in call.user_text
    assert (
        f"  - evidence (Resume): {LCP}" in call.user_text
        and "  - posting: Experience with basic performance" in call.user_text
    )
    assert got["s1"].fix is not None and got["s1"].fix.minutes == 20


async def test_a_failed_call_or_the_spend_ceiling_gives_the_code_only_lines() -> None:
    for failure in (ClaudeOverloaded(), SpendCeilingReached()):
        claude = FakeClaudeClient().script_structured("interview.debrief", failure)
        got = await call_debrief(claude, [outcome()])
        assert len(claude.structured_calls) == 1
        assert got["s1"] == Written(
            say=fallback_say(outcome()), avoid=None, fix=default_fix("Web performance")
        )


async def test_nothing_assessed_means_no_call() -> None:
    claude = FakeClaudeClient()
    got = await call_debrief(claude, [outcome("ask", "not_assessed")])
    assert claude.structured_calls == [] and got == {}


def test_the_schema_puts_the_citations_before_the_text_they_support() -> None:
    item = DEBRIEF_SCHEMA["properties"]["slots"]["items"]
    assert list(item["properties"]) == ["id", "say", "avoid", "fix", "softer"] == item["required"]
    assert list(item["properties"]["say"]["properties"]) == ["sources", "text"]
    assert list(item["properties"]["avoid"]["properties"]) == ["quote", "advice"]
    assert list(item["properties"]["fix"]["properties"]) == ["text", "minutes"]


# ───────────────────────── counts, the first thing, the summary ─────────────────────────


def result(
    kind: str, verdict: str, topic: str = "A topic", why: str = "A reason.", slot_id: str = "s1", **over: Any
) -> SlotResult:
    return SlotResult(
        slotId=slot_id, kind=kind, topic=topic, question="Q?", sources=[], verdict=verdict, why=why, **over
    )  # type: ignore[arg-type]


def test_counts_are_by_verdict() -> None:
    results = [
        result("ask", v) for v in ("defended", "defended", "shaky", "couldnt", "not_assessed", "not_assessed")
    ]
    counts = counts_of(results)
    assert (counts.defended, counts.shaky, counts.couldnt, counts.notAssessed) == (2, 1, 1, 2)


def test_the_first_thing_is_the_first_slot_not_defended_in_the_order_ask_gap_claim_story_task() -> None:
    results = [
        result("story", "couldnt", "Handoffs", "No example yet.", "s1"),
        result("claim", "shaky", "Mentoring", "Some of it.", "s2"),
        result("gap", "shaky", "Responsive", "Not how.", "s3"),
        result("ask", "defended", "React", "Good.", "s4"),
        result("ask", "shaky", "Performance", "The number is on your resume.", "s5"),
    ]
    assert first_thing(results) == ("Performance. The number is on your resume.", "s5")
    assert first_thing(results[:4]) == ("Responsive. Not how.", "s3")
    assert first_thing(results[:2]) == ("Mentoring. Some of it.", "s2")


def test_not_assessed_is_never_the_thing_to_work_on() -> None:
    assert first_thing([result("ask", "not_assessed"), result("claim", "defended")]) == (None, None)
    assert first_thing(
        [result("ask", "not_assessed", slot_id="s1"), result("story", "shaky", "X", "Y.", "s2")]
    ) == ("X. Y.", "s2")
    assert first_thing([]) == (None, None)


def role_state(employer: str | None = "Headout", kind: str = "role") -> SessionState:
    return SessionState(
        kind=kind,
        title="Frontend Engineer at Headout",
        minutes=20,
        slots=[],
        slotStates=[],
        employer=employer,
    )  # type: ignore[arg-type]


def test_the_summary_counts_the_roles_asks_in_code() -> None:
    results = [
        result("ask", "defended"),
        result("ask", "shaky"),
        result("gap", "shaky"),
        result("claim", "defended"),
    ]
    assert (
        summary_of(role_state(), results) == "Of Headout's 2 asks you show, you defended 1 and 1 was shaky."
    )
    two = [result("ask", "shaky"), result("ask", "shaky"), result("ask", "defended")]
    assert summary_of(role_state(), two) == "Of Headout's 3 asks you show, you defended 1 and 2 were shaky."
    assert (
        summary_of(role_state(), [result("ask", "defended")])
        == "Of Headout's 1 ask you show, you defended 1."
    )
    assert summary_of(role_state(), [result("ask", "couldnt"), result("ask", "not_assessed")]) == (
        "Of Headout's 2 asks you show, you defended 0."
    )


def test_a_defended_gap_with_a_new_fact_adds_the_sentence() -> None:
    results = [
        result("ask", "defended"),
        result("ask", "shaky"),
        result("gap", "defended", newFact="Tests checkout in Chrome."),
    ]
    assert summary_of(role_state(), results) == (
        "Of Headout's 2 asks you show, you defended 1 and 1 was shaky. "
        "The gap question went well: add what you said to your profile."
    )
    only_gap = [result("gap", "defended", newFact="Tests checkout in Chrome.")]
    assert (
        summary_of(role_state(), only_gap) == "The gap question went well: add what you said to your profile."
    )
    assert summary_of(role_state(), [result("gap", "defended")]) is None  # no fact to add


def test_only_a_role_interview_has_a_summary() -> None:
    assert summary_of(role_state(kind="resume"), [result("ask", "defended")]) is None
    assert summary_of(role_state(employer=None), [result("ask", "defended")]) is None


def test_minutes_are_the_last_turns_second_rounded_up_and_at_least_one() -> None:
    def turns(*ats: int) -> list[Turn]:
        return [Turn(speaker="you", text="x", at=at) for at in ats]

    assert [
        minutes_of(turns(0)),
        minutes_of(turns(1)),
        minutes_of(turns(60)),
        minutes_of(turns(61)),
        minutes_of(turns(14 * 60)),
    ] == [1, 1, 1, 2, 14]
    assert minutes_of([]) == 1


def test_kept_until_is_the_day_the_transcript_is_deleted() -> None:
    assert kept_until(datetime(2026, 10, 16, 23, 59, tzinfo=UTC), 30) == "2026-11-15"
    assert kept_until(datetime(2026, 12, 15, 9, 0, tzinfo=UTC), 30) == "2027-01-14"


# ───────────────────────── outcomes and the whole debrief ─────────────────────────


def slot_pair(kind: str, slot_id: str, **state: Any) -> tuple[PlanSlot, SlotState]:
    return (
        slot_of(kind, f"Topic {slot_id}", evidence_source(f"Evidence line for {slot_id}"), slot_id=slot_id),
        SlotState(id=slot_id, question=f"Question {slot_id}?", **state),
    )


def session(*pairs: tuple[PlanSlot, SlotState], turns: list[Turn] | None = None, **over: Any) -> SessionState:
    return SessionState(
        kind=over.pop("kind", "resume"),
        title=over.pop("title", "Defend your resume"),
        minutes=15,
        slots=[p[0] for p in pairs],
        slotStates=[p[1] for p in pairs],
        turns=turns or [],
        **over,
    )


def test_a_slots_outcome_is_its_best_rung_and_a_tie_goes_to_the_later_one() -> None:
    q1, q2 = Quote(text="first quote here", at=10), Quote(text="second quote here", at=80)
    slot, st = slot_pair(
        "ask",
        "s1",
        rungs={
            "surface": RungResult(verdict="shaky", quote=q1, why="surface"),
            "push": RungResult(verdict="shaky", quote=q2, why="push"),
        },
    )
    (got,) = outcomes(session((slot, st)))
    assert (got.verdict, got.quote, got.why) == ("shaky", q2, "push")
    better = st.model_copy(
        update={"rungs": {**st.rungs, "surface": RungResult(verdict="defended", quote=q1, why="best")}}
    )
    (got,) = outcomes(session((slot, better)))
    assert (got.verdict, got.quote, got.why) == (
        "defended",
        q1,
        "best",
    )  # shaky can never lower a defended rung


def test_a_new_fact_belongs_to_the_defended_rung_that_stated_it() -> None:
    q1, q2 = Quote(text="the answer that said it", at=10), Quote(text="a later defended answer", at=80)
    slot, st = slot_pair(
        "gap",
        "s1",
        rungs={
            "surface": RungResult(
                verdict="defended", quote=q1, why="a", newFact="Tests in Chrome and Safari."
            ),
            "push": RungResult(verdict="defended", quote=q2, why="b"),
        },
    )
    (got,) = outcomes(session((slot, st)))
    assert (got.quote, got.new_fact) == (q1, "Tests in Chrome and Safari.")
    shaky = slot_pair(
        "gap", "s1", rungs={"surface": RungResult(verdict="shaky", quote=q1, why="a", newFact="Not kept.")}
    )
    assert outcomes(session(shaky))[0].new_fact is None  # a fact only goes with a defended answer


def test_skipped_unreached_and_unquotable_slots_are_not_assessed_with_their_own_reason() -> None:
    skipped = slot_pair("ask", "s1", skipped=True, reached=True)
    unreached = slot_pair("claim", "s2")
    unanswered = slot_pair("gap", "s4", reached=True)  # asked, then only a dispute or a repeat before the end
    unquotable = slot_pair(
        "story",
        "s3",
        reached=True,
        rungs={"surface": RungResult(verdict="not_assessed", why="Nothing could be quoted.")},
    )
    got = outcomes(session(skipped, unreached, unquotable, unanswered))
    assert [o.verdict for o in got] == ["not_assessed"] * 4
    assert got[0].why == "You skipped this one. Not assessed never counts against you."
    assert got[1].why == "This one wasn't asked. Not assessed never counts against you."
    assert got[2].why == "Nothing could be quoted." and got[2].quote is None
    assert got[3].why == "This one wasn't answered. Not assessed never counts against you."


def test_the_question_about_the_assistant_does_not_apply_when_it_was_never_used() -> None:
    first, asked_it = slot_pair("task", "s1"), slot_pair("task", "s2", needsAssistant=True)
    assert [o.slot.id for o in outcomes(session(first, asked_it, kind="work"))] == ["s1"]
    from tayyari.contract.interview import AssistantTurn

    used = session(first, asked_it, kind="work", assistant=[AssistantTurn(you="why", assistant="because")])
    assert [o.slot.id for o in outcomes(used)] == ["s1", "s2"]


def test_a_story_outcome_carries_the_parts_any_rung_had() -> None:
    slot, st = slot_pair(
        "story",
        "s1",
        reached=True,
        rungs={
            "surface": RungResult(
                verdict="shaky",
                quote=Quote(text="a quote here ok", at=5),
                story=StoryParts(situation=True, action=True, result=False, reflection=False),
            ),
            "push": RungResult(
                verdict="defended",
                quote=Quote(text="and a later one", at=40),
                story=StoryParts(situation=False, action=False, result=True, reflection=False),
            ),
        },
    )
    (got,) = outcomes(session((slot, st)))
    assert got.story == StoryParts(situation=True, action=True, result=True, reflection=False)
    assert (
        outcomes(
            session(
                slot_pair(
                    "ask",
                    "s1",
                    reached=True,
                    rungs={"surface": RungResult(verdict="shaky", quote=Quote(text="a quote here ok", at=5))},
                )
            )
        )[0].story
        is None
    )


def test_assembling_a_debrief_puts_it_all_together() -> None:
    slot, st = slot_pair(
        "claim",
        "s1",
        reached=True,
        rungs={
            "surface": RungResult(verdict="shaky", quote=Quote(text="not sure how", at=95), why="Some of it.")
        },
    )
    done, done_state = slot_pair(
        "claim",
        "s2",
        reached=True,
        rungs={
            "surface": RungResult(
                verdict="defended", quote=Quote(text="I paired with them", at=200), why="Good."
            )
        },
    )
    skipped = slot_pair("claim", "s3", skipped=True)
    turns = [
        Turn(speaker="interviewer", text="Q?", at=0, slotId="s1"),
        you("not sure how", 95, "s1"),
        you("I paired with them", 200, "s2"),
    ]
    state = session((slot, st), (done, done_state), skipped, turns=turns)
    created = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
    written = {"s1": Written(fix=default_fix("Topic s1"))}
    debrief = assemble(session_id="abc", state=state, created_at=created, retention_days=30, written=written)
    assert (debrief.sessionId, debrief.kind, debrief.title, debrief.createdAt) == (
        "abc",
        "resume",
        "Defend your resume",
        created.isoformat(),
    )
    assert (debrief.minutes, debrief.keptUntil) == (4, "2026-11-15")
    assert [(r.slotId, r.verdict) for r in debrief.results] == [
        ("s1", "shaky"),
        ("s2", "defended"),
        ("s3", "not_assessed"),
    ]
    assert (
        debrief.counts.defended,
        debrief.counts.shaky,
        debrief.counts.couldnt,
        debrief.counts.notAssessed,
    ) == (1, 1, 0, 1)
    assert debrief.firstThing == "Topic s1. Some of it." and debrief.firstSlotId == "s1"
    assert debrief.summary is None and debrief.checks is None and debrief.assistantUse is None
    assert debrief.results[0].fix == default_fix("Topic s1") and debrief.results[2].quote is None
    assert debrief.transcript == turns and debrief.task is None and debrief.work is None
    assert InterviewLine(kind="done", text="x").kind == "done"
