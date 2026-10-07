"""The helpers the packs share: state cuts, answer readers and the "may I act on this" checks."""

from __future__ import annotations

from tayyari.decisions.packs.common import (
    acted_on,
    choice_label,
    confidence_of,
    head,
    noul_criteria,
    noul_probability,
    noul_yes,
    score_level,
    tail,
    used_value,
)
from tayyari.decisions.spec import DecisionResult, PackResult
from tayyari.llm.jev import normalise_answer
from tests.fakes.jev import choice_answer, noul_answer, score_answer


def result(
    value: object, *, used: bool = True, confidence: float | None = 0.9, key: str = "x.y"
) -> DecisionResult[object]:
    return DecisionResult(
        spec_id=key,
        spec_version="1",
        mode="primary",
        value=value,
        used=used,
        source="jev",
        confidence=confidence,
        threshold=0.6,
        escalated=False,
        escalation_reason=None,
        jev=None,
        claude=None,
        latency_ms=0,
        cost_micro_usd=0,
        log_id=None,
        key=key,
    )


def test_head_and_tail_cut_plainly() -> None:
    assert head("  abcdef  ", 3) == "abc"
    assert tail("  abcdef  ", 3) == "def"
    assert head("abc", 10) == "abc" and tail("abc", 10) == "abc"
    assert head(None, 5) == "" and tail(None, 5) == "" and head("", 5) == ""
    assert head("abc", 0) == "" and tail("abc", 0) == "" and head("abc", -1) == ""


def test_answers_are_read_by_kind() -> None:
    noul = normalise_answer("noul", noul_answer(0.9))
    assert noul_yes(noul) is True and noul_probability(noul) == 0.9
    assert noul_yes(normalise_answer("noul", noul_answer(0.5))) is True  # the argmax at exactly one half
    assert noul_yes(normalise_answer("noul", noul_answer(0.49))) is False
    assert noul_yes(normalise_answer("noul", noul_answer(0.1))) is False

    choice = normalise_answer("choice", choice_answer("b", options=["a", "b"], p=0.9))
    assert choice_label(choice) == "b" and noul_yes(choice) is None and score_level(choice) is None

    score = normalise_answer("score", score_answer(4, n_levels=6))
    assert score_level(score) == 4 and choice_label(score) is None


def test_noul_criteria_has_the_two_keys() -> None:
    assert noul_criteria("yes text", "no text") == {"true": "yes text", "false": "no text"}


def test_acted_on_needs_use_a_value_and_the_pack_s_own_bar() -> None:
    assert acted_on(result("v"))
    assert not acted_on(None)
    assert not acted_on(result("v", used=False))
    assert not acted_on(result(None))
    assert acted_on(result(False))  # False is a value
    assert acted_on(result(0))
    assert acted_on(result("v", confidence=0.65), min_confidence=0.6)
    assert not acted_on(result("v", confidence=0.55), min_confidence=0.6)
    assert not acted_on(result("v", confidence=None), min_confidence=0.6)
    assert acted_on(result("v", confidence=None))  # no bar asked for


def test_used_value_and_confidence_look_results_up_by_key() -> None:
    results = PackResult(
        "p", {"a": result("va", key="a", confidence=0.7), "b": result("vb", key="b", used=False)}
    )
    assert used_value(results, "a") == "va"
    assert used_value(results, "a", min_confidence=0.8) is None
    assert used_value(results, "b") is None
    assert used_value(results, "missing") is None
    assert confidence_of(results, "a") == 0.7
    assert confidence_of(results, "b") == 0.9
    assert confidence_of(results, "missing") is None
