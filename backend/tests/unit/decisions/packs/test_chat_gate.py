"""The chat web-search gate: the direction of the rule, the code shortcuts, the thresholds and fail-open."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tayyari.core.limits import JEV_GATE_MESSAGE_CHARS
from tayyari.decisions.packs.chat_gate import (
    CHAT_GATE_PACK,
    SPEC_ID,
    WEB_GATE_SPEC,
    WITHHOLD_AT_OR_BELOW,
    WITHHOLD_MIN_CONFIDENCE,
    build_state,
    has_link,
)
from tayyari.decisions.service import GateInput
from tayyari.llm.errors import JevError
from tests.fakes.jev import noul_answer
from tests.unit.decisions.packs.support import CLAUDE_NO, CLAUDE_YES, CTX, PackHarness

Harness = Callable[..., PackHarness]

ASK = GateInput("What is my strongest skill?", "you: Your profile")


# ───────────────────────── the spec and the state ─────────────────────────


def test_the_gate_is_one_noul() -> None:
    assert [s.id for s in CHAT_GATE_PACK.specs] == [SPEC_ID] == ["chat.web_gate"]
    assert WEB_GATE_SPEC.pack == "chat_gate" and WEB_GATE_SPEC.question.kind == "noul"
    assert "outside" in str(WEB_GATE_SPEC.question.instructions)
    assert set(WEB_GATE_SPEC.question.criteria or {}) == {"true", "false"}
    assert (WITHHOLD_AT_OR_BELOW, WITHHOLD_MIN_CONFIDENCE) == (0.15, 0.7)


def test_the_state_is_the_message_and_the_thread() -> None:
    assert build_state(ASK) == {"message": "What is my strongest skill?", "thread": "you: Your profile"}


def test_a_long_message_is_clipped() -> None:
    state = build_state(GateInput("m" * 5000, "t" * 900))
    assert len(state["message"]) == JEV_GATE_MESSAGE_CHARS
    assert len(state["thread"]) == 300


@pytest.mark.parametrize(
    ("message", "link"),
    [
        ("Can you read https://example.com/jobs/123 for me?", True),
        ("see http://localhost:3000", True),
        ("my site www.example.org has it", True),
        ("my portfolio is at janedoe.dev/work", True),
        ("check github.com/janedoe", True),
        ("look at my-site.io", True),
        ("What is my strongest skill?", False),
        ("I use Node.js and Vue.js daily", False),
        ("e.g. React, i.e. the library", False),
        ("Is 3.5 years enough?", False),
    ],
)
def test_links_are_found_by_the_code(message: str, link: bool) -> None:
    assert has_link(message) is link


# ───────────────────────── the direction of the rule ─────────────────────────


@pytest.mark.parametrize(
    ("p", "allow"),
    [
        (0.0, False),
        (0.02, False),
        (0.1, False),
        (0.15, False),  # withheld only at p <= 0.15 (confidence 0.7)
        (0.16, True),
        (0.2, True),
        (0.5, True),
        (0.85, True),  # confidence 0.7 too, but the wrong direction: needs outside info
        (0.95, True),
        (1.0, True),
    ],
)
async def test_tools_are_withheld_only_for_a_sure_no(harness: Harness, p: float, allow: bool) -> None:
    h = harness({SPEC_ID: noul_answer(p)}, with_claude=False)
    result = await h.service.chat_web_gate(ASK, CTX)

    assert result.allow_tools is allow
    assert result.p_outside == pytest.approx(p)


async def test_withheld_tools_say_why(harness: Harness) -> None:
    result = await harness({SPEC_ID: noul_answer(0.03)}).service.chat_web_gate(ASK, CTX)
    assert result.allow_tools is False
    assert result.reason == "answerable from the profile"
    assert result.p_outside == pytest.approx(0.03)


async def test_a_message_that_needs_the_web_allows_tools(harness: Harness) -> None:
    result = await harness({SPEC_ID: noul_answer(0.97)}).service.chat_web_gate(
        GateInput("What data analyst roles are open in Pune right now?", "role: Data Analyst"), CTX
    )
    assert result.allow_tools is True and result.p_outside == pytest.approx(0.97)
    assert result.reason == "may need outside information"


async def test_one_jev_call_with_the_state(harness: Harness) -> None:
    h = harness({SPEC_ID: noul_answer(0.5)}, with_claude=False)
    await h.service.chat_web_gate(ASK, CTX)

    (call,) = h.jev.calls
    assert call.question_ids == [SPEC_ID]
    assert call.state == {"message": ASK.message, "thread": ASK.thread}
    assert call.timeout_s == 2.0


async def test_the_decision_log_row(harness: Harness) -> None:
    h = harness({SPEC_ID: noul_answer(0.03)})
    await h.service.chat_web_gate(ASK, CTX)
    await h.drain()
    (row,) = h.rows
    assert row.spec_id == SPEC_ID and row.pack == "chat_gate" and row.used is True
    assert row.threshold == 0.7


# ───────────────────────── a lowered threshold cannot weaken the rule ─────────────────────────


async def test_a_lowered_configured_threshold_does_not_withhold_an_unsure_message(harness: Harness) -> None:
    """The runner would call p=0.25 (confidence 0.5) used at a threshold of 0.4; the pack still allows tools."""
    h = harness({SPEC_ID: noul_answer(0.25)}, overrides={SPEC_ID: {"threshold": 0.4}}, with_claude=False)
    result = await h.service.chat_web_gate(ASK, CTX)
    assert result.allow_tools is True
    assert result.p_outside == pytest.approx(0.25)


async def test_not_sure_enough_to_withhold_is_reported(harness: Harness) -> None:
    h = harness({SPEC_ID: noul_answer(0.3)}, with_claude=False)  # confidence 0.4 < 0.7, no fallback
    result = await h.service.chat_web_gate(ASK, CTX)
    assert result.allow_tools is True and result.p_outside == pytest.approx(0.3)


# ───────────────────────── code first ─────────────────────────


async def test_a_link_allows_tools_without_asking_jev(harness: Harness) -> None:
    h = harness({SPEC_ID: noul_answer(0.0)})
    result = await h.service.chat_web_gate(
        GateInput("Please read https://example.com/post", "free: Chat"), CTX
    )
    assert result.allow_tools is True and "link" in result.reason
    assert h.jev.calls == []


async def test_an_empty_message_asks_nothing(harness: Harness) -> None:
    h = harness({SPEC_ID: noul_answer(0.0)})
    result = await h.service.chat_web_gate(GateInput("   ", "free: Chat"), CTX)
    assert result.allow_tools is True
    assert h.jev.calls == []


# ───────────────────────── the Claude fallback ─────────────────────────


async def test_a_sure_claude_no_can_withhold_tools_when_jev_is_unsure(harness: Harness) -> None:
    h = harness({SPEC_ID: noul_answer(0.4)})  # confidence 0.2: Claude is asked
    h.claude.script_structured("decision_fallback", CLAUDE_NO)  # type: ignore[union-attr]
    result = await h.service.chat_web_gate(ASK, CTX)

    assert result.allow_tools is False
    assert result.p_outside == pytest.approx(0.05)
    assert len(h.fallback_calls) == 1


async def test_a_claude_yes_allows_tools(harness: Harness) -> None:
    h = harness({SPEC_ID: noul_answer(0.4)})
    h.claude.script_structured("decision_fallback", CLAUDE_YES)  # type: ignore[union-attr]
    result = await h.service.chat_web_gate(ASK, CTX)
    assert result.allow_tools is True and result.p_outside == pytest.approx(0.95)


async def test_a_medium_claude_no_is_not_sure_enough(harness: Harness) -> None:
    h = harness({SPEC_ID: noul_answer(0.4)})
    h.claude.script_structured("decision_fallback", {"answer": False, "confidence": "medium"})  # type: ignore[union-attr]
    result = await h.service.chat_web_gate(ASK, CTX)
    assert result.allow_tools is True  # confidence 0.6 < 0.7


# ───────────────────────── fail open ─────────────────────────


@pytest.mark.parametrize("kind", ["timeout", "connection", "server", "rate_limit", "auth", "bad_request"])
async def test_any_jev_error_allows_tools(harness: Harness, kind: str) -> None:
    h = harness({SPEC_ID: noul_answer(0.0)}, with_claude=False)
    h.jev.fail_next(JevError(kind))  # type: ignore[arg-type]
    result = await h.service.chat_web_gate(ASK, CTX)
    assert result.allow_tools is True
    assert result.p_outside is None
    assert result.reason == "no answer, tools allowed"


async def test_a_jev_error_with_a_failing_claude_allows_tools(harness: Harness) -> None:
    h = harness({SPEC_ID: noul_answer(0.0)})
    h.jev.fail_next(JevError("server"))
    # nothing scripted for the fallback: the fake raises, the runner treats it as a failed fallback
    assert (await h.service.chat_web_gate(ASK, CTX)).allow_tools is True


async def test_a_disabled_jev_allows_tools(harness: Harness) -> None:
    h = harness({SPEC_ID: noul_answer(0.0)}, jev_enabled=False, with_claude=False)
    assert (await h.service.chat_web_gate(ASK, CTX)).allow_tools is True


async def test_a_spend_trip_allows_tools(harness: Harness) -> None:
    from tests.unit.decisions.support import SpendTrip

    h = harness({SPEC_ID: noul_answer(0.0)}, spend=SpendTrip())
    assert (await h.service.chat_web_gate(ASK, CTX)).allow_tools is True
    assert h.jev.calls == []


async def test_the_overall_budget_allows_tools_when_jev_is_slow(harness: Harness) -> None:
    h = harness({SPEC_ID: noul_answer(0.0)}, slow_jev_s=5.0)
    h.service._gate_budget_s = 0.05
    result = await h.service.chat_web_gate(ASK, CTX)
    assert result.allow_tools is True and "timed out" in result.reason


async def test_an_open_breaker_allows_tools_without_asking(harness: Harness) -> None:
    h = harness({SPEC_ID: noul_answer(0.0)}, with_claude=False, breaker_failures=1)
    h.jev.fail_next(JevError("server"))
    await h.service.chat_web_gate(ASK, CTX)
    before = len(h.jev.calls)
    assert (await h.service.chat_web_gate(ASK, CTX)).allow_tools is True
    assert len(h.jev.calls) == before


async def test_a_stricter_configured_threshold_stops_the_withholding(harness: Harness) -> None:
    """Raising the threshold above Jev's confidence makes the answer unused: tools stay on, and the reason says why."""
    h = harness({SPEC_ID: noul_answer(0.05)}, overrides={SPEC_ID: {"threshold": 0.95}}, with_claude=False)
    result = await h.service.chat_web_gate(ASK, CTX)
    assert result.allow_tools is True
    assert result.reason == "not sure enough to withhold tools"
    assert result.p_outside == pytest.approx(0.05)
