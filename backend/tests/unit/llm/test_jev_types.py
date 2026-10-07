"""Jev question validation and wire shape, answer normalisation, and the Jev fake.

The wire shapes are the ones in docs.typesafe.ai/api (noul `{"noul": p}`; choice `{"choice", "probabilities",
"confidence"}`; score `{"score", "probabilities": {"0": ...}, "legend", "confidence"}`).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from tayyari.llm.errors import JevError
from tayyari.llm.jev import (
    MAX_CHOICE_OPTIONS,
    JevAnswer,
    JevClient,
    JevQuestion,
    JevResponse,
    choice_confidence,
    normalise_answer,
    score_confidence,
)
from tayyari.llm.models import jev_cost_micro
from tayyari.llm.types import CallContext
from tests.fakes.clock import FakeClock
from tests.fakes.jev import FakeJev, choice_answer, noul_answer, score_answer

CTX = CallContext(purpose="decision_fallback", request_id="rid")

# ───────────────────────── JevQuestion: wire shape ─────────────────────────


def test_noul_wire_shape_omits_missing_parts() -> None:
    assert JevQuestion(kind="noul").to_wire() == {"type": "noul"}
    assert JevQuestion(kind="noul", instructions="Is it a resume?").to_wire() == {
        "type": "noul",
        "instructions": "Is it a resume?",
    }
    full = JevQuestion(
        kind="noul",
        instructions={"question": "Does the person lead others?", "scope": "work history"},
        criteria={"true": "names people led", "false": "individual contributor only"},
    )
    assert full.to_wire() == {
        "type": "noul",
        "instructions": {"question": "Does the person lead others?", "scope": "work history"},
        "criteria": {"true": "names people led", "false": "individual contributor only"},
    }


def test_wire_key_order_is_type_instructions_criteria() -> None:
    wire = JevQuestion.choice("Which field?", {"a": "first", "b": None}).to_wire()
    assert list(wire) == ["type", "instructions", "criteria"]
    assert wire["criteria"] == {"a": "first", "b": None}
    assert json.dumps(wire)  # JSON-serialisable as is


def test_score_wire_shape() -> None:
    wire = JevQuestion.score("How deep?", ["none", "learning", "expert"]).to_wire()
    assert wire == {"type": "score", "instructions": "How deep?", "criteria": ["none", "learning", "expert"]}


def test_to_wire_is_a_deep_copy() -> None:
    question = JevQuestion.choice({"question": "Which?"}, {"a": {"what": "x"}})
    wire = question.to_wire()
    wire["instructions"]["question"] = "changed"
    wire["criteria"]["a"]["what"] = "changed"
    assert question.instructions == {"question": "Which?"}
    assert question.criteria == {"a": {"what": "x"}}


def test_questions_are_immutable() -> None:
    question = JevQuestion(kind="noul", instructions="x")
    with pytest.raises(ValidationError):
        question.kind = "choice"  # type: ignore[misc]


def test_choice_options_may_be_described_by_objects_or_nothing() -> None:
    question = JevQuestion.choice(
        "Pick one",
        {"calm": None, "angry": {"what": "shouting", "not_for": ["mild annoyance"]}, "other": "none fit"},
    )
    assert question.option_labels == ("calm", "angry", "other")
    assert question.level_count == 0


def test_score_level_count() -> None:
    question = JevQuestion.score("How much?", ["a", "b", "c"])
    assert question.level_count == 3
    assert question.option_labels == ()


# ───────────────────────── JevQuestion: validation ─────────────────────────


def test_a_choice_may_have_255_options_but_not_256() -> None:
    assert (
        len(JevQuestion.choice("x", {f"o{i}": None for i in range(MAX_CHOICE_OPTIONS)}).option_labels) == 255
    )
    with pytest.raises(ValidationError, match="255"):
        JevQuestion.choice("x", {f"o{i}": None for i in range(256)})


def test_a_choice_needs_options_and_non_blank_labels() -> None:
    with pytest.raises(ValidationError):
        JevQuestion(kind="choice", instructions="x")
    with pytest.raises(ValidationError):
        JevQuestion(kind="choice", instructions="x", criteria={})
    with pytest.raises(ValidationError):
        JevQuestion(kind="choice", instructions="x", criteria=["a", "b"])
    with pytest.raises(ValidationError, match="blank"):
        JevQuestion.choice("x", {" ": None})
    assert JevQuestion.choice("x", {"only": None}).option_labels == ("only",)  # one option is allowed


@pytest.mark.parametrize("levels", [2, 6, 10])
def test_a_score_may_have_two_to_ten_levels(levels: int) -> None:
    assert JevQuestion.score("x", [f"level {i}" for i in range(levels)]).level_count == levels


@pytest.mark.parametrize("levels", [0, 1, 11, 20])
def test_a_score_outside_two_to_ten_levels_is_rejected(levels: int) -> None:
    with pytest.raises(ValidationError, match="levels"):
        JevQuestion.score("x", [f"level {i}" for i in range(levels)])


def test_a_score_needs_a_list_of_described_levels() -> None:
    with pytest.raises(ValidationError):
        JevQuestion(kind="score", instructions="x")
    with pytest.raises(ValidationError):
        JevQuestion(kind="score", instructions="x", criteria={"0": "a", "1": "b"})
    with pytest.raises(ValidationError, match="blank"):
        JevQuestion.score("x", ["a", ""])
    with pytest.raises(ValidationError, match="blank"):
        JevQuestion.score("x", ["a", {}])


def test_noul_criteria_may_only_describe_true_and_false() -> None:
    assert JevQuestion.noul("x", {"true": "yes means this"}).criteria == {"true": "yes means this"}
    with pytest.raises(ValidationError, match="true"):
        JevQuestion.noul("x", {"maybe": "no"})
    with pytest.raises(ValidationError):
        JevQuestion(kind="noul", instructions="x", criteria=["yes", "no"])


def test_blank_instructions_and_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="blank"):
        JevQuestion(kind="noul", instructions="   ")
    with pytest.raises(ValidationError):
        JevQuestion(kind="maybe")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        JevQuestion(kind="noul", instructions="x", extra_field=1)  # type: ignore[call-arg]


# ───────────────────────── normalise_answer: noul ─────────────────────────


@pytest.mark.parametrize(
    ("p", "confidence"),
    [(1.0, 1.0), (0.0, 1.0), (0.5, 0.0), (0.95, 0.9), (0.05, 0.9), (0.75, 0.5), (0.25, 0.5), (0.8, 0.6)],
)
def test_noul_confidence_is_the_distance_from_one_half(p: float, confidence: float) -> None:
    answer = normalise_answer("noul", {"type": "noul", "noul": p})
    assert answer.kind == "noul"
    assert answer.noul == p
    assert answer.confidence == pytest.approx(confidence)
    assert (answer.choice, answer.score_level, answer.score_expected, answer.probabilities) == (
        None,
        None,
        None,
        None,
    )


def test_noul_probability_is_clipped_into_range() -> None:
    assert normalise_answer("noul", {"noul": 1.2}).noul == 1.0
    assert normalise_answer("noul", {"noul": -0.1}).confidence == 1.0


@pytest.mark.parametrize("raw", [{}, {"noul": None}, {"noul": "0.9"}, {"noul": True}, {"noul": float("nan")}])
def test_a_bad_noul_answer_is_an_invalid_response(raw: dict[str, Any]) -> None:
    with pytest.raises(JevError) as caught:
        normalise_answer("noul", raw)
    assert caught.value.kind == "invalid_response"


# ───────────────────────── normalise_answer: choice ─────────────────────────


def test_choice_takes_the_api_label_and_confidence() -> None:
    raw = {
        "type": "choice",
        "choice": "design_ux",
        "probabilities": {"design_ux": 0.7, "marketing": 0.2, "other": 0.1},
        "confidence": 0.55,
    }
    answer = normalise_answer("choice", raw)
    assert answer.choice == "design_ux"
    assert answer.confidence == 0.55  # the API's value wins
    assert answer.probabilities == {"design_ux": 0.7, "marketing": 0.2, "other": 0.1}
    assert answer.noul is None and answer.score_level is None


def test_choice_without_a_confidence_uses_the_documented_formula() -> None:
    raw = {"choice": "a", "probabilities": {"a": 0.6, "b": 0.3, "c": 0.1}}
    answer = normalise_answer("choice", raw)
    # (0.6 - 1/3) / (1 - 1/3) = 0.4
    assert answer.confidence == pytest.approx(0.4)
    assert choice_confidence({"a": 0.6, "b": 0.3, "c": 0.1}) == pytest.approx(0.4)


@pytest.mark.parametrize(
    ("probabilities", "confidence"),
    [
        ({"a": 0.5, "b": 0.5}, 0.0),  # even split
        ({"a": 1.0, "b": 0.0}, 1.0),  # certainty
        ({"a": 0.9, "b": 0.1}, 0.8),
        ({"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}, 0.0),
        ({"a": 0.625, "b": 0.125, "c": 0.125, "d": 0.125}, 0.5),
        ({"only": 1.0}, 1.0),  # a single option is trivially certain
        ({"a": 0.2, "b": 0.8}, 0.6),
    ],
)
def test_choice_confidence_formula(probabilities: dict[str, float], confidence: float) -> None:
    assert choice_confidence(probabilities) == pytest.approx(confidence)


def test_choice_confidence_of_nothing_is_zero_and_never_negative() -> None:
    assert choice_confidence({}) == 0.0
    assert choice_confidence({"a": 0.2, "b": 0.2, "c": 0.2}) == 0.0  # top below an even split (bad input)


def test_choice_label_falls_back_to_the_argmax_of_the_probabilities() -> None:
    answer = normalise_answer("choice", {"probabilities": {"a": 0.1, "b": 0.7, "c": 0.2}})
    assert answer.choice == "b"
    tie = normalise_answer("choice", {"probabilities": {"x": 0.5, "y": 0.5}})
    assert tie.choice == "x"  # the first declared option wins a tie


def test_choice_without_probabilities_or_confidence_is_never_confident() -> None:
    answer = normalise_answer("choice", {"choice": "a"})
    assert (answer.choice, answer.probabilities, answer.confidence) == ("a", None, 0.0)


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"choice": ""},
        {"choice": 3, "probabilities": None},
        {"choice": "a", "probabilities": {"a": "high"}},
        {"choice": "a", "probabilities": ["a"]},
        {"choice": "a", "confidence": "x"},
    ],
)
def test_a_bad_choice_answer_is_an_invalid_response(raw: dict[str, Any]) -> None:
    with pytest.raises(JevError) as caught:
        normalise_answer("choice", raw)
    assert caught.value.kind == "invalid_response"


# ───────────────────────── normalise_answer: score ─────────────────────────

DOCS_SCORE = {
    "type": "score",
    "score": 1.43,
    "confidence": 0.35,
    "legend": {"0": "Cosmetic", "1": "Workaround exists", "2": "Blocking"},
    "probabilities": {"0": 0.0, "1": 0.57, "2": 0.43},
}


def test_score_level_is_the_argmax_and_expected_is_the_weighted_mean() -> None:
    answer = normalise_answer("score", DOCS_SCORE)
    assert answer.kind == "score"
    assert answer.score_level == 1  # argmax, not round(1.43)
    assert answer.score_expected == 1.43
    assert answer.confidence == 0.35
    assert answer.probabilities == {"0": 0.0, "1": 0.57, "2": 0.43}
    assert answer.noul is None and answer.choice is None


def test_score_argmax_and_expected_can_disagree() -> None:
    # bimodal: the single most likely level is 0, but the mean sits at 1.6
    raw = {"probabilities": {"0": 0.4, "1": 0.0, "2": 0.0, "3": 0.3, "4": 0.3}}
    answer = normalise_answer("score", raw)
    assert answer.score_level == 0
    assert answer.score_expected == pytest.approx(0.3 * 3 + 0.3 * 4)
    assert round(answer.score_expected or 0) == 2  # rounding the mean would give a different level


def test_score_expected_is_computed_when_the_api_omits_it() -> None:
    answer = normalise_answer("score", {"probabilities": {"0": 0.0, "1": 0.57, "2": 0.43}})
    assert answer.score_expected == pytest.approx(1.43)


def test_score_confidence_is_computed_when_missing() -> None:
    answer = normalise_answer("score", {"probabilities": {"0": 0.0, "1": 0.57, "2": 0.43}})
    # 1 - 0.43 / MAD_uniform(3) with MAD = (1 + 0 + 1) / 3 -> 0.355, the docs' example rounds it to 0.35
    assert answer.confidence == pytest.approx(0.355, abs=1e-3)


@pytest.mark.parametrize(
    ("probabilities", "confidence"),
    [
        ({0: 0.0, 1: 1.0, 2: 0.0}, 1.0),  # all mass on the mode
        ({0: 1 / 3, 1: 1 / 3, 2: 1 / 3}, 0.0),  # uniform
        ({0: 0.0, 1: 0.8, 2: 0.2}, 1 - 0.2 / (2 / 3)),
        ({0: 0.5, 1: 0.0, 2: 0.5}, 0.0),  # far apart halves are no better than uniform here
        ({0: 0.5, 1: 0.5, 2: 0.0}, 1 - 0.5 / (2 / 3)),  # a neighbour costs less than a far level
        ({0: 0.9, 1: 0.0, 2: 0.1}, 1 - 0.2 / (2 / 3)),  # the same 0.1 two levels away costs twice as much
        ({0: 0.9, 1: 0.1, 2: 0.0}, 1 - 0.1 / (2 / 3)),
    ],
)
def test_score_confidence_formula(probabilities: dict[int, float], confidence: float) -> None:
    assert score_confidence(probabilities) == pytest.approx(confidence)


def test_score_confidence_never_goes_negative_and_handles_one_level() -> None:
    assert score_confidence({0: 0.5, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.5, 5: 0.0}) == 0.0
    assert score_confidence({0: 1.0}) == 1.0
    assert score_confidence({}) == 0.0


def test_a_score_tie_goes_to_the_lower_level() -> None:
    answer = normalise_answer("score", {"probabilities": {"0": 0.1, "1": 0.4, "2": 0.4, "3": 0.1}})
    assert answer.score_level == 1


def test_a_score_with_list_probabilities_is_accepted() -> None:
    answer = normalise_answer("score", {"probabilities": [0.1, 0.2, 0.7]})
    assert answer.score_level == 2
    assert answer.probabilities == {"0": 0.1, "1": 0.2, "2": 0.7}


def test_score_probabilities_are_ordered_by_level() -> None:
    answer = normalise_answer("score", {"probabilities": {"10": 0.1, "2": 0.5, "0": 0.4}})
    assert answer.probabilities is not None and list(answer.probabilities) == ["0", "2", "10"]


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"score": 1.0},
        {"probabilities": {}},
        {"probabilities": {"high": 0.5, "low": 0.5}},
        {"probabilities": {"-1": 0.5, "0": 0.5}},
        {"probabilities": {"0": 0.5}, "score": "x"},
    ],
)
def test_a_bad_score_answer_is_an_invalid_response(raw: dict[str, Any]) -> None:
    with pytest.raises(JevError) as caught:
        normalise_answer("score", raw)
    assert caught.value.kind == "invalid_response"


# ───────────────────────── normalise_answer: general ─────────────────────────


def test_an_answer_of_the_wrong_type_is_an_invalid_response() -> None:
    with pytest.raises(JevError, match="expected a noul"):
        normalise_answer("noul", {"type": "choice", "choice": "a"})
    with pytest.raises(JevError):
        normalise_answer("noul", "0.9")  # type: ignore[arg-type]


def test_probabilities_are_clipped_and_answers_are_immutable() -> None:
    answer = normalise_answer(
        "choice", {"choice": "a", "probabilities": {"a": 1.3, "b": -0.2}, "confidence": 4}
    )
    assert answer.probabilities == {"a": 1.0, "b": 0.0}
    assert answer.confidence == 1.0
    with pytest.raises(ValidationError):
        answer.confidence = 0.0  # type: ignore[misc]


def test_a_normalised_answer_round_trips_through_json() -> None:
    answer = normalise_answer("score", DOCS_SCORE)
    assert JevAnswer.model_validate(json.loads(answer.model_dump_json())) == answer


# ───────────────────────── FakeJev ─────────────────────────

QUESTIONS = {
    "career_family": JevQuestion.choice(
        "Which field?", {"design_ux": "design", "marketing": "marketing", "other": None}
    ),
    "depth": JevQuestion.score("How deep?", [f"level {i}" for i in range(6)]),
    "public_proof": JevQuestion.noul("Has public work?"),
}
STATE = {"resume": "Designed things.", "notes": None}


def test_the_fake_satisfies_the_protocol() -> None:
    client: JevClient = FakeJev()
    assert client.model_id == "jev-1.13.0"


async def test_fake_jev_normalises_scripted_raw_answers() -> None:
    jev = FakeJev(
        {
            "career_family": choice_answer("design_ux", options=["design_ux", "marketing", "other"], p=0.8),
            "depth": score_answer(3, n_levels=6, p=0.7),
            "public_proof": noul_answer(0.95),
        }
    )
    response = await jev.system_one(STATE, QUESTIONS, timeout_s=2.5, ctx=CTX)
    assert isinstance(response, JevResponse)
    family, depth, proof = (response.answers[k] for k in QUESTIONS)
    assert (family.choice, round(family.confidence, 3)) == (
        "design_ux",
        round(choice_confidence(family.probabilities or {}), 3),
    )
    assert depth.score_level == 3 and depth.score_expected is not None
    assert proof.noul == 0.95 and proof.confidence == pytest.approx(0.9)
    assert response.model == "jev-1.13.0"


async def test_fake_jev_builds_a_replayable_request_and_a_priced_response() -> None:
    jev = FakeJev({"public_proof": noul_answer(0.9)}, input_tokens=10_000)
    response = await jev.system_one(
        STATE, {"public_proof": QUESTIONS["public_proof"]}, timeout_s=2.5, ctx=CTX
    )
    assert response.request_json == {
        "state": STATE,
        "model": "jev-1.13.0",
        "questions": {"public_proof": {"type": "noul", "instructions": "Has public work?"}},
    }
    assert response.response_json["answers"]["public_proof"] == {"type": "noul", "noul": 0.9}
    assert response.response_json["usage"] == {"input_tokens": 10_000, "output_tokens": 8}
    assert (response.input_tokens, response.output_tokens) == (10_000, 8)
    assert response.cost_micro_usd == jev_cost_micro("jev-1.13.0", 10_000) == 420
    assert response.request_id == "fake-jev-1"


async def test_fake_jev_records_requests_and_the_model_override() -> None:
    jev = FakeJev()
    await jev.system_one(STATE, QUESTIONS, timeout_s=4.0, ctx=CTX)
    response = await jev.system_one(
        "plain state", {"public_proof": QUESTIONS["public_proof"]}, model="jev-1.14.0", timeout_s=1.0, ctx=CTX
    )
    first, second = jev.calls
    assert first.question_ids == ["career_family", "depth", "public_proof"]
    assert (first.state, first.model, first.timeout_s, first.ctx) == (STATE, "jev-1.13.0", 4.0, CTX)
    assert (second.state, second.model) == ("plain state", "jev-1.14.0")
    assert response.model == "jev-1.14.0"
    assert response.request_json["model"] == "jev-1.14.0"


async def test_unscripted_questions_get_uninformative_answers() -> None:
    response = await FakeJev().system_one(STATE, QUESTIONS, timeout_s=2.5, ctx=CTX)
    assert response.answers["public_proof"].noul == 0.5
    assert response.answers["public_proof"].confidence == 0.0
    assert response.answers["career_family"].confidence == 0.0
    assert response.answers["depth"].confidence == pytest.approx(0.0)
    assert response.answers["depth"].score_expected == pytest.approx(2.5)


async def test_strict_fake_jev_refuses_unscripted_questions() -> None:
    with pytest.raises(AssertionError, match="depth"):
        await FakeJev({"public_proof": noul_answer(0.9)}, strict=True).system_one(
            STATE, {"depth": QUESTIONS["depth"]}, timeout_s=2.5, ctx=CTX
        )


async def test_indexed_question_ids_fall_back_to_the_base_script_entry() -> None:
    questions = {f"skill_level#{name}": QUESTIONS["depth"] for name in ("python", "figma")}
    jev = FakeJev({"skill_level": score_answer(4), "skill_level#figma": score_answer(1)})
    response = await jev.system_one(STATE, questions, timeout_s=2.5, ctx=CTX)
    assert response.answers["skill_level#python"].score_level == 4
    assert response.answers["skill_level#figma"].score_level == 1  # the exact id wins


async def test_the_script_can_be_a_callable_of_state_and_questions() -> None:
    def script(state: Any, questions: Any) -> dict[str, Any]:
        return {qid: noul_answer(0.99 if "design" in state["resume"].lower() else 0.01) for qid in questions}

    jev = FakeJev(script)
    questions = {"a": JevQuestion.noul("x"), "b": JevQuestion.noul("y")}
    assert (await jev.system_one({"resume": "Design lead"}, questions, timeout_s=1, ctx=CTX)).answers[
        "a"
    ].noul == 0.99
    assert (await jev.system_one({"resume": "Welder"}, questions, timeout_s=1, ctx=CTX)).answers[
        "b"
    ].noul == 0.01


async def test_set_answer_and_script_replace_entries() -> None:
    jev = FakeJev({"public_proof": noul_answer(0.1)})
    jev.set_answer("public_proof", noul_answer(0.9))
    question = {"public_proof": QUESTIONS["public_proof"]}
    assert (await jev.system_one(STATE, question, timeout_s=1, ctx=CTX)).answers["public_proof"].noul == 0.9
    jev.script({"public_proof": noul_answer(0.3)})
    assert (await jev.system_one(STATE, question, timeout_s=1, ctx=CTX)).answers["public_proof"].noul == 0.3


async def test_a_malformed_scripted_answer_fails_like_a_bad_api_response() -> None:
    jev = FakeJev({"public_proof": {"noul": "very likely"}})
    with pytest.raises(JevError) as caught:
        await jev.system_one(STATE, {"public_proof": QUESTIONS["public_proof"]}, timeout_s=1, ctx=CTX)
    assert caught.value.kind == "invalid_response"


async def test_fail_next_raises_then_recovers_and_still_records() -> None:
    jev = FakeJev({"public_proof": noul_answer(0.9)})
    jev.fail_next(JevError("rate_limit", retry_after_s=2.0), times=2)
    question = {"public_proof": QUESTIONS["public_proof"]}
    for _ in range(2):
        with pytest.raises(JevError) as caught:
            await jev.system_one(STATE, question, timeout_s=1, ctx=CTX)
        assert caught.value.kind == "rate_limit"
    assert (await jev.system_one(STATE, question, timeout_s=1, ctx=CTX)).answers["public_proof"].noul == 0.9
    assert len(jev.calls) == 3


async def test_fail_with_fails_every_call_until_cleared() -> None:
    jev = FakeJev().fail_with(JevError("server", status=503))
    question = {"public_proof": QUESTIONS["public_proof"]}
    for _ in range(3):
        with pytest.raises(JevError):
            await jev.system_one(STATE, question, timeout_s=1, ctx=CTX)
    jev.fail_with(None)
    assert await jev.system_one(STATE, question, timeout_s=1, ctx=CTX)


async def test_delay_runs_on_the_clock_and_counts_towards_latency() -> None:
    clock = FakeClock()
    jev = FakeJev({"public_proof": noul_answer(0.9)}, clock=clock, delay_s=1.2)
    started = clock.monotonic()
    response = await jev.system_one(
        STATE, {"public_proof": QUESTIONS["public_proof"]}, timeout_s=2.5, ctx=CTX
    )
    assert clock.monotonic() - started == pytest.approx(1.2)
    assert response.latency_ms == 1200


async def test_a_delay_beyond_the_timeout_times_out_at_the_timeout() -> None:
    clock = FakeClock()
    jev = FakeJev(clock=clock).set_delay(10.0)
    started = clock.monotonic()
    with pytest.raises(JevError) as caught:
        await jev.system_one(STATE, {"public_proof": QUESTIONS["public_proof"]}, timeout_s=2.5, ctx=CTX)
    assert caught.value.kind == "timeout"
    assert clock.monotonic() - started == pytest.approx(2.5)


async def test_aclose_is_recorded() -> None:
    jev = FakeJev()
    assert not jev.closed
    await jev.aclose()
    assert jev.closed
