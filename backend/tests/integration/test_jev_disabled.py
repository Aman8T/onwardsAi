"""With Jev switched off nothing about the product breaks: an analysis completes without the Jev lines, the
first-read chips and the evidence summary line, chat keeps its web tools, and no Jev call or decision row is made.

Two ways to be without Jev: the kill switch (``jev.enabled = false``, even with a client injected), and the
default settings of a deployment that has no ``JEV_API_KEY``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tayyari.contract import LogEvent, ToolEvent
from tayyari.core.settings import JevSettings, Settings
from tayyari.llm.errors import JevError
from tayyari.llm.types import ToolUse
from tayyari.storage.tables import DecisionLogRow, DecisionStateRow, JevCallRow
from tests.fakes.claude import StructuredCall, text_turn
from tests.fakes.jev import FakeJev
from tests.integration.support import (
    JevScript,
    RecordingClaude,
    analysis_events,
    assert_skeleton,
    chat_events,
    done_of,
    index_of,
    logs_by,
    running,
    sample_readers,
    sample_synth,
    signals_from,
    source_id_of,
    types_of,
)

MakeSettings = Callable[..., Settings]


@pytest.mark.parametrize("how", ["kill_switch", "no_key"])
async def test_analysis_and_chat_work_without_jev(build_settings: MakeSettings, how: str) -> None:
    if how == "kill_switch":
        settings = build_settings(jev=JevSettings(enabled=False))
        jev: FakeJev | None = FakeJev(JevScript())  # injected, yet it must never be called
    else:
        settings = build_settings()  # defaults: enabled, but no JEV_API_KEY and no client
        jev = None

    readers = sample_readers()
    claude = RecordingClaude()

    def reader(call: StructuredCall) -> dict[str, Any]:
        return readers[source_id_of(call)]

    claude.script_structured("reader", reader, repeat=True)
    claude.script_structured("synth", lambda _call: sample_synth(), repeat=True)
    claude.script_chat(text_turn("Try these.", tools=[ToolUse("WebSearch", "data jobs")]))

    async with running(settings, claude=claude, jev=jev) as env:
        assert env.container.jev_active is False
        analysis = await env.analyze(sample=True)
        events = analysis_events(analysis)
        chat = await env.chat("What should I learn next?")
        chat_stream = chat_events(chat)
        await env.settle()
        jev_calls = await env.rows(JevCallRow)
        decisions = await env.rows(DecisionLogRow)
        states = await env.rows(DecisionStateRow)
        spend = await env.spend()

    # the analysis is complete and well ordered, just without anything from Jev
    assert analysis.status_code == 200
    assert_skeleton(events)
    assert logs_by(events, "Jev") == []
    assert signals_from(events, "first read") == []
    assert not any(isinstance(e, LogEvent) and "evidence quote" in e.text for e in events)
    graph_ready = index_of(events, lambda e: isinstance(e, LogEvent) and e.text.startswith("Graph ready"))
    assert types_of(events[graph_ready:]) == ["log", "synth", "phase", "done"]
    assert len(claude.calls_for("reader")) == 4 and len(claude.calls_for("synth")) == 1

    # chat works, with its web tools allowed (the gate is not consulted)
    assert chat.status_code == 200
    assert types_of(chat_stream) == ["tool", "delta", "done"]
    tool = chat_stream[0]
    assert isinstance(tool, ToolEvent) and tool.name == "WebSearch"
    assert claude.chat_calls[0].tools == "web"

    # no Claude fallback, no Jev request, nothing in the decision log, no Jev spend
    assert claude.calls_for("decision_fallback") == []
    if jev is not None:
        assert jev.calls == []
    assert (jev_calls, decisions, states) == ([], [], [])
    assert "jev" not in spend
    assert spend["total"] == spend["anthropic"]


async def test_a_dead_jev_costs_an_analysis_nothing_but_its_extras(build_settings: MakeSettings) -> None:
    """Jev enabled but failing on every request: the analysis still finishes and only the lines Jev makes are
    missing. Questions with a Claude fallback (triage, evidence) are answered by Claude; the first read, which
    has none (it could not meet its deadline), simply shows nothing."""
    readers = sample_readers()
    claude = RecordingClaude()
    claude.script_structured("reader", lambda call: readers[source_id_of(call)], repeat=True)
    claude.script_structured("synth", lambda _call: sample_synth(), repeat=True)
    claude.script_structured("decision_fallback", _harmless_fallback, repeat=True)
    jev = FakeJev(JevScript())
    jev.fail_with(JevError("server", "Jev is down"))

    async with running(build_settings(), claude=claude, jev=jev) as env:
        response = await env.analyze(sample=True)
        events = analysis_events(response)
        await env.settle()
        jev_rows = await env.rows(JevCallRow)
        decisions = await env.rows(DecisionLogRow)

    assert response.status_code == 200
    assert_skeleton(events)
    assert signals_from(events, "first read") == []
    assert [e for e in logs_by(events, "Jev") if e.text.startswith(("First read", "Depth"))] == []
    assert jev_rows and {r.status for r in jev_rows} == {"error"}  # every failed request is on record
    triage = [r for r in decisions if r.pack == "triage"]
    # Claude answered the triage questions that have a fallback; the language check (fallback = skip) stayed neutral
    assert {r.source_used for r in triage} == {"claude", "default"}
    assert {r.escalation_reason for r in triage} <= {"jev_error", "circuit_open"}
    assert len(claude.calls_for("decision_fallback")) >= len([r for r in triage if r.source_used == "claude"])
    done_of(events)


def _harmless_fallback(call: StructuredCall) -> dict[str, Any]:
    """Claude's answer to any decision question: the unremarkable one (readable, supported, no label)."""
    props = call.schema["properties"]
    if "choice" in props:
        options = props["choice"]["enum"]
        return {"choice": next((o for o in ("readable", "supports", "other") if o in options), options[0])}
    if "answer" in props:
        return {"answer": True, "confidence": "high"}
    return {"level": 2}
