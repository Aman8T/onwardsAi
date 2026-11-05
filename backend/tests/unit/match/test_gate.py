"""Gates: severity is withheld from the model, verdicts are validated, and only code decides what blocks."""

from __future__ import annotations

from datetime import date

import pytest

from tayyari.contract.match import Fact, FactRef, Gate, GateAnswer
from tayyari.llm.errors import ClaudeOverloaded
from tayyari.match.gate import answer_gates, decide, validate_answers
from tayyari.match.schemas import GateAnswerOut
from tayyari.prompts.match import YEARS_RULE
from tests.fakes.claude import FakeClaudeClient
from tests.unit.match.support import CTX, HAIKU


def gate(gate_id: str, severity: int, question: str = "Are you authorised to work in India?") -> Gate:
    return Gate(id=gate_id, question=question, quote="Must be authorised to work in India", severity=severity)


def fact(fact_id: str, text: str = "Based in Pune, India") -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        kind="experience",
        refs=[FactRef(source="resume", docId="resume", lines=[1, 1], quote=text)],
    )


def answer(gate_id: str, verdict: str) -> GateAnswer:
    return GateAnswer(gateId=gate_id, factIds=["f1"], verdict=verdict, note="")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("verdict", "severity", "blocked"),
    [
        ("fail", 10, True),
        ("fail", 9, True),
        ("fail", 7, True),  # the boundary blocks
        ("fail", 6, False),  # a failed but minor condition does not
        ("fail", 1, False),
        ("uncertain", 10, False),  # not evidenced never blocks
        ("uncertain", 7, False),
        ("pass", 10, False),
    ],
)
def test_only_a_fail_with_severity_seven_or_more_blocks(verdict: str, severity: int, blocked: bool) -> None:
    decision = decide([gate("g1", severity)], [answer("g1", verdict)])
    assert decision.blocked is blocked
    assert decision.blocking == (("g1",) if blocked else ())


def test_several_gates_block_when_any_blocking_one_fails() -> None:
    gates = [gate("g1", 9), gate("g2", 5), gate("g3", 8)]
    decision = decide(gates, [answer("g1", "pass"), answer("g2", "fail"), answer("g3", "fail")])
    assert decision.blocked
    assert decision.blocking == ("g3",)
    assert decision.uncertain == ()


def row(gate_id: str, verdict: str, fact_ids: list[str]) -> GateAnswerOut:
    return GateAnswerOut(gateId=gate_id, factIds=fact_ids, verdict=verdict, note="n")  # type: ignore[arg-type]


def test_a_pass_or_fail_needs_a_real_cited_fact_or_it_becomes_uncertain() -> None:
    gates = [gate("g1", 9), gate("g2", 9), gate("g3", 9), gate("g4", 9)]
    rows = [
        row("g1", "pass", []),  # a pass with no evidence
        row("g2", "fail", ["made-up"]),  # a fail citing a fact that does not exist
        row("g3", "fail", ["f1"]),  # a fail citing the fact that contradicts: it stands
        row("g4", "pass", ["f1", "f1", "nope"]),
    ]
    got = validate_answers(gates, rows, {"f1"})
    assert [a.verdict for a in got] == ["uncertain", "uncertain", "fail", "pass"]
    assert got[3].factIds == ["f1"]  # repeats and unknown ids removed
    assert "No cited fact" in got[0].note
    assert decide(gates, got).blocking == ("g3",)


def test_a_skipped_or_repeated_or_unknown_gate_is_handled() -> None:
    gates = [gate("g1", 9), gate("g2", 9)]
    rows = [row("g1", "pass", ["f1"]), row("g1", "fail", ["f1"]), row("g9", "fail", ["f1"])]
    got = validate_answers(gates, rows, {"f1"})
    assert [(a.gateId, a.verdict) for a in got] == [("g1", "pass"), ("g2", "uncertain")]


async def test_severity_and_consequences_are_not_in_the_prompt() -> None:
    gates = [gate("g1", 9), gate("g2", 3, "Do you hold a bachelor's degree?")]
    fake = FakeClaudeClient().script_structured("match.gates", {"answers": []})
    await answer_gates(fake, model=HAIKU, gates=gates, facts=[fact("f1")], call_ctx=CTX)
    call = fake.structured_calls[0]
    seen = (call.system_text + call.user_text).lower()
    for withheld in ("severity", "block", "disqualif", "must be authorised", "9", "consequence"):
        assert withheld not in seen, withheld
    assert "g1: are you authorised to work in india?" in seen
    assert call.ctx.purpose == "match.gates"
    props = call.schema["properties"]["answers"]["items"]["properties"]
    assert list(props).index("factIds") < list(props).index("verdict")
    assert props["gateId"]["enum"] == ["g1", "g2"]
    assert props["factIds"]["items"]["enum"] == ["f1"]


async def test_no_gates_means_no_call_and_nothing_blocked() -> None:
    fake = FakeClaudeClient()
    decision = await answer_gates(fake, model=HAIKU, gates=[], facts=[fact("f1")], call_ctx=CTX)
    assert not decision.blocked
    assert decision.answers == []
    assert fake.structured_calls == []


async def test_a_failed_call_leaves_every_gate_uncertain_and_blocks_nothing() -> None:
    fake = FakeClaudeClient().script_structured("match.gates", ClaudeOverloaded())
    decision = await answer_gates(
        fake, model=HAIKU, gates=[gate("g1", 10), gate("g2", 10)], facts=[fact("f1")], call_ctx=CTX
    )
    assert [a.verdict for a in decision.answers] == ["uncertain", "uncertain"]
    assert not decision.blocked
    assert decision.uncertain == ("g1", "g2")


async def test_the_model_cannot_block_by_itself_a_fail_needs_a_fact_and_severity() -> None:
    gates = [gate("g1", 9), gate("g2", 4)]
    fake = FakeClaudeClient().script_structured(
        "match.gates",
        {
            "answers": [
                {"gateId": "g1", "factIds": [], "verdict": "fail", "note": "Probably not."},
                {"gateId": "g2", "factIds": ["f1"], "verdict": "fail", "note": "Lives elsewhere."},
            ]
        },
    )
    decision = await answer_gates(fake, model=HAIKU, gates=gates, facts=[fact("f1")], call_ctx=CTX)
    assert [a.verdict for a in decision.answers] == ["uncertain", "fail"]
    assert not decision.blocked  # g1 had no evidence, g2 is minor


async def test_the_message_starts_with_today_s_date_and_the_system_says_to_count_years_to_it() -> None:
    gates = [gate("g1", 6, "Do you have at least 3 years of experience?")]
    fake = FakeClaudeClient().script_structured("match.gates", {"answers": []})
    await answer_gates(
        fake, model=HAIKU, gates=gates, facts=[fact("f1")], call_ctx=CTX, today=date(2026, 11, 5)
    )
    call = fake.structured_calls[0]
    assert call.user_text.startswith("Today's date: 2026-11-05.\n\n")
    assert YEARS_RULE in call.system_text
    assert '"present"' in YEARS_RULE and "today" in YEARS_RULE


async def test_without_a_date_the_message_has_no_date_line() -> None:
    fake = FakeClaudeClient().script_structured("match.gates", {"answers": []})
    await answer_gates(fake, model=HAIKU, gates=[gate("g1", 6)], facts=[fact("f1")], call_ctx=CTX)
    assert "Today's date" not in fake.structured_calls[0].user_text
