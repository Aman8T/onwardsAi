"""Runner behaviour around the pack: batching, binding, chunking, the shadow time cap, audits, logging, redaction,
``run_many`` and the tracked background tasks.
"""

from __future__ import annotations

import asyncio
import zlib
from typing import Any

import pytest

from tayyari.core.hashing import canonical_json, sha256_hex
from tayyari.core.limits import JEV_MAX_QUESTIONS_PER_CALL
from tayyari.decisions.spec import DecisionPack
from tayyari.llm.errors import JevError
from tests.fakes.jev import choice_answer, noul_answer, score_answer
from tests.unit.decisions.support import (
    CTX,
    OPTIONS,
    SAMPLE_CTX,
    ScriptedRandom,
    SpendTrip,
    bound_spec,
    build_harness,
    choice_spec,
    make_pack,
    noul_spec,
    score_spec,
)

CLAUDE_YES = {"answer": True, "confidence": "high"}
ABOVE = noul_answer(0.95)
BELOW = noul_answer(0.45)


# ───────────────────────── batching and binding ─────────────────────────


async def test_all_specs_of_a_pack_share_one_jev_call() -> None:
    h = build_harness(
        {
            "t.flag": noul_answer(0.95),
            "t.pick": choice_answer("alpha", options=list(OPTIONS), p=0.9),
            "t.level": score_answer(2, n_levels=4, p=0.9),
        }
    )
    result = await h.runner.run(make_pack(noul_spec(), choice_spec(), score_spec()), "text", ctx=CTX)
    [call] = h.fake_jev.calls
    assert call.question_ids == ["t.flag", "t.pick", "t.level"]
    assert call.state == {"text": "text"}
    assert call.timeout_s == 2.5
    assert list(result) == ["t.flag", "t.pick", "t.level"]  # plan order
    assert (result["t.flag"].value, result["t.pick"].value, result["t.level"].value) == (True, "alpha", 2)
    assert all(r.used and r.source == "jev" for r in result.values())


async def test_only_limits_the_run_by_spec_id_or_question_key() -> None:
    h = build_harness({})
    pack = make_pack(noul_spec(), choice_spec(), bound_spec())
    result = await h.runner.run(pack, "x", ctx=CTX, only={"t.pick"})
    assert list(result) == ["t.pick"]
    assert h.fake_jev.calls[0].question_ids == ["t.pick"]

    bindings = {"t.bound#a": {"name": "a"}, "t.bound#b": {"name": "b"}}
    result = await h.runner.run(pack, "x", ctx=CTX, only={"t.bound#b"}, bindings=bindings)
    assert list(result) == ["t.bound#b"]
    result = await h.runner.run(pack, "x", ctx=CTX, only={"t.bound"}, bindings=bindings)
    assert list(result) == ["t.bound#a", "t.bound#b"]


async def test_a_bound_spec_is_asked_once_per_binding_and_never_without_one() -> None:
    h = build_harness({"t.bound": noul_answer(0.9)})
    pack = make_pack(noul_spec(), bound_spec())
    plain = await h.runner.run(pack, "x", ctx=CTX)
    assert list(plain) == ["t.flag"]

    bindings = {
        "t.bound#python": {"name": "python"},
        "t.bound#sql": {"name": "sql"},
        "other#x": {"name": "x"},
    }
    result = await h.runner.run(pack, "x", ctx=CTX, bindings=bindings)
    assert list(result) == ["t.flag", "t.bound#python", "t.bound#sql"]
    asked = h.fake_jev.calls[-1].questions
    assert asked["t.bound#python"].instructions == {"question": "Is it present?", "name": "python"}
    assert asked["t.bound#sql"].instructions == {"question": "Is it present?", "name": "sql"}
    instances = result.instances("t.bound")
    assert set(instances) == {"python", "sql"}
    assert result.instances("t.flag").keys() == {""}
    # the FakeJev script entry for the spec id answers every instance
    assert instances["python"].value is True and instances["python"].spec_version == "2"
    assert instances["python"].key == "t.bound#python"


async def test_a_bind_function_that_raises_gives_a_neutral_result_for_that_key_only() -> None:
    spec = bound_spec()
    broken = type(spec)(
        id=spec.id,
        version=spec.version,
        pack=spec.pack,
        question=spec.question,
        bind=lambda args: spec.bind(args) if args["name"] != "bad" else 1 / 0,  # type: ignore[misc]
        interpret=spec.interpret,
        neutral=False,
        default=spec.default,
    )
    h = build_harness({"t.bound": noul_answer(0.9)})
    bindings = {"t.bound#good": {"name": "good"}, "t.bound#bad": {"name": "bad"}}
    result = await h.runner.run(make_pack(broken), "x", ctx=CTX, bindings=bindings)
    assert result["t.bound#good"].source == "jev" and result["t.bound#good"].value is True
    assert result["t.bound#bad"].used is False and result["t.bound#bad"].source == "default"
    assert list(h.fake_jev.calls[0].questions) == ["t.bound#good"]


async def test_used_value_helper() -> None:
    h = build_harness(
        {"t.flag": ABOVE, "t.pick": choice_answer("alpha", options=list(OPTIONS), p=0.34)}, with_claude=False
    )
    result = await h.runner.run(make_pack(noul_spec(), choice_spec()), "x", ctx=CTX)
    assert result.used_value("t.flag") is True
    assert result.used_value("t.pick", "fallback-value") == "fallback-value"  # low confidence, not used
    assert result.used_value("missing", 5) == 5
    assert "t.flag" in result and len(result) == 2 and result.get("missing") is None


# ───────────────────────── chunking ─────────────────────────


async def test_more_questions_than_the_limit_are_split_into_chunks() -> None:
    assert JEV_MAX_QUESTIONS_PER_CALL == 30
    count = JEV_MAX_QUESTIONS_PER_CALL * 2 + 5
    h = build_harness({"t.bound": noul_answer(0.9)})
    bindings = {f"t.bound#{i}": {"name": f"skill-{i}"} for i in range(count)}
    result = await h.runner.run(
        make_pack(bound_spec(mode="primary", threshold=0.5)), "x", ctx=CTX, bindings=bindings
    )
    sizes = sorted(len(c.questions) for c in h.fake_jev.calls)
    assert sizes == [5, 30, 30]
    assert len(result) == count
    assert all(r.used and r.value is True for r in result.values())
    assert {q for c in h.fake_jev.calls for q in c.question_ids} == set(bindings)
    await h.drain()
    assert len(h.calls) == 3 and len(h.rows) == count
    # one stored state for three calls over the same state
    assert len(h.repos.decisions.states) == 1


async def test_the_chunk_size_is_configurable_and_primary_questions_come_first() -> None:
    h = build_harness({}, max_questions_per_call=2)
    pack = make_pack(
        noul_spec("t.a", mode="shadow"), noul_spec("t.b"), noul_spec("t.c", mode="shadow"), noul_spec("t.d")
    )
    result = await h.runner.run(pack, "x", ctx=CTX)
    chunks = [c.question_ids for c in h.fake_jev.calls]
    assert sorted(chunks) == [["t.a", "t.c"], ["t.b", "t.d"]]  # primary ones together, shadow ones together
    assert list(result) == ["t.a", "t.b", "t.c", "t.d"]  # results keep plan order


async def test_a_chunk_with_a_primary_question_carries_shadow_questions_along() -> None:
    h = build_harness({"t.p": ABOVE, "t.s": ABOVE})
    pack = make_pack(noul_spec("t.p"), noul_spec("t.s", mode="shadow"))
    result = await h.runner.run(pack, "x", ctx=CTX)
    assert len(h.fake_jev.calls) == 1
    assert result["t.p"].used and result["t.s"].used is False and result["t.s"].value is True


async def test_the_timeout_of_a_call_is_the_largest_of_its_questions() -> None:
    h = build_harness({})
    pack = make_pack(noul_spec("t.a", timeout_s=1.0), noul_spec("t.b", timeout_s=4.0))
    await h.runner.run(pack, "x", ctx=CTX)
    assert h.fake_jev.calls[0].timeout_s == 4.0


# ───────────────────────── the shadow time cap ─────────────────────────


async def test_a_slow_shadow_call_does_not_hold_the_caller_and_still_logs_in_the_background() -> None:
    h = build_harness({"t.flag": ABOVE}, slow_jev_s=0.3, shadow_wait_s=0.02)
    started = asyncio.get_running_loop().time()
    result = await h.runner.run(make_pack(noul_spec(mode="shadow")), "x", ctx=CTX, incumbent={"t.flag": 41})
    waited = asyncio.get_running_loop().time() - started
    assert waited < 0.25
    assert (result["t.flag"].mode, result["t.flag"].source, result["t.flag"].used) == (
        "shadow",
        "default",
        False,
    )
    assert h.runner.pending_tasks == 1
    assert h.rows == []

    await h.runner.drain(3.0)
    assert h.runner.pending_tasks == 0
    [row] = h.rows
    assert row.mode == "shadow" and row.incumbent == {"value": 41} and row.jev_answer is not None


async def test_a_shadow_call_that_finishes_in_time_returns_its_answers() -> None:
    h = build_harness({"t.flag": ABOVE}, slow_jev_s=0.01, shadow_wait_s=1.0)
    result = await h.runner.run(make_pack(noul_spec(mode="shadow")), "x", ctx=CTX)
    assert result["t.flag"].source == "jev" and result["t.flag"].value is True


async def test_a_primary_call_is_always_awaited_in_full() -> None:
    h = build_harness({"t.flag": ABOVE}, slow_jev_s=0.15, shadow_wait_s=0.01)
    result = await h.runner.run(make_pack(noul_spec()), "x", ctx=CTX)
    assert result["t.flag"].used and result["t.flag"].source == "jev"


async def test_cancelling_the_caller_leaves_the_shadow_call_running() -> None:
    h = build_harness({"t.flag": ABOVE}, slow_jev_s=0.1, shadow_wait_s=5.0)
    task = asyncio.create_task(h.runner.run(make_pack(noul_spec(mode="shadow")), "x", ctx=CTX))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert h.runner.pending_tasks == 1  # shielded: it finishes and logs anyway
    await h.runner.drain(3.0)
    assert len(h.rows) == 1


async def test_cancellation_is_not_swallowed_by_the_safety_net() -> None:
    h = build_harness({"t.flag": ABOVE}, slow_jev_s=5.0)
    task = asyncio.create_task(h.runner.run(make_pack(noul_spec()), "x", ctx=CTX))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ───────────────────────── audits ─────────────────────────


async def test_audit_rate_samples_used_jev_answers_and_writes_one_row_with_both_answers() -> None:
    h = build_harness({"t.flag": ABOVE}, rng=ScriptedRandom([0.01, 0.9, 0.01]))
    assert h.claude is not None
    h.claude.script_structured("decision_fallback", CLAUDE_YES, {"answer": False, "confidence": "high"})
    pack = make_pack(noul_spec(audit_rate=0.5))

    first = await h.runner.run(pack, "a", ctx=CTX)  # rng 0.01 < 0.5: audited, Claude agrees
    skipped = await h.runner.run(pack, "b", ctx=CTX)  # rng 0.9: not audited
    third = await h.runner.run(pack, "c", ctx=CTX)  # rng 0.01: audited, Claude disagrees
    await h.drain()

    assert all(
        r["t.flag"].used and r["t.flag"].source == "jev" and not r["t.flag"].escalated
        for r in (first, skipped, third)
    )
    assert len(h.fallback_calls) == 2
    assert len(h.rows) == 3  # exactly one row per decision, audited or not
    by_id = {r.id: r for r in h.rows}
    audited_ok = by_id[first["t.flag"].log_id]  # type: ignore[index]
    assert audited_ok.claude_answer == CLAUDE_YES
    assert audited_ok.extra["audit"] is True and audited_ok.extra["audit_agrees"] is True
    assert audited_ok.source_used == "jev" and audited_ok.used is True and audited_ok.escalated is False
    audited_bad = by_id[third["t.flag"].log_id]  # type: ignore[index]
    assert audited_bad.extra["audit_agrees"] is False
    plain = by_id[skipped["t.flag"].log_id]  # type: ignore[index]
    assert plain.claude_answer is None and "audit" not in plain.extra


async def test_audit_cost_is_added_to_the_row() -> None:
    h = build_harness({"t.flag": ABOVE}, rng=ScriptedRandom([0.0]))
    assert h.claude is not None
    h.claude.script_structured("decision_fallback", CLAUDE_YES)
    result = await h.runner.run(make_pack(noul_spec(audit_rate=1.0)), "a", ctx=CTX)
    await h.drain()
    [row] = h.rows
    assert row.cost_micro_usd > result["t.flag"].cost_micro_usd > 0
    assert row.extra["claude_model"] == "claude-haiku-4-5" and row.extra["claude_request_id"]


@pytest.mark.parametrize("situation", ["rate_zero", "no_claude", "escalated", "shadow"])
async def test_no_audit_when_it_does_not_apply(situation: str) -> None:
    h = build_harness(
        {"t.flag": BELOW if situation == "escalated" else ABOVE},
        rng=ScriptedRandom([0.0] * 5),
        with_claude=situation != "no_claude",
    )
    if h.claude is not None:
        h.claude.script_structured("decision_fallback", CLAUDE_YES, repeat=True)
    spec = noul_spec(
        audit_rate=0.0 if situation == "rate_zero" else 1.0,
        mode="shadow" if situation == "shadow" else "primary",
    )
    await h.runner.run(make_pack(spec), "a", ctx=CTX)
    await h.drain()
    expected_fallback_calls = 1 if situation == "escalated" else 0  # the escalation itself, not an audit
    assert len(h.fallback_calls) == expected_fallback_calls
    assert len(h.rows) == 1
    assert "audit" not in h.rows[0].extra


async def test_an_audit_that_fails_still_writes_the_row() -> None:
    from tayyari.llm.errors import ClaudeOverloaded

    h = build_harness({"t.flag": ABOVE}, rng=ScriptedRandom([0.0]))
    assert h.claude is not None
    h.claude.script_structured("decision_fallback", ClaudeOverloaded())
    await h.runner.run(make_pack(noul_spec(audit_rate=1.0)), "a", ctx=CTX)
    await h.drain()
    [row] = h.rows
    assert (
        row.claude_answer is None and row.extra["audit"] is True and row.extra["claude_error"] == "overloaded"
    )
    assert "audit_agrees" not in row.extra


# ───────────────────────── logging ─────────────────────────


async def test_a_call_and_its_decisions_are_logged_and_linked() -> None:
    h = build_harness(
        {"t.flag": ABOVE, "t.pick": choice_answer("beta", options=list(OPTIONS), p=0.8)}, send_pii=True
    )
    state = {"text": "Built a design system"}
    result = await h.runner.run(make_pack(noul_spec(), choice_spec()), "Built a design system", ctx=CTX)
    await h.drain()

    [call] = h.calls
    assert call.pack == "t" and call.status == "ok"
    assert call.model_requested == "jev-1.13.0" and call.model_served == "jev-1.13.0"
    assert call.profile_id == "profile-1" and call.analysis_id == CTX.analysis_id and call.is_sample is False
    assert call.questions == {
        "t.flag": noul_spec().question.to_wire(),
        "t.pick": choice_spec().question.to_wire(),
    }
    assert call.response is not None and set(call.response["answers"]) == {"t.flag", "t.pick"}
    assert call.request_id and call.input_tokens > 0 and call.cost_micro_usd > 0 and call.error_kind is None
    assert call.state_hash == sha256_hex(canonical_json(state))
    blob = h.repos.decisions.states[call.state_hash]
    assert zlib.decompress(blob.payload or b"") == canonical_json(state)
    assert blob.raw_bytes == len(canonical_json(state))

    rows = {r.spec_id: r for r in h.rows}
    flag = rows["t.flag"]
    assert flag.call_id == call.id and flag.pack == "t" and flag.spec_version == "1"
    assert flag.mode == "primary" and flag.used and flag.source_used == "jev" and not flag.escalated
    assert flag.escalation_reason is None and flag.subject_ref == "t.flag"
    assert flag.jev_answer is not None and flag.jev_answer["kind"] == "noul"
    assert flag.jev_confidence == pytest.approx(0.9) and flag.threshold == 0.6
    assert flag.profile_id == "profile-1" and flag.analysis_id == CTX.analysis_id and flag.is_sample is False
    assert flag.extra["state_hash"] == call.state_hash and flag.extra["value"] is True
    assert flag.extra["purpose_tag"] == "test" and flag.extra["jev_model"] == "jev-1.13.0"
    assert flag.id == result["t.flag"].log_id
    assert rows["t.pick"].extra["value"] == "beta"
    # the cost of the call is shared out exactly
    assert sum(r.cost_micro_usd for r in h.rows) == call.cost_micro_usd


async def test_an_escalated_decision_records_both_answers() -> None:
    h = build_harness({"t.flag": BELOW})
    assert h.claude is not None
    h.claude.script_structured("decision_fallback", CLAUDE_YES)
    result = await h.runner.run(make_pack(noul_spec()), "x", ctx=CTX)
    await h.drain()
    [row] = h.rows
    assert row.escalated and row.escalation_reason == "low_confidence" and row.source_used == "claude"
    assert row.jev_answer is not None and row.claude_answer == CLAUDE_YES and row.used
    assert row.jev_confidence == pytest.approx(0.1) and row.extra["claude_model"] == "claude-haiku-4-5"
    assert row.cost_micro_usd == result["t.flag"].cost_micro_usd
    assert result["t.flag"].cost_micro_usd > h.calls[0].cost_micro_usd  # Jev plus the Haiku call


async def test_a_failed_call_is_logged_with_its_error_kind() -> None:
    h = build_harness({}, with_claude=False)
    h.fake_jev.fail_next(JevError("rate_limit", retry_after_s=3))
    await h.runner.run(make_pack(noul_spec()), "x", ctx=CTX)
    await h.drain()
    [call] = h.calls
    assert call.status == "error" and call.error_kind == "rate_limit" and call.response is None
    assert call.cost_micro_usd == 0
    [row] = h.rows
    assert (
        row.call_id == call.id
        and row.escalation_reason == "jev_error"
        and row.extra["jev_error"] == "rate_limit"
    )


async def test_nothing_is_logged_when_nothing_was_asked() -> None:
    h = build_harness({}, jev_enabled=False, with_claude=False)
    await h.runner.run(make_pack(noul_spec()), "x", ctx=CTX)
    await h.drain()
    assert h.rows == [] and h.calls == [] and h.repos.decisions.states == {}


async def test_sample_runs_are_flagged_on_every_row() -> None:
    h = build_harness({"t.flag": ABOVE}, with_claude=False)
    await h.runner.run(make_pack(noul_spec()), "x", ctx=SAMPLE_CTX)
    await h.drain()
    assert h.calls[0].is_sample and h.rows[0].is_sample
    assert next(iter(h.repos.decisions.states.values())).is_sample


async def test_the_spend_ledger_gets_the_jev_cost_through_the_log() -> None:
    h = build_harness({"t.flag": ABOVE}, with_claude=False)
    await h.runner.run(make_pack(noul_spec()), "x", ctx=CTX)
    await h.drain()
    micro, calls = h.repos.limits.spend(h.clock.now().date(), "jev")
    assert calls == 1 and micro == h.calls[0].cost_micro_usd > 0


# ───────────────────────── redaction ─────────────────────────


async def test_contact_data_is_masked_before_sending_and_before_storing() -> None:
    h = build_harness({"t.flag": ABOVE}, with_claude=False)
    await h.runner.run(
        make_pack(noul_spec()), "Reach me at jane.doe@example.com or +44 20 7946 0958.", ctx=CTX
    )
    await h.drain()
    sent_state = h.fake_jev.calls[0].state
    assert sent_state == {"text": "Reach me at [email] or [phone]."}
    stored = next(iter(h.repos.decisions.states.values()))
    assert zlib.decompress(stored.payload or b"") == canonical_json(sent_state)


async def test_with_send_pii_the_state_is_untouched() -> None:
    h = build_harness({"t.flag": ABOVE}, with_claude=False, send_pii=True)
    await h.runner.run(make_pack(noul_spec()), "mail jane.doe@example.com", ctx=CTX)
    assert h.fake_jev.calls[0].state == {"text": "mail jane.doe@example.com"}


async def test_the_fallback_sees_the_same_masked_state() -> None:
    h = build_harness({"t.flag": BELOW})
    assert h.claude is not None
    h.claude.script_structured("decision_fallback", CLAUDE_YES)
    await h.runner.run(make_pack(noul_spec()), "mail jane.doe@example.com", ctx=CTX)
    assert "jane.doe@example.com" not in h.fallback_calls[0].user_text
    assert "[email]" in h.fallback_calls[0].user_text


# ───────────────────────── run_many ─────────────────────────


async def test_run_many_keeps_input_order_and_bounds_concurrency() -> None:
    h = build_harness({"t.flag": ABOVE}, slow_jev_s=0.02, with_claude=False)
    subjects = [f"claim {i}" for i in range(12)]
    results = await h.runner.run_many(make_pack(noul_spec()), subjects, concurrency=3, ctx=CTX)
    assert len(results) == 12 and all(r["t.flag"].used for r in results)
    assert h.jev.max_in_flight == 3 and h.jev.started == 12
    assert [c.state["text"] for c in h.fake_jev.calls].count("claim 0") == 1
    assert sorted(c.state["text"] for c in h.fake_jev.calls) == sorted(subjects)


async def test_run_many_accepts_per_subject_incumbents_and_bindings() -> None:
    h = build_harness({"t.bound": ABOVE}, with_claude=False)
    pack = make_pack(bound_spec())
    subjects = ["a", "b"]
    bindings = [{"t.bound#x": {"name": "x"}}, {"t.bound#y": {"name": "y"}}]
    incumbent = [{"t.bound#x": 10}, {"t.bound#y": 20}]
    results = await h.runner.run_many(pack, subjects, ctx=CTX, bindings=bindings, incumbent=incumbent)
    assert [list(r) for r in results] == [["t.bound#x"], ["t.bound#y"]]
    await h.drain()
    assert sorted(str(r.incumbent) for r in h.rows) == ["{'value': 10}", "{'value': 20}"]


async def test_run_many_accepts_one_mapping_for_every_subject() -> None:
    h = build_harness({"t.bound": ABOVE}, with_claude=False)
    results = await h.runner.run_many(
        make_pack(bound_spec()), ["a", "b", "c"], ctx=CTX, bindings={"t.bound#x": {"name": "x"}}
    )
    assert [list(r) for r in results] == [["t.bound#x"]] * 3


async def test_run_many_rejects_a_sequence_of_the_wrong_length() -> None:
    h = build_harness({})
    with pytest.raises(ValueError):
        await h.runner.run_many(make_pack(bound_spec()), ["a", "b"], ctx=CTX, bindings=[{}])


async def test_run_many_of_nothing_is_empty() -> None:
    h = build_harness({})
    assert await h.runner.run_many(make_pack(noul_spec()), [], ctx=CTX) == []


# ───────────────────────── tracked tasks ─────────────────────────


async def test_spawn_tracks_a_task_and_drain_waits_for_it() -> None:
    h = build_harness({})
    done: list[int] = []

    async def work() -> None:
        await asyncio.sleep(0.05)
        done.append(1)

    task = h.runner.spawn(work())
    assert h.runner.pending_tasks == 1 and not done
    await h.runner.drain(2.0)
    assert done == [1] and task.done() and h.runner.pending_tasks == 0


async def test_an_exception_in_a_spawned_task_is_logged_not_raised() -> None:
    h = build_harness({})

    async def boom() -> None:
        raise RuntimeError("background failure")

    task = h.runner.spawn(boom())
    await h.runner.drain(1.0)  # does not raise
    assert task.done() and isinstance(task.exception(), RuntimeError)
    assert h.runner.pending_tasks == 0


async def test_drain_leaves_slow_tasks_running_unless_asked_to_cancel() -> None:
    h = build_harness({})

    async def slow() -> None:
        await asyncio.sleep(10)

    task = h.runner.spawn(slow())
    await h.runner.drain(0.05)
    assert not task.done() and h.runner.pending_tasks == 1
    await h.runner.drain(0.05, cancel_remaining=True)
    assert task.cancelled() and h.runner.pending_tasks == 0


async def test_drain_flushes_the_decision_log() -> None:
    h = build_harness({"t.flag": ABOVE}, with_claude=False)
    await h.runner.run(make_pack(noul_spec()), "x", ctx=CTX)
    assert h.rows == []  # buffered: the timer is an hour away
    await h.runner.drain(1.0)
    assert len(h.rows) == 1 and len(h.calls) == 1


async def test_a_pack_with_no_applicable_question_returns_an_empty_result() -> None:
    h = build_harness({})
    empty: DecisionPack[Any] = make_pack()
    result = await h.runner.run(empty, "x", ctx=CTX)
    assert len(result) == 0 and h.fake_jev.calls == []
    only_bound = await h.runner.run(make_pack(bound_spec()), "x", ctx=CTX)
    assert len(only_bound) == 0 and h.fake_jev.calls == []


async def test_config_for_exposes_the_effective_config() -> None:
    h = build_harness({}, overrides={"t.*": {"threshold": 0.9}})
    cfg = h.runner.config_for(noul_spec())
    assert cfg.threshold == 0.9 and cfg.mode == "primary"


async def test_the_spend_gate_trip_makes_every_decision_neutral_even_in_a_mixed_pack() -> None:
    h = build_harness({}, spend=SpendTrip())
    pack = make_pack(noul_spec(neutral=True), choice_spec(), score_spec(), bound_spec())
    result = await h.runner.run(pack, "x", ctx=CTX, bindings={"t.bound#a": {"name": "a"}})
    assert h.fake_jev.calls == [] and h.fallback_calls == []
    assert all(r.used is False and r.source == "default" for r in result.values())
    assert (
        result["t.flag"].value is True
        and result["t.pick"].value == "unknown"
        and result["t.level"].value == -1
    )
    assert {r.escalation_reason for r in result.values() if r.mode == "primary"} == {"spend"}
    assert result["t.bound#a"].mode == "off"  # shadow with nothing asked
