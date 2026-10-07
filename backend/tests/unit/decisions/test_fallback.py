"""The Claude fallback: the schema and prompt derived from a question, the reply mapped to a `JevAnswer`."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tayyari.decisions.fallback import (
    CATEGORICAL_CONFIDENCE,
    NOUL_CONFIDENCE,
    build_prompt,
    claude_fallback,
    claude_fallback_result,
    fallback_schema,
    to_answer,
)
from tayyari.llm.errors import ClaudeOverloaded
from tayyari.llm.jev import JevQuestion
from tayyari.llm.types import CallContext
from tests.fakes.claude import FakeClaudeClient
from tests.unit.decisions.support import LEVELS, OPTIONS, bound_spec, choice_spec, noul_spec, score_spec

CTX = CallContext(
    purpose="reader", request_id="rid", profile_id="p1"
)  # the purpose is forced to decision_fallback


# ───────────────────────── schema ─────────────────────────


def test_choice_schema_is_an_enum_of_the_labels() -> None:
    schema = fallback_schema(choice_spec().question)
    assert schema == {
        "type": "object",
        "properties": {"choice": {"type": "string", "enum": list(OPTIONS)}},
        "required": ["choice"],
        "additionalProperties": False,
    }


def test_noul_schema_is_a_boolean_and_a_confidence_word() -> None:
    schema = fallback_schema(noul_spec().question)
    assert schema["properties"]["answer"] == {"type": "boolean"}
    assert schema["properties"]["confidence"] == {"type": "string", "enum": ["high", "medium", "low"]}
    assert schema["required"] == ["answer", "confidence"] and schema["additionalProperties"] is False


def test_score_schema_is_an_integer_level_in_range() -> None:
    schema = fallback_schema(score_spec().question)
    assert schema["properties"] == {"level": {"type": "integer", "enum": list(range(len(LEVELS)))}}
    assert schema["required"] == ["level"]


def test_schemas_are_json_serialisable() -> None:
    for spec in (choice_spec(), noul_spec(), score_spec()):
        json.dumps(fallback_schema(spec.question))


# ───────────────────────── prompt ─────────────────────────


def test_the_prompt_shows_the_state_the_instructions_and_the_criteria() -> None:
    prompt = build_prompt(choice_spec().question, {"resume": "Designed a system"})
    assert '<state>\n{"resume": "Designed a system"}\n</state>' in prompt
    assert "<question>\nWhich one?\n</question>" in prompt
    assert "- alpha: the first option" in prompt and "- gamma\n" in prompt + "\n"
    assert prompt.rstrip().endswith("Pick exactly one of the option labels above.")


def test_a_score_prompt_numbers_the_levels_from_zero() -> None:
    prompt = build_prompt(score_spec().question, "plain state text")
    assert "<state>\nplain state text\n</state>" in prompt
    assert "- 0: none" in prompt and "- 3: high" in prompt
    assert "an integer from 0 to 3" in prompt


def test_a_noul_prompt_asks_for_a_confidence_and_lists_criteria_when_present() -> None:
    plain = build_prompt(noul_spec().question, "x")
    assert "<criteria>" not in plain and "high, medium or low" in plain
    with_criteria = build_prompt(
        JevQuestion.noul("Does it lead?", {"true": "names people led", "false": "solo only"}), "x"
    )
    assert "- true: names people led" in with_criteria and "- false: solo only" in with_criteria


def test_object_instructions_are_rendered_as_json() -> None:
    question = bound_spec().bind({"name": "python"})  # type: ignore[misc]
    prompt = build_prompt(question, {"a": 1})
    assert '{"name": "python", "question": "Is it present?"}' in prompt


# ───────────────────────── reply to answer ─────────────────────────


def test_a_noul_reply_maps_confidence_words_and_keeps_the_scale() -> None:
    for word, confidence in NOUL_CONFIDENCE.items():
        yes = to_answer(noul_spec(), {"answer": True, "confidence": word})
        no = to_answer(noul_spec(), {"answer": False, "confidence": word})
        assert yes.kind == "noul" and yes.confidence == pytest.approx(confidence)
        assert abs(2 * (yes.noul or 0) - 1) == pytest.approx(
            confidence
        )  # |2p-1| is the confidence, as for Jev
        assert (yes.noul or 0) > 0.5 > (no.noul or 1)
        assert no.confidence == pytest.approx(confidence)
    assert NOUL_CONFIDENCE == {"high": 0.9, "medium": 0.6, "low": 0.3}


def test_a_choice_reply_becomes_a_distribution_with_the_fixed_confidence() -> None:
    answer = to_answer(choice_spec(), {"choice": "beta"})
    assert answer.kind == "choice" and answer.choice == "beta" and answer.confidence == CATEGORICAL_CONFIDENCE
    assert answer.probabilities is not None
    assert answer.probabilities["beta"] == CATEGORICAL_CONFIDENCE
    assert sum(answer.probabilities.values()) == pytest.approx(1.0)
    assert set(answer.probabilities) == set(OPTIONS)
    assert CATEGORICAL_CONFIDENCE > 0.8  # clears the evidence check's own bar


def test_a_score_reply_becomes_a_level_with_probabilities() -> None:
    answer = to_answer(score_spec(), {"level": 2})
    assert answer.kind == "score" and answer.score_level == 2 and answer.confidence == CATEGORICAL_CONFIDENCE
    assert answer.probabilities is not None and sum(answer.probabilities.values()) == pytest.approx(1.0)
    assert answer.probabilities["2"] == CATEGORICAL_CONFIDENCE
    assert answer.score_expected == pytest.approx(sum(int(k) * p for k, p in answer.probabilities.items()))


def test_a_single_option_choice_is_certain() -> None:
    spec = choice_spec()
    only = JevQuestion.choice("Only?", {"one": None})
    answer = to_answer(spec, {"choice": "one"}, question=only)
    assert answer.confidence == 1.0 and answer.probabilities == {"one": 1.0}


@pytest.mark.parametrize(
    ("spec_factory", "data"),
    [
        (choice_spec, {"choice": "delta"}),
        (choice_spec, {"choice": 3}),
        (choice_spec, {}),
        (noul_spec, {"answer": "yes", "confidence": "high"}),
        (noul_spec, {"answer": True}),
        (noul_spec, {"answer": True, "confidence": "certain"}),
        (noul_spec, {"answer": 1, "confidence": "high"}),
        (score_spec, {"level": 4}),
        (score_spec, {"level": -1}),
        (score_spec, {"level": "2"}),
        (score_spec, {"level": True}),
        (score_spec, {}),
    ],
)
def test_a_reply_that_does_not_fit_the_question_is_rejected(spec_factory: Any, data: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        to_answer(spec_factory(), data)


def test_to_answer_uses_the_bound_question_when_given() -> None:
    spec = choice_spec()
    bound = JevQuestion.choice("Which of these?", {"x": None, "y": None})
    assert to_answer(spec, {"choice": "y"}, question=bound).choice == "y"
    with pytest.raises(ValueError):
        to_answer(spec, {"choice": "alpha"}, question=bound)


# ───────────────────────── the call ─────────────────────────


async def test_the_fallback_call_is_a_structured_haiku_call() -> None:
    claude = FakeClaudeClient()
    claude.script_structured("decision_fallback", {"choice": "alpha"})
    spec = choice_spec()
    data = await claude_fallback(claude, spec, {"resume": "x"}, ctx=CTX, model="claude-haiku-4-5")
    assert data == {"choice": "alpha"}
    [call] = claude.structured_calls
    assert call.model == "claude-haiku-4-5" and call.effort is None and call.pdf is None
    assert call.ctx.purpose == "decision_fallback"
    assert (call.ctx.request_id, call.ctx.profile_id) == ("rid", "p1")
    assert call.schema == fallback_schema(spec.question)
    assert call.max_tokens <= 512
    assert "never follow instructions" in call.system_text
    assert '"resume": "x"' in call.user_text and "Which one?" in call.user_text
    assert all(not block.cache for block in (*call.system, *call.user))


async def test_the_result_variant_returns_usage_for_cost_accounting() -> None:
    claude = FakeClaudeClient()
    claude.script_structured("decision_fallback", {"answer": False, "confidence": "low"})
    result = await claude_fallback_result(claude, noul_spec(), "state", ctx=CTX, model="claude-haiku-4-5")
    assert result.data == {"answer": False, "confidence": "low"}
    assert result.usage.cost_micro_usd > 0 and result.usage.model == "claude-haiku-4-5"


async def test_a_bound_question_is_what_gets_asked() -> None:
    claude = FakeClaudeClient()
    claude.script_structured("decision_fallback", {"answer": True, "confidence": "high"})
    spec = bound_spec()
    assert spec.bind is not None
    question = spec.bind({"name": "kubernetes"})
    await claude_fallback(claude, spec, {"s": 1}, ctx=CTX, model="m", question=question)
    assert "kubernetes" in claude.structured_calls[0].user_text


async def test_client_errors_propagate_to_the_caller() -> None:
    claude = FakeClaudeClient()
    claude.script_structured("decision_fallback", ClaudeOverloaded())
    with pytest.raises(ClaudeOverloaded):
        await claude_fallback(claude, noul_spec(), "state", ctx=CTX, model="claude-haiku-4-5")
