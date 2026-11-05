"""The synthesis stage: progress steps, the graph-size gate, the single repair pass, request shape."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tayyari.analysis.schemas import SYNTH_SCHEMA
from tayyari.analysis.synth import (
    STEP2_CHARS,
    TIGHTENING_LOG,
    graph_size_problem,
    repair_note,
    synthesize,
)
from tayyari.contract import AnalysisEvent, LogEvent, SynthEvent
from tayyari.core.settings import Settings
from tayyari.llm.errors import ClaudeInvalidOutput, ClaudeOverloaded, ClaudeTruncated
from tayyari.llm.types import StructuredResult
from tayyari.prompts import SYNTH_SYSTEM
from tests.fakes.claude import FakeClaudeClient, StructuredCall, fake_usage
from tests.fakes.clock import FakeClock
from tests.unit.analysis.support import make_run, synth_output, tiny_synth

PROMPT = "THE SYNTH PROMPT"
LEGACY_REPAIR = (
    "IMPORTANT — your previous attempt failed ({reason}). Return ONLY the structured output, valid against the schema: "
    "every required field present, scores as integers 0–100, kebab-case ids, evidence.sourceId taken from the list "
    "above, 4–7 domains, 12–26 skills, 4–6 roles with 4–7 requirements each."
)


def synth_events(events: list[AnalysisEvent]) -> list[tuple[int, int | None]]:
    return [(e.step, e.chars) for e in events if isinstance(e, SynthEvent)]


def logs(events: list[AnalysisEvent]) -> list[str]:
    return [e.text for e in events if isinstance(e, LogEvent)]


def reporting(
    *chars: int, then: dict[str, Any] | None = None
) -> Callable[[StructuredCall], StructuredResult]:
    """A scripted answer that reports progress (as the client does while streaming), then returns ``then``.

    It returns a `StructuredResult` so the fake adds no progress report of its own after the last one.
    """

    def answer(call: StructuredCall) -> StructuredResult:
        assert call.on_progress is not None
        for n in chars:
            call.on_progress(n)
        data = then if then is not None else synth_output(["resume"])
        return StructuredResult(data=data, usage=fake_usage(call.model))

    return answer


# ───────────────────────── the gate ─────────────────────────


def test_graph_size_gate_boundaries() -> None:
    assert graph_size_problem(synth_output(["resume"])) is None
    exactly = {"domains": [1, 2], "skills": [1, 2, 3, 4], "roles": [1]}
    assert graph_size_problem(exactly) is None
    assert graph_size_problem({**exactly, "domains": [1]}) == (
        "structured output failed validation: graph too small (1 domains, 4 skills, 1 roles)"
    )
    assert graph_size_problem({**exactly, "skills": [1, 2, 3]}) is not None
    assert graph_size_problem({**exactly, "roles": []}) is not None
    assert (
        graph_size_problem({})
        == "structured output failed validation: graph too small (0 domains, 0 skills, 0 roles)"
    )
    assert graph_size_problem({"domains": None, "skills": None, "roles": None}) is not None


def test_the_repair_note_is_the_legacy_text() -> None:
    problem = graph_size_problem(tiny_synth())
    assert problem is not None
    assert repair_note(problem) == LEGACY_REPAIR.format(reason=problem)


def test_a_long_reason_is_cut_in_the_repair_note() -> None:
    note = repair_note("x" * 1000)
    assert "x" * 300 not in note
    assert "…[trimmed]" in note


# ───────────────────────── progress ─────────────────────────


async def test_step_two_then_step_three_with_their_log_lines(settings: Settings) -> None:
    claude = FakeClaudeClient().script_structured("synth", reporting(100, 2400, 2500, 4000, 6500, 9000))
    run, events = make_run(settings, claude=claude)
    await synthesize(run, PROMPT)
    steps = synth_events(events)
    assert (2, 2500) in steps
    assert (3, 6500) in steps
    assert steps.index((2, 2500)) < steps.index((3, 6500))
    texts = logs(events)
    assert texts.index("Scoring domains and skills") < texts.index("Ranking next roles by alignment")
    # nothing before step 2 is a step change; nothing goes back down
    assert [s for s, _ in steps] == sorted(s for s, _ in steps)


async def test_one_big_jump_reports_step_two_first(settings: Settings) -> None:
    claude = FakeClaudeClient().script_structured("synth", reporting(8000))
    run, events = make_run(settings, claude=claude)
    await synthesize(run, PROMPT)
    # legacy behaviour: a single report past 6,500 only crosses step 2; the closing step 3 is the service's
    assert synth_events(events)[0] == (2, 8000)


async def test_repeated_character_counts_are_not_reported_twice(settings: Settings) -> None:
    claude = FakeClaudeClient().script_structured("synth", reporting(300, 300, 300, 300))
    run, events = make_run(settings, claude=claude)
    await synthesize(run, PROMPT)
    counts = [chars for _, chars in synth_events(events) if chars is not None and chars < STEP2_CHARS]
    assert counts.count(300) == 1


async def test_in_between_character_counts_are_throttled(make_settings: Callable[..., Settings]) -> None:
    clock = FakeClock()
    claude = FakeClaudeClient()

    def answer(call: StructuredCall) -> dict[str, Any]:
        assert call.on_progress is not None
        call.on_progress(100)
        clock.advance(0.1)
        call.on_progress(200)  # 0.1 s later: held back (0.3 s throttle)
        clock.advance(0.3)
        call.on_progress(300)  # 0.4 s after the first: sent
        return synth_output(["resume"])

    claude.script_structured("synth", answer)
    run, events = make_run(make_settings(pacing_scale=1.0), clock=clock, claude=claude)
    await synthesize(run, PROMPT)
    counts = [chars for _, chars in synth_events(events) if chars is not None and chars < STEP2_CHARS]
    assert 100 in counts and 300 in counts
    assert 200 not in counts


async def test_the_counter_stays_monotonic_across_the_repair_call(settings: Settings) -> None:
    claude = FakeClaudeClient()
    claude.script_structured("synth", reporting(3000, 7000, then=tiny_synth()))
    claude.script_structured("synth", reporting(100, 500, then=synth_output(["resume"])))
    run, events = make_run(settings, claude=claude)
    await synthesize(run, PROMPT)
    chars = [c for _, c in synth_events(events) if c is not None]
    assert chars == sorted(chars)
    assert max(chars) >= 7000 + 500  # the second call's counts are added to the first call's last count


# ───────────────────────── one repair pass ─────────────────────────


async def test_a_graph_that_is_too_small_gets_one_repair_pass(settings: Settings) -> None:
    good = synth_output(["resume"])
    claude = FakeClaudeClient()
    claude.script_structured("synth", tiny_synth(), good)
    run, events = make_run(settings, claude=claude)
    assert await synthesize(run, PROMPT) == good
    first, second = claude.structured_calls
    assert [b.text for b in first.user] == [PROMPT]
    problem = graph_size_problem(tiny_synth())
    assert problem is not None
    assert [b.text for b in second.user] == [PROMPT, repair_note(problem)]
    assert logs(events).count(TIGHTENING_LOG) == 1
    assert TIGHTENING_LOG == "Tightening the graph, one more pass"
    only_logs = [e for e in events if isinstance(e, LogEvent)]
    assert all(e.who == "graph" and e.kind == "sonnet" for e in only_logs)


async def test_a_good_first_graph_is_one_call_and_no_tightening_line(settings: Settings) -> None:
    claude = FakeClaudeClient().script_structured("synth", synth_output(["resume"]))
    run, events = make_run(settings, claude=claude)
    await synthesize(run, PROMPT)
    assert len(claude.structured_calls) == 1
    assert TIGHTENING_LOG not in logs(events)


async def test_too_small_twice_is_an_incomplete_answer_and_no_third_call(settings: Settings) -> None:
    claude = FakeClaudeClient().script_structured("synth", tiny_synth(), tiny_synth())
    run, _ = make_run(settings, claude=claude)
    with pytest.raises(ClaudeInvalidOutput) as info:
        await synthesize(run, PROMPT)
    assert info.value.user_message == "The model returned an incomplete answer. Try again."
    assert "graph too small" in info.value.issues[0]
    assert len(claude.structured_calls) == 2


@pytest.mark.parametrize("error", [ClaudeOverloaded(), ClaudeTruncated(), ClaudeInvalidOutput(["bad json"])])
async def test_a_failed_call_is_not_retried_here(settings: Settings, error: Exception) -> None:
    claude = FakeClaudeClient().script_structured("synth", error)
    run, events = make_run(settings, claude=claude)
    with pytest.raises(type(error)):
        await synthesize(run, PROMPT)
    assert len(claude.structured_calls) == 1  # the client already retried what is worth retrying
    assert TIGHTENING_LOG not in logs(events)


async def test_a_failure_in_the_repair_call_propagates(settings: Settings) -> None:
    claude = FakeClaudeClient().script_structured("synth", tiny_synth(), ClaudeOverloaded())
    run, _ = make_run(settings, claude=claude)
    with pytest.raises(ClaudeOverloaded):
        await synthesize(run, PROMPT)


# ───────────────────────── the request ─────────────────────────


async def test_the_request_uses_the_synth_settings(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(synth_model="claude-sonnet-5-5", synth_effort="high", synth_max_tokens=12345)
    claude = FakeClaudeClient().script_structured("synth", synth_output(["resume"]))
    run, _ = make_run(settings, claude=claude)
    await synthesize(run, PROMPT)
    (call,) = claude.structured_calls
    assert call.model == "claude-sonnet-5-5"
    assert (call.effort, call.max_tokens) == ("high", 12345)
    assert call.schema is SYNTH_SCHEMA
    assert call.pdf is None
    assert [b.text for b in call.system] == [SYNTH_SYSTEM]
    ctx = call.ctx
    assert (ctx.purpose, ctx.request_id, ctx.profile_id, ctx.ip_hash) == (
        "synth",
        "req-test",
        "profile-test",
        "iphash0123456789",
    )
    assert ctx.analysis_id is not None


@pytest.mark.parametrize(
    ("mode", "system_cached", "data_cached"),
    [("off", False, False), ("system", True, False), ("system+data", True, True)],
)
async def test_the_cache_setting_places_the_breakpoints(
    make_settings: Callable[..., Settings], mode: str, system_cached: bool, data_cached: bool
) -> None:
    claude = FakeClaudeClient().script_structured("synth", tiny_synth(), synth_output(["resume"]))
    run, _ = make_run(make_settings(synth_cache=mode), claude=claude)
    await synthesize(run, PROMPT)
    first, second = claude.structured_calls
    assert [b.cache for b in first.system] == [system_cached]
    assert [b.cache for b in first.user] == [data_cached]
    # the repair note follows the cached data block, so a retry reads the prefix from the cache
    assert [b.cache for b in second.user] == [data_cached, False]
