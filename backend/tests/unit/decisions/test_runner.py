"""The decision runner matrix: modes (off, shadow, primary) x confidence (above, below) x Jev error x open circuit x
spend trip x kill switch x fallback (skip, claude), plus the guarantee that a failure never raises out of ``run``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from tayyari.decisions.spec import PackResult
from tayyari.llm.errors import ClaudeOverloaded, JevError
from tests.fakes.jev import noul_answer
from tests.unit.decisions.support import (
    CTX,
    SpendTrip,
    build_harness,
    choice_spec,
    make_pack,
    noul_spec,
    score_spec,
)

SPEC_ID = "t.flag"
CLAUDE_YES = {"answer": True, "confidence": "high"}
ABOVE = noul_answer(0.95)  # confidence 0.9, value True
BELOW = noul_answer(0.45)  # confidence 0.1, value False


async def decide(h: Any, spec: Any = None, text: str = "some text", **kwargs: Any) -> Any:
    pack = make_pack(spec if spec is not None else noul_spec())
    result: PackResult = await h.runner.run(pack, text, ctx=CTX, **kwargs)
    return result[SPEC_ID]


# ───────────────────────── off ─────────────────────────


async def test_off_asks_nothing_and_returns_the_neutral_value() -> None:
    h = build_harness({SPEC_ID: ABOVE})
    result = await decide(h, noul_spec(mode="off", neutral=True))
    assert h.fake_jev.calls == [] and h.fallback_calls == []
    assert (result.mode, result.used, result.source, result.value) == ("off", False, "default", True)
    assert result.escalated is False and result.escalation_reason is None
    assert result.jev is None and result.claude is None and result.log_id is None
    await h.drain()
    assert h.rows == [] and h.calls == []


async def test_an_override_can_switch_a_spec_off() -> None:
    h = build_harness({SPEC_ID: ABOVE}, overrides={SPEC_ID: {"mode": "off"}})
    result = await decide(h)
    assert h.fake_jev.calls == []
    assert result.mode == "off" and not result.used


# ───────────────────────── shadow ─────────────────────────


@pytest.mark.parametrize("answer", [ABOVE, BELOW], ids=["confident", "unsure"])
async def test_shadow_logs_the_answer_next_to_the_incumbent_and_never_uses_it(answer: dict[str, Any]) -> None:
    h = build_harness({SPEC_ID: answer})
    result = await decide(h, noul_spec(mode="shadow", threshold=0.0), incumbent={SPEC_ID: {"score": 58}})
    assert result.mode == "shadow" and result.used is False and result.source == "jev"
    assert result.value == (answer is ABOVE)
    assert result.escalated is False and result.claude is None
    assert h.fallback_calls == []
    await h.drain()
    [row] = h.rows
    assert row.mode == "shadow" and row.used is False and row.source_used == "jev"
    assert row.incumbent == {"score": 58}
    assert row.id == result.log_id


async def test_a_scalar_incumbent_is_wrapped_and_a_missing_one_is_none() -> None:
    h = build_harness({SPEC_ID: ABOVE})
    await decide(h, noul_spec(mode="shadow"), incumbent={SPEC_ID: 58})
    await decide(h, noul_spec(mode="shadow"))
    await h.drain()
    incumbents = sorted(str(r.incumbent) for r in h.rows)
    assert incumbents == ["None", "{'value': 58}"]


async def test_shadow_with_a_jev_error_is_neutral_and_logged_as_an_error() -> None:
    h = build_harness({SPEC_ID: ABOVE})
    h.fake_jev.fail_next(JevError("timeout"))
    result = await decide(h, noul_spec(mode="shadow", neutral=False))
    assert (result.mode, result.used, result.source, result.value) == ("shadow", False, "default", False)
    assert result.escalation_reason == "jev_error"
    assert h.fallback_calls == []  # a shadow decision never reaches Claude
    await h.drain()
    [call] = h.calls
    assert call.status == "error" and call.error_kind == "timeout"
    [row] = h.rows
    assert row.source_used == "default" and row.jev_answer is None


@pytest.mark.parametrize("situation", ["kill_switch", "open_circuit", "spend"])
async def test_shadow_that_cannot_reach_jev_is_effectively_off(situation: str) -> None:
    h = build_harness(
        {SPEC_ID: ABOVE},
        jev_enabled=situation != "kill_switch",
        spend=SpendTrip() if situation == "spend" else None,
    )
    if situation == "open_circuit":
        h.breaker.record_failure()
        h.breaker.record_failure()
    result = await decide(h, noul_spec(mode="shadow"))
    assert h.fake_jev.calls == []
    assert (result.mode, result.used, result.source) == ("off", False, "default")
    await h.drain()
    assert h.rows == []


# ───────────────────────── primary: the matrix ─────────────────────────


def _jev_outcome(h: Any, jev: str) -> None:
    if jev == "error:timeout":
        h.fake_jev.fail_with(JevError("timeout"))
    elif jev == "error:server":
        h.fake_jev.fail_with(JevError("server", status=503))
    elif jev == "error:auth":
        h.fake_jev.fail_with(JevError("auth", status=401))
    elif jev == "error:invalid":
        h.fake_jev.set_answer(SPEC_ID, {"noul": "very"})
    elif jev == "error:crash":
        h.fake_jev.fail_with(RuntimeError("client bug"))  # type: ignore[arg-type]
    elif jev == "disabled":
        h.fake_jev.fail_with(JevError("disabled"))
    elif jev == "above":
        h.fake_jev.set_answer(SPEC_ID, ABOVE)
    elif jev == "below":
        h.fake_jev.set_answer(SPEC_ID, BELOW)
    elif jev == "exactly_at_threshold":
        h.fake_jev.set_answer(SPEC_ID, noul_answer(0.8))  # |2p-1| = 0.6, the threshold


# fallback, jev outcome, world -> (used, source, value, escalated, reason, jev calls, claude calls)
MATRIX = [
    ("claude", "above", "ok", True, "jev", True, False, None, 1, 0),
    ("skip", "above", "ok", True, "jev", True, False, None, 1, 0),
    ("claude", "exactly_at_threshold", "ok", True, "jev", True, False, None, 1, 0),
    ("claude", "below", "ok", True, "claude", True, True, "low_confidence", 1, 1),
    ("skip", "below", "ok", False, "default", False, False, "low_confidence", 1, 0),
    ("claude", "error:timeout", "ok", True, "claude", True, True, "jev_error", 1, 1),
    ("skip", "error:timeout", "ok", False, "default", False, False, "jev_error", 1, 0),
    ("claude", "error:server", "ok", True, "claude", True, True, "jev_error", 1, 1),
    ("claude", "error:auth", "ok", True, "claude", True, True, "jev_error", 1, 1),
    ("claude", "error:invalid", "ok", True, "claude", True, True, "jev_error", 1, 1),
    ("skip", "error:invalid", "ok", False, "default", False, False, "jev_error", 1, 0),
    ("claude", "error:crash", "ok", True, "claude", True, True, "jev_error", 1, 1),
    ("claude", "disabled", "ok", True, "claude", True, True, "jev_disabled", 1, 1),
    ("skip", "disabled", "ok", False, "default", False, False, "jev_disabled", 1, 0),
    ("claude", "above", "kill_switch", True, "claude", True, True, "jev_disabled", 0, 1),
    ("skip", "above", "kill_switch", False, "default", False, False, "jev_disabled", 0, 0),
    ("claude", "above", "open_circuit", True, "claude", True, True, "circuit_open", 0, 1),
    ("skip", "above", "open_circuit", False, "default", False, False, "circuit_open", 0, 0),
    ("claude", "above", "spend_all", False, "default", False, False, "spend", 0, 0),
    ("skip", "above", "spend_all", False, "default", False, False, "spend", 0, 0),
    ("claude", "below", "spend_fallback", False, "default", False, False, "spend", 1, 0),
    ("claude", "error:timeout", "spend_fallback", False, "default", False, False, "spend", 1, 0),
    ("claude", "below", "no_claude_client", False, "default", False, False, "low_confidence", 1, 0),
    ("claude", "below", "claude_overloaded", False, "default", False, True, "low_confidence", 1, 1),
    ("claude", "below", "claude_garbage", False, "default", False, True, "low_confidence", 1, 1),
]


@pytest.mark.parametrize(
    (
        "fallback",
        "jev",
        "world",
        "used",
        "source",
        "value",
        "escalated",
        "reason",
        "jev_calls",
        "claude_calls",
    ),
    MATRIX,
    ids=[f"{m[0]}-{m[1]}-{m[2]}" for m in MATRIX],
)
async def test_primary_matrix(
    fallback: str,
    jev: str,
    world: str,
    used: bool,
    source: str,
    value: bool,
    escalated: bool,
    reason: str | None,
    jev_calls: int,
    claude_calls: int,
) -> None:
    spend = {"spend_all": SpendTrip(), "spend_fallback": SpendTrip({"decision_fallback"})}.get(world)
    h = build_harness(
        {SPEC_ID: ABOVE},
        jev_enabled=world != "kill_switch",
        spend=spend,
        with_claude=world != "no_claude_client",
    )
    _jev_outcome(h, jev)
    if world == "open_circuit":
        h.breaker.record_failure()
        h.breaker.record_failure()
    if h.claude is not None:
        if world == "claude_overloaded":
            h.claude.script_structured("decision_fallback", ClaudeOverloaded(), repeat=True)
        elif world == "claude_garbage":
            h.claude.script_structured("decision_fallback", {"answer": "maybe"}, repeat=True)
        else:
            h.claude.script_structured("decision_fallback", CLAUDE_YES, repeat=True)

    spec = noul_spec(fallback=fallback)  # type: ignore[arg-type]
    result = await decide(h, spec)

    assert result.mode == "primary"
    assert (result.used, result.source, result.escalated, result.escalation_reason) == (
        used,
        source,
        escalated,
        reason,
    )
    assert len(h.fake_jev.calls) == jev_calls
    assert len(h.fallback_calls) == claude_calls
    if used:
        assert result.value is value
    else:
        assert result.value is spec.neutral  # the neutral value, whatever Jev thought
    # what is attached
    if source == "jev":
        assert result.jev is not None and result.claude is None and result.confidence == result.jev.confidence
    if source == "claude":
        assert result.claude == CLAUDE_YES and result.confidence == pytest.approx(0.9)
    if jev == "below" and world == "ok":
        assert result.jev is not None  # the unsure answer stays attached for the log
        if source == "default":
            assert result.confidence == pytest.approx(0.1)
    if jev.startswith("error") or jev == "disabled" or world in {"kill_switch", "open_circuit", "spend_all"}:
        assert result.jev is None


async def test_the_fallback_call_is_a_haiku_decision_fallback_call_without_effort() -> None:
    h = build_harness({SPEC_ID: BELOW})
    assert h.claude is not None
    h.claude.script_structured("decision_fallback", CLAUDE_YES)
    result = await decide(h, noul_spec(), "the resume text")
    [call] = h.fallback_calls
    assert call.model == "claude-haiku-4-5" and call.effort is None
    assert call.ctx.purpose == "decision_fallback"
    assert call.ctx.profile_id == "profile-1" and call.ctx.analysis_id == CTX.analysis_id
    assert "the resume text" in call.user_text
    assert result.claude == CLAUDE_YES


async def test_the_fallback_model_can_be_set_per_spec_through_overrides() -> None:
    h = build_harness({SPEC_ID: BELOW}, overrides={SPEC_ID: {"fallback_model": "claude-sonnet-5-5"}})
    assert h.claude is not None
    h.claude.script_structured("decision_fallback", CLAUDE_YES)
    await decide(h)
    assert h.fallback_calls[0].model == "claude-sonnet-5-5"


async def test_a_confidence_just_below_the_threshold_escalates() -> None:
    h = build_harness({SPEC_ID: noul_answer(0.79)})  # |2p-1| = 0.58 < 0.6
    assert h.claude is not None
    h.claude.script_structured("decision_fallback", CLAUDE_YES)
    result = await decide(h)
    assert result.escalated and result.escalation_reason == "low_confidence"


async def test_a_threshold_override_changes_which_answers_are_used() -> None:
    h = build_harness({SPEC_ID: noul_answer(0.79)}, overrides={SPEC_ID: {"threshold": 0.5}})
    result = await decide(h)
    assert result.used and result.source == "jev" and result.threshold == 0.5


async def test_a_claude_fallback_choice_and_score_flow_through_interpret() -> None:
    pick, level = choice_spec(), score_spec()
    h = build_harness({})  # an uninformative Jev (confidence 0) for both
    assert h.claude is not None
    h.claude.script_structured(
        "decision_fallback", {"choice": "beta"}, when=lambda c: "Which one?" in c.user_text
    )
    h.claude.script_structured("decision_fallback", {"level": 2}, when=lambda c: "How much?" in c.user_text)
    result = await h.runner.run(make_pack(pick, level), "text", ctx=CTX)
    assert (result["t.pick"].value, result["t.pick"].source, result["t.pick"].used) == (
        "beta",
        "claude",
        True,
    )
    assert (result["t.level"].value, result["t.level"].source) == (2, "claude")
    assert len(h.fake_jev.calls) == 1  # both questions in one Jev call
    assert len(h.fallback_calls) == 2


# ───────────────────────── never raises ─────────────────────────


@pytest.mark.parametrize(
    "error",
    [
        JevError("auth"),
        JevError("rate_limit", retry_after_s=3),
        JevError("bad_request"),
        JevError("timeout"),
        JevError("connection"),
        JevError("server"),
        JevError("invalid_response"),
        JevError("disabled"),
        RuntimeError("boom"),
        ValueError("boom"),
    ],
    ids=lambda e: getattr(e, "kind", type(e).__name__),
)
@pytest.mark.parametrize("mode", ["primary", "shadow"])
async def test_a_jev_failure_never_raises_out_of_run(error: Exception, mode: str) -> None:
    h = build_harness({SPEC_ID: ABOVE}, with_claude=False)
    h.fake_jev.fail_with(error)  # type: ignore[arg-type]
    result = await decide(h, noul_spec(mode=mode))  # type: ignore[arg-type]
    assert result.used is False and result.source == "default"


async def test_a_state_builder_that_raises_gives_neutral_results() -> None:
    h = build_harness({SPEC_ID: ABOVE})
    pack = make_pack(noul_spec(neutral=True))
    broken = type(pack)(id=pack.id, specs=pack.specs, build_state=lambda s: 1 / 0)
    result = await h.runner.run(broken, "x", ctx=CTX)
    assert result[SPEC_ID].used is False and result[SPEC_ID].value is True  # neutral=True
    assert h.fake_jev.calls == []


async def test_an_interpret_that_raises_is_treated_as_a_jev_error() -> None:
    spec = noul_spec()
    boom = type(spec)(
        id=spec.id,
        version=spec.version,
        pack=spec.pack,
        question=spec.question,
        interpret=lambda a: 1 / 0,
        neutral=False,
        default=spec.default,
    )
    h = build_harness({SPEC_ID: ABOVE})
    assert h.claude is not None
    h.claude.script_structured("decision_fallback", CLAUDE_YES)
    # the fallback's own interpret fails too, so the decision stays neutral instead of crashing
    result = await decide(h, boom)
    assert result.used is False and result.escalation_reason == "jev_error" and result.escalated


async def test_a_missing_answer_in_the_response_counts_as_a_jev_error() -> None:
    h = build_harness({SPEC_ID: ABOVE})
    assert h.claude is not None
    h.claude.script_structured("decision_fallback", CLAUDE_YES)
    original = h.fake_jev.system_one

    async def drop_answers(*args: Any, **kwargs: Any) -> Any:
        return dataclasses.replace(await original(*args, **kwargs), answers={})

    h.fake_jev.system_one = drop_answers  # type: ignore[method-assign]
    result = await decide(h)
    assert result.escalation_reason == "jev_error" and result.source == "claude" and result.used


# ───────────────────────── breaker accounting ─────────────────────────


async def test_repeated_failures_open_the_circuit_and_stop_the_calls() -> None:
    h = build_harness({SPEC_ID: ABOVE}, with_claude=False)
    h.fake_jev.fail_with(JevError("timeout"))
    for _ in range(2):
        await decide(h)
    assert h.breaker.state == "open"
    result = await decide(h)
    assert len(h.fake_jev.calls) == 2  # the third run never reached Jev
    assert result.escalation_reason == "circuit_open"


async def test_a_bad_request_does_not_count_against_the_circuit() -> None:
    h = build_harness({SPEC_ID: ABOVE}, with_claude=False)
    h.fake_jev.fail_with(JevError("bad_request"))
    for _ in range(5):
        await decide(h)
    assert h.breaker.state == "closed"
    assert len(h.fake_jev.calls) == 5


async def test_the_circuit_recovers_after_the_cool_down_with_one_trial_call() -> None:
    h = build_harness({SPEC_ID: ABOVE}, with_claude=False)
    h.fake_jev.fail_with(JevError("server"))
    for _ in range(2):
        await decide(h)
    assert h.breaker.state == "open"

    h.fake_jev.fail_with(None)
    h.clock.advance(61)
    result = await decide(h)
    assert result.used and result.source == "jev"
    assert h.breaker.state == "closed"


async def test_a_success_clears_the_failure_count() -> None:
    h = build_harness({SPEC_ID: ABOVE}, with_claude=False)
    h.fake_jev.fail_next(JevError("timeout"))
    await decide(h)
    await decide(h)  # succeeds
    h.fake_jev.fail_next(JevError("timeout"))
    await decide(h)
    assert h.breaker.state == "closed"  # failures were never consecutive enough to open it


# ───────────────────────── spend gate ─────────────────────────


async def test_the_spend_gate_is_asked_for_jev_and_for_the_fallback() -> None:
    gate = SpendTrip(set())  # records, trips nothing
    h = build_harness({SPEC_ID: BELOW}, spend=gate)
    assert h.claude is not None
    h.claude.script_structured("decision_fallback", CLAUDE_YES)
    await decide(h)
    assert gate.calls == ["jev", "decision_fallback"]


async def test_a_spend_gate_that_breaks_fails_closed() -> None:
    class Broken:
        async def __call__(self, kind: str) -> None:
            raise RuntimeError("ledger down")

    h = build_harness({SPEC_ID: ABOVE})
    h.runner._spend_gate = Broken()  # type: ignore[assignment]
    result = await decide(h)
    assert result.used is False and result.escalation_reason == "spend"
    assert h.fake_jev.calls == []


async def test_a_spend_error_raised_by_the_client_itself_means_spend() -> None:
    from tayyari.llm.errors import SpendCeilingReached

    h = build_harness({SPEC_ID: ABOVE})
    h.fake_jev.fail_with(SpendCeilingReached())  # type: ignore[arg-type]
    result = await decide(h)
    assert result.escalation_reason == "spend" and not result.used
    assert h.breaker.state == "closed"  # a guard trip is not a Jev failure
    await h.drain()
    assert h.calls == []  # nothing was sent, so there is no call row


async def test_a_spend_error_raised_by_the_claude_client_means_spend() -> None:
    from tayyari.llm.errors import SpendCeilingReached

    h = build_harness({SPEC_ID: BELOW})
    assert h.claude is not None
    h.claude.script_structured("decision_fallback", SpendCeilingReached(), repeat=True)
    result = await decide(h)
    assert result.escalation_reason == "spend" and not result.used and result.escalated is False
