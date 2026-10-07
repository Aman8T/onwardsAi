"""The first-read pack: its questions and wire shape, the interpretation tables, and what the service does with them."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tayyari.core.limits import JEV_FIRST_READ_RESUME_CHARS, SYNTH_NOTES_CLIP_CHARS
from tayyari.decisions.packs.common import SIGNAL_MAX_CHARS
from tayyari.decisions.packs.first_read import (
    CAREER_FAMILY_ID,
    CAREER_STAGE_ID,
    DEPTH_ID,
    DEPTH_LEVELS,
    FAMILIES,
    FIRST_READ_PACK,
    FLAG_IDS,
    FLAGS,
    MAX_LINES,
    RESUME_LIKE_ID,
    STAGES,
    build_state,
    spec_ids_to_ask,
)
from tayyari.decisions.service import FirstReadInput
from tayyari.llm.errors import JevError
from tests.fakes.jev import choice_answer, noul_answer, score_answer
from tests.unit.decisions.packs.support import (
    CTX,
    FAMILY_LABELS,
    STAGE_LABELS,
    TODAY,
    PackHarness,
    first_read_answers,
    resume_input,
)

Harness = Callable[..., PackHarness]

ALL_IDS = [CAREER_FAMILY_ID, CAREER_STAGE_ID, DEPTH_ID, *FLAG_IDS, RESUME_LIKE_ID]


# ───────────────────────── the question set ─────────────────────────


def test_the_pack_asks_the_agreed_questions() -> None:
    assert [s.id for s in FIRST_READ_PACK.specs] == ALL_IDS
    assert len(ALL_IDS) == 11
    kinds = {s.id: s.question.kind for s in FIRST_READ_PACK.specs}
    assert kinds[CAREER_FAMILY_ID] == "choice"
    assert kinds[CAREER_STAGE_ID] == "choice"
    assert kinds[DEPTH_ID] == "score"
    assert all(kinds[i] == "noul" for i in (*FLAG_IDS, RESUME_LIKE_ID))
    assert {s.pack for s in FIRST_READ_PACK.specs} == {"first_read"}
    assert {s.version for s in FIRST_READ_PACK.specs} == {"2"}


def test_the_flags_are_the_seven_agreed_nouls() -> None:
    assert {f.id.removeprefix("first_read.") for f in FLAGS} == {
        "leads_or_mentors",
        "public_proof",
        "measured_outcomes",
        "ai_experience",
        "progression_promoted",
        "progression_scope",
        "progression_switch",
    }


def test_the_career_family_is_field_neutral_with_example_roles() -> None:
    question = FIRST_READ_PACK.spec(CAREER_FAMILY_ID).question
    assert len(question.option_labels) == 18
    assert "other_or_unclear" in question.option_labels
    assert {"software_engineering", "healthcare_clinical", "trades_skilled_services"} <= set(
        question.option_labels
    )
    assert question.criteria is not None and isinstance(question.criteria, dict)
    assert all(isinstance(d, str) and 20 < len(d) <= 255 for d in question.criteria.values())
    assert len(FAMILIES) == 18 and len({o.display for o in FAMILIES}) == 18


def test_the_career_stage_is_described_by_situations_not_years() -> None:
    question = FIRST_READ_PACK.spec(CAREER_STAGE_ID).question
    assert list(question.option_labels) == STAGE_LABELS
    assert "unclear" in question.option_labels and len(STAGES) == 7
    text = " ".join(str(d) for d in question.criteria.values()).lower()  # type: ignore[union-attr]
    assert "year" not in text


def test_depth_has_six_levels_described_as_situations_with_a_lower_pick_rule() -> None:
    question = FIRST_READ_PACK.spec(DEPTH_ID).question
    assert question.level_count == 6 == len(DEPTH_LEVELS)
    instructions = str(question.instructions)
    assert "not from degrees" in instructions
    assert "pick the lower" in instructions
    assert all(level.strip() for level in DEPTH_LEVELS)


def test_no_question_asks_jev_to_count_or_work_with_dates() -> None:
    forbidden = ("how many", "how long", "years of", "number of years", "since when", "date", "today")
    for spec in FIRST_READ_PACK.specs:
        wire = str(spec.question.to_wire()).lower()
        assert not any(word in wire for word in forbidden), spec.id


def test_every_choice_description_fits_the_jev_limit_and_nouls_carry_criteria() -> None:
    for spec in FIRST_READ_PACK.specs:
        q = spec.question
        if q.kind == "choice":
            assert q.criteria and all(len(str(d)) <= 255 for d in q.criteria.values()), spec.id
        if q.kind == "noul":
            assert set(q.criteria or {}) == {"true", "false"}, spec.id


# ───────────────────────── the state ─────────────────────────


def test_the_state_is_the_resume_and_optional_notes() -> None:
    assert build_state(FirstReadInput("My resume", None, TODAY)) == {"resume": "My resume"}
    assert build_state(FirstReadInput("My resume", "  ", TODAY)) == {"resume": "My resume"}
    assert build_state(FirstReadInput("My resume", "Notes here", TODAY)) == {
        "resume": "My resume",
        "notes": "Notes here",
    }


def test_the_state_clips_the_resume_and_the_notes() -> None:
    state = build_state(FirstReadInput("r" * (JEV_FIRST_READ_RESUME_CHARS + 500), "n" * 9000, TODAY))
    assert len(state["resume"]) == JEV_FIRST_READ_RESUME_CHARS
    assert len(state["notes"]) == SYNTH_NOTES_CLIP_CHARS


def test_resume_like_is_not_asked_of_a_notes_only_input() -> None:
    notes_only = FirstReadInput(None, "I like building things", TODAY)
    assert RESUME_LIKE_ID not in spec_ids_to_ask(notes_only)
    assert RESUME_LIKE_ID in spec_ids_to_ask(resume_input())
    assert len(spec_ids_to_ask(notes_only)) == 10


# ───────────────────────── through the service ─────────────────────────


async def test_one_jev_call_carries_every_question_and_the_state(harness: Harness) -> None:
    h = harness(first_read_answers())
    await h.service.first_read(resume_input("Jane Doe, product designer."), CTX)

    assert len(h.jev.calls) == 1
    call = h.jev.calls[0]
    assert call.question_ids == ALL_IDS
    assert call.state == {"resume": "Jane Doe, product designer."}
    assert call.timeout_s == 2.5
    wire = call.questions[DEPTH_ID].to_wire()
    assert wire["type"] == "score" and len(wire["criteria"]) == 6
    assert call.questions[CAREER_FAMILY_ID].to_wire()["criteria"]["design_ux"]
    assert h.fallback_calls == []  # fallback is "skip" for the live path


async def test_a_confident_read_becomes_lines_chips_and_facts(harness: Harness) -> None:
    h = harness(first_read_answers())
    result = await h.service.first_read(resume_input(), CTX)

    assert result is not None
    assert result.lines == [
        "First read: Design and UX, early career",
        "Depth 3 of 5: owns real work end to end",
        "Leads or mentors others",
        "States measured results",
        "Shares public work",
    ]
    assert result.signals == ["Leads or mentors others", "Measured results", "Public work"]
    facts = result.facts
    assert facts["career_family"] == "design_ux" and facts["career_stage"] == "early_career"
    assert facts["depth"] == 3 and facts["resume_like"] is True
    assert facts["leads_or_mentors"] is True and facts["ai_experience"] is None
    assert facts["confidence"]["depth"] > 0.5 and "ai_experience" in facts["confidence"]
    assert 3 <= len(result.lines) <= MAX_LINES


async def test_the_decision_log_gets_one_row_per_question(harness: Harness) -> None:
    h = harness(first_read_answers())
    await h.service.first_read(resume_input(), CTX)
    await h.drain()

    rows = {r.spec_id: r for r in h.rows}
    assert set(rows) == set(ALL_IDS)
    assert {r.pack for r in rows.values()} == {"first_read"}
    assert {r.spec_version for r in rows.values()} == {"2"}
    assert rows[DEPTH_ID].mode == "primary" and rows[DEPTH_ID].used is True
    assert rows["first_read.ai_experience"].used is False  # p=0.5: not sure enough to show
    assert h.calls[0].pack == "first_read"


async def test_contact_details_never_reach_jev(harness: Harness) -> None:
    h = harness(first_read_answers())
    text = "Jane Doe. Product designer. Reach me at jane.doe@example.com or +91 98765 43210."
    await h.service.first_read(resume_input(text), CTX)
    sent = h.jev.calls[0].state["resume"]  # type: ignore[index]
    assert "jane.doe@example.com" not in sent and "98765" not in sent
    assert "Product designer" in sent


@pytest.mark.parametrize("confidence_p", [0.75, 0.5, 0.25])
async def test_an_unsure_yes_or_no_shows_nothing(harness: Harness, confidence_p: float) -> None:
    answers = first_read_answers(
        leads_or_mentors=noul_answer(confidence_p),
        measured_outcomes=noul_answer(confidence_p),
        public_proof=noul_answer(confidence_p),
    )
    result = await harness(answers).service.first_read(resume_input(), CTX)
    assert result is not None
    assert result.lines == [
        "First read: Design and UX, early career",
        "Depth 3 of 5: owns real work end to end",
    ]
    assert result.signals == []


async def test_the_bar_is_two_p_minus_one_at_point_six(harness: Harness) -> None:
    just_above = first_read_answers(leads_or_mentors=noul_answer(0.81), public_proof=noul_answer(0.79))
    result = await harness(just_above).service.first_read(resume_input(), CTX)
    assert result is not None
    assert "Leads or mentors others" in result.lines
    assert "Shares public work" not in result.lines


async def test_a_confident_no_is_a_fact_not_a_line(harness: Harness) -> None:
    answers = first_read_answers(leads_or_mentors=noul_answer(0.04))
    result = await harness(answers).service.first_read(resume_input(), CTX)
    assert result is not None
    assert "Leads or mentors others" not in result.lines
    assert result.facts["leads_or_mentors"] is False


async def test_resume_like_no_silences_the_whole_read(harness: Harness) -> None:
    h = harness(first_read_answers(resume_like=noul_answer(0.03)))
    assert await h.service.first_read(resume_input("A recipe for soup."), CTX) is None
    assert len(h.jev.calls) == 1  # it was asked; the answer suppressed everything


async def test_an_unsure_resume_like_does_not_silence_the_read(harness: Harness) -> None:
    result = await harness(first_read_answers(resume_like=noul_answer(0.35))).service.first_read(
        resume_input(), CTX
    )
    assert result is not None and result.lines[0].startswith("First read:")
    assert result.facts["resume_like"] is None


async def test_empty_text_asks_nothing(harness: Harness) -> None:
    h = harness(first_read_answers())
    assert await h.service.first_read(FirstReadInput("", None, TODAY), CTX) is None
    assert await h.service.first_read(FirstReadInput(None, "   ", TODAY), CTX) is None
    assert await h.service.first_read(FirstReadInput("  \n ", "", TODAY), CTX) is None  # a scanned resume
    assert h.jev.calls == []


async def test_notes_only_input_skips_the_resume_gate_and_sends_the_notes(harness: Harness) -> None:
    h = harness(first_read_answers(resume_like=noul_answer(0.01)))  # would silence a resume
    result = await h.service.first_read(FirstReadInput(None, "I teach maths at a school.", TODAY), CTX)

    assert result is not None and result.lines[0].startswith("First read:")
    call = h.jev.calls[0]
    assert RESUME_LIKE_ID not in call.question_ids
    assert call.state == {"resume": "", "notes": "I teach maths at a school."}


async def test_the_resume_and_notes_go_together(harness: Harness) -> None:
    h = harness(first_read_answers())
    await h.service.first_read(FirstReadInput("Resume text", "Some notes", TODAY), CTX)
    assert h.jev.calls[0].state == {"resume": "Resume text", "notes": "Some notes"}


async def test_the_state_does_not_carry_the_date(harness: Harness) -> None:
    h = harness(first_read_answers())
    await h.service.first_read(resume_input(), CTX)
    assert "2026" not in str(h.jev.calls[0].state)


# ───────────────────────── the interpretation table ─────────────────────────


@pytest.mark.parametrize(
    ("family", "stage", "expected_first"),
    [
        ("software_engineering", "senior_or_lead", "First read: Software engineering, senior or lead"),
        ("healthcare_clinical", "student_or_new_grad", "First read: Healthcare, student or new grad"),
        ("trades_skilled_services", "established", "First read: Skilled trades and services, established"),
        ("data_ai", "career_changer", "First read: Data and AI, career changer"),
        (
            "legal_compliance",
            "executive_or_founder",
            "First read: Legal and compliance, executive or founder",
        ),
        ("other_or_unclear", "early_career", "First read: early career"),
        ("design_ux", "unclear", "First read: Design and UX"),
    ],
)
async def test_the_first_line_names_the_field_and_the_stage(
    harness: Harness, family: str, stage: str, expected_first: str
) -> None:
    answers = first_read_answers(
        career_family=choice_answer(family, options=FAMILY_LABELS, p=0.92),
        career_stage=choice_answer(stage, options=STAGE_LABELS, p=0.92),
    )
    result = await harness(answers).service.first_read(resume_input(), CTX)
    assert result is not None
    assert result.lines[0] == expected_first


async def test_no_first_line_when_field_and_stage_are_both_unclear(harness: Harness) -> None:
    answers = first_read_answers(
        career_family=choice_answer("other_or_unclear", options=FAMILY_LABELS, p=0.92),
        career_stage=choice_answer("unclear", options=STAGE_LABELS, p=0.92),
    )
    result = await harness(answers).service.first_read(resume_input(), CTX)
    assert result is not None
    assert not any(line.startswith("First read") for line in result.lines)
    assert result.lines[0].startswith("Depth 3")


@pytest.mark.parametrize(
    ("level", "line"),
    [
        (1, "Depth 1 of 5: still learning the basics"),
        (2, "Depth 2 of 5: does real work with guidance"),
        (3, "Depth 3 of 5: owns real work end to end"),
        (4, "Depth 4 of 5: owns significant scope with results"),
        (5, "Depth 5 of 5: recognised authority or organisation-wide scope"),
    ],
)
async def test_the_depth_line_describes_the_situation(harness: Harness, level: int, line: str) -> None:
    answers = first_read_answers(depth=score_answer(level, n_levels=6, p=0.9))
    result = await harness(answers).service.first_read(resume_input(), CTX)
    assert result is not None and line in result.lines
    assert result.facts["depth"] == level


async def test_depth_zero_or_unsure_has_no_line(harness: Harness) -> None:
    zero = await harness(first_read_answers(depth=score_answer(0, n_levels=6, p=0.9))).service.first_read(
        resume_input(), CTX
    )
    assert zero is not None and not any(line.startswith("Depth") for line in zero.lines)
    assert zero.facts["depth"] == 0

    unsure = await harness(
        first_read_answers(depth=score_answer([1 / 6] * 6, n_levels=6))
    ).service.first_read(resume_input(), CTX)
    assert unsure is not None and not any(line.startswith("Depth") for line in unsure.lines)
    assert unsure.facts["depth"] is None


async def test_at_most_five_lines_and_every_chip_fits(harness: Harness) -> None:
    everything = {flag_id.removeprefix("first_read."): noul_answer(0.97) for flag_id in FLAG_IDS}
    result = await harness(first_read_answers(**everything)).service.first_read(resume_input(), CTX)

    assert result is not None
    assert len(result.lines) == MAX_LINES
    assert result.lines[2:] == ["Leads or mentors others", "States measured results", "Promoted over time"]
    assert len(result.signals) == 7  # every confident yes is a chip, even when its line did not fit
    assert all(0 < len(s) <= SIGNAL_MAX_CHARS for s in result.signals)
    assert all(len(line) <= 60 for line in result.lines)


async def test_nothing_confident_means_no_read(harness: Harness) -> None:
    h = harness({})  # FakeJev answers every question with an uninformative answer
    assert await h.service.first_read(resume_input(), CTX) is None
    assert h.fallback_calls == []  # fallback "skip": a Haiku call would blow the 1 to 2 s budget


async def test_every_chip_label_fits_the_limit() -> None:
    assert all(0 < len(f.signal) <= SIGNAL_MAX_CHARS for f in FLAGS)
    assert all(0 < len(f.line) <= 60 for f in FLAGS)


# ───────────────────────── failure ─────────────────────────


@pytest.mark.parametrize("kind", ["timeout", "connection", "server", "rate_limit", "auth", "bad_request"])
async def test_a_jev_error_means_no_first_read_and_no_exception(harness: Harness, kind: str) -> None:
    h = harness(first_read_answers())
    h.jev.fail_next(JevError(kind))  # type: ignore[arg-type]
    assert await h.service.first_read(resume_input(), CTX) is None
    assert h.fallback_calls == []


async def test_the_overall_budget_cuts_a_slow_jev_off(harness: Harness) -> None:
    h = harness(first_read_answers(), slow_jev_s=5.0)
    h.service._first_read_budget_s = 0.05
    assert await h.service.first_read(resume_input(), CTX) is None


async def test_a_disabled_jev_gives_no_read(harness: Harness) -> None:
    h = harness(first_read_answers(), jev_enabled=False)
    assert await h.service.first_read(resume_input(), CTX) is None
    assert h.jev.calls == []
