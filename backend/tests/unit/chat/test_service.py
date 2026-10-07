"""`CoachChatService` against fakes: sessions, the tool gate, streaming, persistence and failures."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from tayyari.chat.context import CHAT_RULES, render_origin_block
from tayyari.chat.ports import ChatService
from tayyari.chat.service import (
    CUT_OFF_NOTE,
    PROFILE_NOT_FOUND_MESSAGE,
    RESET_NOTE,
    CoachChatService,
)
from tayyari.contract import ChatDoneEvent, ChatErrorEvent, ChatEvent, DeltaEvent, StoredContext, ToolEvent
from tayyari.core.limits import CHAT_HISTORY_MESSAGES
from tayyari.decisions.service import DecisionContext, GateInput, GateResult
from tayyari.llm.errors import (
    AUTH_MESSAGE,
    BUSY_MESSAGE,
    SPEND_CEILING_MESSAGE,
    ClaudeAuthError,
    ClaudeOverloaded,
    ClaudeRefusal,
    SpendCeilingReached,
)
from tayyari.llm.types import ChatStreamEvent, TextDelta, ToolUse, TurnEnd
from tayyari.storage.ports import ToolEvent as StoredToolEvent
from tests.fakes.claude import fake_usage, text_turn
from tests.unit.chat.support import (
    IP_HASH,
    OTHER_PROFILE_ID,
    PROFILE_ID,
    RecordingLog,
    RecordingProfiles,
    Rig,
)

SKILL_ORIGIN = {
    "kind": "skill",
    "refId": "motion-design",
    "title": "Motion & animation",
    "caption": "Frontend",
}


def deltas(events: Sequence[ChatEvent]) -> str:
    return "".join(e.text for e in events if isinstance(e, DeltaEvent))


def done_id(events: Sequence[ChatEvent]) -> str:
    done = [e for e in events if isinstance(e, ChatDoneEvent)]
    assert len(done) == 1 and events[-1] is done[0], f"expected one final done event, got {events!r}"
    return done[0].sessionId


def kinds(events: Sequence[ChatEvent]) -> list[str]:
    return [e.type for e in events]


# ───────────────────────── the port ─────────────────────────


def test_it_implements_the_chat_service_port(rig: Rig) -> None:
    assert isinstance(rig.service, ChatService)


# ───────────────────────── a new session ─────────────────────────


async def test_a_new_session_streams_the_answer_and_stores_the_exchange(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("Start with tests.", chunk_chars=6))

    events = await rig.run("How do I get better at testing?")

    assert kinds(events) == ["delta", "delta", "delta", "done"]
    assert deltas(events) == "Start with tests."
    sid = done_id(events)
    assert len(sid) == 32  # a fresh 128-bit id

    stored = await rig.stored(sid)
    assert [(m.seq, m.role, m.content) for m in stored.messages] == [
        (0, "user", "How do I get better at testing?"),
        (1, "assistant", "Start with tests."),
    ]
    assert stored.origin is None
    assert stored.turn_count == 1
    assert len(rig.sessions.creates) == 1 and rig.sessions.loads == []  # no sessionId: nothing to load


async def test_the_claude_call_carries_the_settings_the_blocks_and_the_context(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("ok"))

    events = await rig.run("Plan my next quarter", origin=SKILL_ORIGIN)

    (call,) = rig.claude.chat_calls
    assert call.model == rig.settings.chat_model
    assert call.effort == rig.settings.chat_effort
    assert call.max_tokens == rig.settings.chat_max_tokens
    assert call.cache_tail is True
    assert call.tools == "web"
    assert call.messages == ({"role": "user", "content": "Plan my next quarter"},)

    rules, profile_block, origin_block = call.system
    assert (rules.cache, profile_block.cache, origin_block.cache) == (False, True, False)
    assert rules.text == CHAT_RULES
    assert "Riya" not in rules.text
    assert profile_block.text.startswith(
        '## About the user\nTheir first name is Riya: address them as "Riya"'
    )
    assert "## Their profile (the graph they are looking at)" in profile_block.text
    assert origin_block.text == render_origin_block(SKILL_ORIGIN)
    assert 'clicking "Motion & animation"' in origin_block.text

    ctx = call.ctx
    assert ctx.purpose == "chat"
    assert ctx.request_id == "req-chat-1"
    assert ctx.profile_id == PROFILE_ID
    assert ctx.session_id == done_id(events)
    assert ctx.ip_hash == IP_HASH
    assert ctx.analysis_id is None


async def test_the_origin_is_stored_with_a_new_session(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("ok"))
    events = await rig.run("Plan", origin=SKILL_ORIGIN)
    assert (await rig.stored(done_id(events))).origin == SKILL_ORIGIN
    assert rig.sessions.creates == [(PROFILE_ID, SKILL_ORIGIN, IP_HASH)]


async def test_the_whole_text_comes_from_the_turn_end(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(
        [TextDelta("Hel"), TextDelta("lo"), TurnEnd(text="Hello", usage=fake_usage("claude-sonnet-5-5"))]
    )
    events = await rig.run()
    assert (await rig.stored(done_id(events))).messages[1].content == "Hello"


async def test_empty_text_deltas_are_not_sent(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(
        [
            TextDelta(""),
            TextDelta("Hi"),
            TextDelta(""),
            TurnEnd(text="Hi", usage=fake_usage("claude-sonnet-5-5")),
        ]
    )
    assert kinds(await rig.run()) == ["delta", "done"]


# ───────────────────────── a resumed session ─────────────────────────


async def test_a_resumed_session_replays_the_history_in_order(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(
        text_turn("First answer."), text_turn("Second answer."), text_turn("Third answer.")
    )

    sid = done_id(await rig.run("first question"))
    assert done_id(await rig.run("second question", sessionId=sid)) == sid
    assert done_id(await rig.run("third question", sessionId=sid)) == sid

    first, second, third = rig.claude.chat_calls
    assert [m["content"] for m in first.messages] == ["first question"]
    assert list(second.messages) == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "First answer."},
        {"role": "user", "content": "second question"},
    ]
    assert [m["role"] for m in third.messages] == ["user", "assistant", "user", "assistant", "user"]
    assert [m["content"] for m in third.messages] == [
        "first question",
        "First answer.",
        "second question",
        "Second answer.",
        "third question",
    ]
    assert all(call.ctx.session_id == sid for call in (second, third))

    stored = await rig.stored(sid)
    assert [m.seq for m in stored.messages] == [0, 1, 2, 3, 4, 5]
    assert stored.turn_count == 3
    assert len(rig.sessions.creates) == 1  # resumed, not re-created
    assert RESET_NOTE not in second.messages[0]["content"]


async def test_the_history_is_the_last_forty_messages(stored_rig: Rig) -> None:
    rig = stored_rig
    assert CHAT_HISTORY_MESSAGES == 40
    sid = await rig.seed_turns(25)  # 50 messages: q0 a0 ... q24 a24
    rig.claude.script_chat(text_turn("answer"))

    assert done_id(await rig.run("one more", sessionId=sid)) == sid

    (call,) = rig.claude.chat_calls
    assert rig.sessions.loads == [(sid, PROFILE_ID, 40)]
    assert len(call.messages) == 41
    assert call.messages[0] == {"role": "user", "content": "q5"}  # messages 0..9 fell off the front
    assert call.messages[39] == {"role": "assistant", "content": "a24"}
    assert call.messages[40] == {"role": "user", "content": "one more"}
    stored = await rig.stored(sid)
    assert len(stored.messages) == 52  # nothing is deleted from the store, only the replay is capped


async def test_a_window_that_starts_on_a_reply_is_trimmed_to_start_on_a_question(stored_rig: Rig) -> None:
    """20 exchanges and a turn with no visible text make 41 messages: the last 40 would start with an answer."""
    rig = stored_rig
    sid = await rig.seed_turns(20)
    await rig.fakes.sessions.append_turn(sid, user="tools only", assistant=None, tools=[], llm_call_id=None)
    rig.claude.script_chat(text_turn("answer"))

    await rig.run("next", sessionId=sid)

    (call,) = rig.claude.chat_calls
    assert call.messages[0] == {"role": "user", "content": "q1"}
    assert [m["role"] for m in call.messages][:2] == ["user", "assistant"]
    assert len(call.messages) == 40  # 39 history messages (a0 dropped) and the new one
    assert call.messages[-2] == {"role": "user", "content": "tools only"}
    assert call.messages[-1] == {"role": "user", "content": "next"}


async def test_a_resumed_session_without_an_origin_keeps_the_threads_origin(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("one"), text_turn("two"))
    sid = done_id(await rig.run("first", origin=SKILL_ORIGIN))
    await rig.run("second", sessionId=sid)
    assert rig.claude.chat_calls[1].system[2].text == render_origin_block(SKILL_ORIGIN)
    assert rig.decisions.gate_calls[1][0].thread == "skill: Motion & animation"


async def test_a_new_origin_on_a_follow_up_wins(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("one"), text_turn("two"))
    sid = done_id(await rig.run("first", origin=SKILL_ORIGIN))
    other = {"kind": "role", "title": "Staff Engineer", "caption": ""}
    await rig.run("second", sessionId=sid, origin=other)
    assert rig.claude.chat_calls[1].system[2].text == render_origin_block(other)


# ───────────────────────── unknown and foreign sessions ─────────────────────────


async def test_an_unknown_session_id_starts_fresh_with_the_reset_note(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("answer"))

    events = await rig.run("where was I?", sessionId="0123456789abcdef0123456789abcdef")

    (call,) = rig.claude.chat_calls
    assert list(call.messages) == [{"role": "user", "content": RESET_NOTE + "where was I?"}]
    sid = done_id(events)
    assert sid != "0123456789abcdef0123456789abcdef"
    assert call.ctx.session_id == sid
    stored = await rig.stored(sid)
    assert stored.messages[0].content == RESET_NOTE + "where was I?"  # replayed unchanged on the next turn
    assert stored.messages[1].content == "answer"
    assert rig.sessions.loads == [("0123456789abcdef0123456789abcdef", PROFILE_ID, 40)]


async def test_the_next_turn_of_a_reset_thread_has_no_second_note(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("one"), text_turn("two"))
    sid = done_id(await rig.run("first", sessionId="gone"))
    await rig.run("second", sessionId=sid)
    messages = rig.claude.chat_calls[1].messages
    assert messages[0]["content"] == RESET_NOTE + "first"
    assert messages[2]["content"] == "second"


async def test_a_session_of_another_profile_is_treated_as_unknown(stored_rig: Rig) -> None:
    rig = stored_rig
    await rig.save_profile(OTHER_PROFILE_ID)
    foreign = await rig.seed_turns(2, profile_id=OTHER_PROFILE_ID)
    rig.claude.script_chat(text_turn("answer"))

    events = await rig.run("hello", sessionId=foreign)

    (call,) = rig.claude.chat_calls
    assert list(call.messages) == [{"role": "user", "content": RESET_NOTE + "hello"}]  # no history leaked
    sid = done_id(events)
    assert sid != foreign
    assert len((await rig.stored(foreign, OTHER_PROFILE_ID)).messages) == 4  # the other thread is untouched
    assert (await rig.stored(sid)).profile_id == PROFILE_ID


async def test_no_session_id_means_no_reset_note(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("answer"))
    await rig.run("plain")
    assert rig.claude.chat_calls[0].messages[0]["content"] == "plain"


# ───────────────────────── the profile ─────────────────────────


async def test_a_missing_profile_is_the_legacy_error_text(rig: Rig) -> None:
    events = await rig.run(profileId="not-a-profile")

    assert events == [ChatErrorEvent(message="Profile not found — run the analysis again")]
    assert PROFILE_NOT_FOUND_MESSAGE == "Profile not found — run the analysis again"
    assert rig.claude.chat_calls == []
    assert rig.decisions.gate_calls == []
    assert rig.sessions.creates == [] and rig.sessions.loads == []
    assert rig.profiles.saves == []  # only the bundled sample id is ever seeded


async def test_the_sample_profile_is_seeded_on_its_first_chat(rig: Rig) -> None:
    sample_profile = rig.sample.sample_profile
    assert sample_profile is not None
    sample_id = sample_profile.id
    assert await rig.fakes.profiles.get_profile(sample_id) is None
    rig.claude.script_chat(text_turn("hello"), text_turn("again"))

    events = await rig.run("Tell me about my graph", profileId=sample_id)

    sid = done_id(events)
    (save,) = rig.profiles.saves
    assert save["profile_id"] == sample_id
    assert save["is_sample"] is True
    assert save["owner_hash"] is None
    assert save["goal"] == sample_profile.goal
    assert save["context"] == rig.sample.sample_context
    assert save["profile"]["id"] == sample_id
    stored_profile = await rig.fakes.profiles.get_profile(sample_id)
    assert stored_profile is not None and stored_profile["person"]["name"] == "Riya Kapoor"
    assert (await rig.stored(sid, sample_id)).profile_id == sample_id  # the session's foreign key holds
    assert '"Riya"' in rig.claude.chat_calls[0].system[1].text

    await rig.run("And again", profileId=sample_id, sessionId=sid)
    assert len(rig.profiles.saves) == 1  # seeded once


async def test_the_sample_profile_is_marked_as_a_sample_for_the_decision_log(rig: Rig) -> None:
    sample_id = rig.sample.sample_profile_id()
    rig.claude.script_chat(text_turn("hello"))
    await rig.run("Tell me about my graph", profileId=sample_id)
    assert rig.decisions.gate_calls[0][1].is_sample is True


async def test_a_sample_run_id_is_a_sample_too(rig: Rig) -> None:
    await rig.save_profile("sample-" + "ab" * 16)
    rig.claude.script_chat(text_turn("hello"))
    await rig.run("hi", profileId="sample-" + "ab" * 16)
    assert rig.decisions.gate_calls[0][1].is_sample is True
    assert rig.profiles.saves == []  # it was already stored: nothing is seeded


async def test_a_failing_sample_seed_does_not_fail_the_turn(rig: Rig) -> None:
    sample_id = rig.sample.sample_profile_id()
    rig.profiles.fail_save = RuntimeError("database is down")
    rig.claude.script_chat(text_turn("hello"))

    events = await rig.run("Tell me about my graph", profileId=sample_id)

    assert kinds(events)[-1] == "done"
    assert "Riya" in rig.claude.chat_calls[0].system[1].text  # the in-memory sample is used


class SortedKeyProfiles(RecordingProfiles):
    """A profile store that returns documents with their keys sorted, as Postgres JSONB does."""

    async def get_for_chat(self, pid: str) -> tuple[dict[str, Any], StoredContext] | None:
        found = await super().get_for_chat(pid)
        if found is None:
            return None
        profile, context = found
        return (
            json.loads(json.dumps(profile, sort_keys=True)),
            StoredContext.model_validate(json.loads(json.dumps(context.to_dict(), sort_keys=True))),
        )


async def test_the_sample_prompt_is_the_same_before_and_after_the_store_round_trip(rig: Rig) -> None:
    """The seed is read back, so turn 1 renders the stored document exactly like every later turn.

    A store that reorders keys (Postgres JSONB sorts them) would otherwise give turn 1 a different profile
    block from turn 2, and the second turn would miss the cache for no reason."""
    sample_id = rig.sample.sample_profile_id()
    service = CoachChatService(
        settings=rig.settings,
        claude=rig.claude,
        decisions=rig.decisions,
        profiles=SortedKeyProfiles(rig.fakes.profiles),
        sessions=rig.sessions,
        sample=rig.sample,
        clock=rig.clock,
    )
    rig.claude.script_chat(text_turn("one"), text_turn("two"))

    async def run(message: str, **fields: Any) -> list[ChatEvent]:
        return [
            e async for e in service.stream_turn(rig.request(message, profileId=sample_id, **fields), rig.rc)
        ]

    sid = done_id(await run("first"))
    service.renderer.clear()  # another replica, or this one after a restart, renders the stored document afresh
    await run("second", sessionId=sid)

    first, second = rig.claude.chat_calls
    assert first.system[:2] == second.system[:2]
    # sanity: the document as the sample provider spells it is not what the store returns
    document = rig.sample.sample_profile.to_dict()  # type: ignore[union-attr]
    assert list(document["person"]) != sorted(document["person"])


# ───────────────────────── the tool gate ─────────────────────────


async def test_the_gate_is_asked_with_the_message_and_the_thread(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("ok"))

    await rig.run("What is my strongest skill?", origin=SKILL_ORIGIN)

    ((inp, ctx),) = rig.decisions.gate_calls
    assert inp == GateInput(message="What is my strongest skill?", thread="skill: Motion & animation")
    assert ctx == DecisionContext(PROFILE_ID, None, False, "req-chat-1", purpose_tag="chat")


async def test_a_thread_without_an_origin_is_free_form_for_the_gate(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("ok"), text_turn("ok"))
    await rig.run("hello")
    await rig.run("hello", origin={"kind": "free", "title": "", "caption": ""})
    assert [inp.thread for inp, _ in rig.decisions.gate_calls] == ["free", "free"]


async def test_the_default_gate_allows_the_web_tools(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("ok"))
    await rig.run("What courses should I take?")
    assert rig.claude.chat_calls[0].tools == "web"


async def test_a_gate_that_says_no_turns_the_tools_off_but_not_the_prefix(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.decisions.gate_script = GateResult(allow_tools=False, reason="from the profile", p_outside=0.05)
    rig.claude.script_chat(text_turn("ok"), text_turn("ok"))

    await rig.run("What is my strongest skill?", origin=SKILL_ORIGIN)
    rig.decisions.gate_script = GateResult(allow_tools=True, reason="needs outside facts", p_outside=0.9)
    await rig.run("What is my strongest skill?", origin=SKILL_ORIGIN)

    none_call, web_call = rig.claude.chat_calls
    assert none_call.tools == "none"
    assert web_call.tools == "web"
    assert none_call.system == web_call.system  # the cached prefix does not depend on the gate


@pytest.mark.parametrize(
    "message",
    [
        "Read https://example.com/jobs/42 and tell me if I fit",
        "what do you think of github.com/ada/engine?",
        "my site is www.ada.dev",
    ],
)
async def test_a_link_forces_the_tools_on_without_asking_the_gate(stored_rig: Rig, message: str) -> None:
    rig = stored_rig
    rig.decisions.gate_script = GateResult(allow_tools=False, reason="would say no")
    rig.claude.script_chat(text_turn("ok"))

    await rig.run(message)

    assert rig.claude.chat_calls[0].tools == "web"
    assert rig.decisions.gate_calls == []


async def test_a_message_without_a_link_is_gated(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.decisions.gate_script = GateResult(allow_tools=False, reason="no")
    rig.claude.script_chat(text_turn("ok"))
    await rig.run("I know Node.js/React well, what next?")
    assert rig.claude.chat_calls[0].tools == "none"
    assert len(rig.decisions.gate_calls) == 1


async def test_the_gate_sees_the_message_without_the_reset_note(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("ok"))
    await rig.run("hello", sessionId="gone")
    assert rig.decisions.gate_calls[0][0].message == "hello"


async def test_a_broken_gate_fails_open(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.decisions.fail("chat_web_gate", RuntimeError("jev exploded"))
    rig.claude.script_chat(text_turn("ok"))
    events = await rig.run("What is my strongest skill?")
    assert kinds(events) == ["delta", "done"]
    assert rig.claude.chat_calls[0].tools == "web"


# ───────────────────────── tool events ─────────────────────────


async def test_tool_uses_are_streamed_and_stored_with_the_ui_names(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(
        text_turn(
            "Here is what I found.",
            tools=[
                ToolUse("WebSearch", "react testing courses"),
                ToolUse("WebFetch", "https://example.com/a"),
            ],
        )
    )

    events = await rig.run("Find me a course")

    assert kinds(events) == ["tool", "tool", "delta", "done"]
    assert events[0] == ToolEvent(name="WebSearch", detail="react testing courses")
    assert events[1] == ToolEvent(name="WebFetch", detail="https://example.com/a")
    stored = await rig.stored(done_id(events))
    assert stored.messages[1].content == "Here is what I found."
    assert stored.messages[1].tools == [
        StoredToolEvent("WebSearch", "react testing courses"),
        StoredToolEvent("WebFetch", "https://example.com/a"),
    ]
    assert stored.messages[0].tools == []


async def test_raw_server_tool_names_are_mapped_to_the_ui_names(stored_rig: Rig) -> None:
    """The live defect of the old port: web_search reached the UI, which only knows WebSearch."""
    rig = stored_rig
    rig.claude.script_chat(
        text_turn("x", tools=[ToolUse("web_search", "q"), ToolUse("web_fetch", "https://a.dev")])
    )
    events = await rig.run("go")
    assert [(e.name, e.detail) for e in events if isinstance(e, ToolEvent)] == [
        ("WebSearch", "q"),
        ("WebFetch", "https://a.dev"),
    ]


async def test_a_long_tool_detail_is_cut_to_200_characters(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("x", tools=[ToolUse("WebFetch", "https://a.dev/" + "p" * 500)]))
    events = await rig.run("go")
    tool = next(e for e in events if isinstance(e, ToolEvent))
    assert len(tool.detail) == 200
    assert (await rig.stored(done_id(events))).messages[1].tools[0].detail == tool.detail


async def test_a_turn_with_only_tools_is_stored_as_the_user_message_alone(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("", tools=[ToolUse("WebSearch", "q")]), text_turn("Answer."))

    events = await rig.run("search please")

    assert kinds(events) == ["tool", "done"]
    sid = done_id(events)
    stored = await rig.stored(sid)
    assert [(m.role, m.content) for m in stored.messages] == [("user", "search please")]
    assert stored.messages[0].tools == [StoredToolEvent("WebSearch", "q")]

    # the next turn replays that lone user message, then the new one (the API merges consecutive user turns)
    await rig.run("and then?", sessionId=sid)
    assert [m["role"] for m in rig.claude.chat_calls[1].messages] == ["user", "user"]


async def test_a_whitespace_only_answer_counts_as_no_text(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("  \n"))
    events = await rig.run("hello")
    assert [(m.role) for m in (await rig.stored(done_id(events))).messages] == ["user"]


# ───────────────────────── truncated answers ─────────────────────────


async def test_a_truncated_answer_gets_a_cut_off_note_and_still_ends_with_done(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("A long answer that", stop_reason="max_tokens", chunk_chars=100))

    events = await rig.run("tell me everything")

    assert kinds(events) == ["delta", "delta", "done"]
    assert events[0] == DeltaEvent(text="A long answer that")
    note = events[1]
    assert isinstance(note, DeltaEvent)
    assert note.text == CUT_OFF_NOTE
    assert "_(The answer was cut off — ask me to continue.)_" in note.text
    stored = await rig.stored(done_id(events))
    assert stored.messages[1].content == "A long answer that"  # the note is for the screen, not the history


async def test_a_normal_answer_has_no_note(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("Complete.", stop_reason="end_turn"))
    assert CUT_OFF_NOTE not in deltas(await rig.run("hello"))


async def test_a_pause_turn_ending_is_a_normal_ending(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("Partial but done.", stop_reason="pause_turn"))
    events = await rig.run("hello")
    assert deltas(events) == "Partial but done."


async def test_truncation_with_no_visible_text_still_explains_itself(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("", stop_reason="max_tokens"))
    events = await rig.run("hello")
    assert kinds(events) == ["delta", "done"]
    assert deltas(events) == CUT_OFF_NOTE
    assert [m.role for m in (await rig.stored(done_id(events))).messages] == ["user"]


# ───────────────────────── failures ─────────────────────────


async def test_a_refusal_is_an_error_event_with_the_friendly_message(stored_rig: Rig) -> None:
    rig = stored_rig
    refusal = ClaudeRefusal("cyber")
    rig.claude.script_chat(refusal)

    events = await rig.run("something sensitive")

    assert events == [ChatErrorEvent(message=refusal.user_message)]
    assert "security-sensitive" in refusal.user_message
    assert rig.sessions.appends == []


async def test_an_error_after_some_text_ends_the_stream_with_the_error(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat([TextDelta("Partial "), ToolUse("WebSearch", "q"), ClaudeOverloaded()])

    events = await rig.run("hello")

    assert kinds(events) == ["delta", "tool", "error"]
    assert events[-1] == ChatErrorEvent(message=BUSY_MESSAGE)
    assert rig.sessions.appends == []  # nothing half-finished is stored


async def test_the_spend_ceiling_is_an_error_event(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(SpendCeilingReached(3600))
    events = await rig.run("hello")
    assert events == [ChatErrorEvent(message=SPEND_CEILING_MESSAGE)]
    assert rig.sessions.appends == []


async def test_a_claude_error_message_is_the_users_message_not_the_providers(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(ClaudeAuthError(detail="invalid x-api-key sk-ant-secret"))
    events = await rig.run("hello")
    assert events == [ChatErrorEvent(message=AUTH_MESSAGE)]
    assert "sk-ant" not in events[0].model_dump_json()


async def test_an_unexpected_bug_is_not_swallowed(stored_rig: Rig) -> None:
    """Only expected failures become error events; a bug propagates so the HTTP layer logs its traceback."""
    rig = stored_rig
    rig.claude.script_chat(RuntimeError("bug"))
    with pytest.raises(RuntimeError, match="bug"):
        await rig.run("hello")
    assert rig.sessions.appends == []


async def test_the_logs_have_no_message_or_profile_text(stored_rig: Rig, logs: RecordingLog) -> None:
    rig = stored_rig
    rig.claude.script_chat(ClaudeOverloaded(), text_turn("fine"))
    await rig.run("my secret question about Riya")
    await rig.run("another secret question")
    assert logs.events() == ["chat_turn_failed", "chat_turn"]
    fields = logs.records[1][2]
    assert (
        fields["tools"] == "web" and fields["cache_read_tokens"] == 0 and fields["profile_id"] == PROFILE_ID
    )
    dump = repr(logs.records)
    for text in ("secret question", "Riya", "Kapoor", "Kitebird"):
        assert text not in dump


# ───────────────────────── persistence trouble ─────────────────────────


async def test_a_failure_to_store_the_turn_is_logged_and_does_not_fail_it(
    stored_rig: Rig, logs: RecordingLog
) -> None:
    rig = stored_rig
    rig.sessions.fail_append = RuntimeError("database is down")
    rig.claude.script_chat(text_turn("The answer."))

    events = await rig.run("hello")

    assert deltas(events) == "The answer."
    assert kinds(events)[-1] == "done"
    assert "chat_turn_not_saved" in logs.events("warning")


async def test_losing_the_sequence_race_twice_does_not_fail_the_turn(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.fakes.sessions.conflicts = 2  # both attempts of append_turn lose, then SessionConflict is raised
    rig.claude.script_chat(text_turn("The answer."))

    events = await rig.run("hello")

    assert kinds(events)[-1] == "done"
    assert (await rig.stored(done_id(events))).messages == []


async def test_one_lost_race_is_retried_by_the_repository(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.fakes.sessions.conflicts = 1
    rig.claude.script_chat(text_turn("The answer."))
    events = await rig.run("hello")
    assert len((await rig.stored(done_id(events))).messages) == 2


async def test_a_failing_session_store_costs_the_history_not_the_answer(
    stored_rig: Rig, logs: RecordingLog
) -> None:
    rig = stored_rig
    sid = await rig.seed_turns(2)
    rig.sessions.fail_load = RuntimeError("database is down")
    rig.claude.script_chat(text_turn("The answer."))

    events = await rig.run("hello", sessionId=sid)

    assert deltas(events) == "The answer."
    new_sid = done_id(events)
    assert new_sid != sid
    assert list(rig.claude.chat_calls[0].messages) == [{"role": "user", "content": RESET_NOTE + "hello"}]
    assert "chat_session_load_failed" in logs.events("warning")


async def test_when_no_session_can_be_created_the_turn_runs_unsaved(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.sessions.fail_create = RuntimeError("database is down")
    rig.claude.script_chat(text_turn("The answer."))

    events = await rig.run("hello")

    assert deltas(events) == "The answer."
    sid = done_id(events)
    assert len(sid) == 32
    assert rig.claude.chat_calls[0].ctx.session_id == sid
    assert list(rig.claude.chat_calls[0].messages) == [{"role": "user", "content": "hello"}]  # no reset note
    assert rig.sessions.appends == []  # nothing to append to


# ───────────────────────── deterministic blocks ─────────────────────────


async def test_the_same_request_twice_sends_identical_blocks(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("a"), text_turn("b"))

    await rig.run("What next?", origin=SKILL_ORIGIN)
    await rig.run("What next?", origin=SKILL_ORIGIN)

    first, second = rig.claude.chat_calls
    assert first.system == second.system
    assert [b.text for b in first.system] == [b.text for b in second.system]
    assert [b.cache for b in first.system] == [False, True, False]
    assert rig.service.renderer.misses == 1 and rig.service.renderer.hits == 1


async def test_threads_of_one_profile_share_the_cached_prefix_and_differ_after_it(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("a"), text_turn("b"), text_turn("c"))

    await rig.run("What next?", origin=SKILL_ORIGIN)
    await rig.run("What next?", origin={"kind": "role", "title": "Staff Engineer", "caption": ""})
    await rig.run("What next?")

    a, b, c = rig.claude.chat_calls
    assert a.system[0] == b.system[0] == c.system[0]
    assert a.system[1] == b.system[1] == c.system[1]
    assert len({a.system[2].text, b.system[2].text, c.system[2].text}) == 3
    assert a.system[2].cache is False


async def test_a_changed_profile_is_rendered_again(stored_rig: Rig) -> None:
    rig = stored_rig
    rig.claude.script_chat(text_turn("a"), text_turn("b"))
    await rig.run("hello")

    stored = await rig.fakes.profiles.get_profile(PROFILE_ID)
    assert stored is not None
    stored["skills"][0]["score"] = 1
    await rig.fakes.profiles.save(
        PROFILE_ID, stored, rig.sample.sample_context, goal=None, owner_hash=None, is_sample=False
    )
    await rig.run("hello")

    first, second = rig.claude.chat_calls
    assert first.system[1].text != second.system[1].text
    assert first.system[0] == second.system[0]


# ───────────────────────── cancellation ─────────────────────────


class HangingClaude:
    """A Claude client whose stream sends one delta and then waits forever, remembering that it was closed."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = False
        self.session_id: str | None = None

    async def structured(self, **_: Any) -> Any:
        raise AssertionError("not used by chat")

    def stream_chat(self, **kwargs: Any) -> AsyncIterator[ChatStreamEvent]:
        self.session_id = kwargs["ctx"].session_id
        return self._stream()

    async def _stream(self) -> AsyncIterator[ChatStreamEvent]:
        try:
            yield TextDelta("Partial ")
            self.started.set()
            await asyncio.Event().wait()
            yield TurnEnd(text="never", usage=fake_usage("claude-sonnet-5-5"))
        finally:
            self.closed = True


async def test_cancelling_the_turn_stores_nothing_and_closes_the_model_stream(stored_rig: Rig) -> None:
    rig = stored_rig
    hanging = HangingClaude()
    service = rig.service_with(hanging)
    seen: list[ChatEvent] = []

    async def consume() -> None:
        async for event in service.stream_turn(rig.request("hello"), rig.rc):
            seen.append(event)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(hanging.started.wait(), timeout=5)
    assert deltas(seen) == "Partial "

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert hanging.closed is True
    assert rig.sessions.appends == []
    assert hanging.session_id is not None
    assert (await rig.stored(hanging.session_id)).messages == []  # the session exists, empty


async def test_closing_the_stream_early_stores_nothing_and_closes_the_model_stream(stored_rig: Rig) -> None:
    rig = stored_rig
    hanging = HangingClaude()
    stream = rig.service_with(hanging).stream_turn(rig.request("hello"), rig.rc)

    first = await stream.__anext__()
    assert first == DeltaEvent(text="Partial ")
    await stream.aclose()  # type: ignore[attr-defined]  # what the HTTP layer does when the client disconnects

    assert hanging.closed is True
    assert rig.sessions.appends == []


async def test_a_cancelled_turn_can_be_asked_again_from_the_same_session(stored_rig: Rig) -> None:
    rig = stored_rig
    sid = await rig.seed_turns(1)
    hanging = HangingClaude()
    service = rig.service_with(hanging)

    async def consume() -> None:
        async for _ in service.stream_turn(rig.request("hello", sessionId=sid), rig.rc):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(hanging.started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len((await rig.stored(sid)).messages) == 2  # only the seeded exchange
    rig.claude.script_chat(text_turn("ok"))
    assert done_id(await rig.run("hello again", sessionId=sid)) == sid
    assert len((await rig.stored(sid)).messages) == 4


# ───────────────────────── the service as built by the container ─────────────────────────


def test_the_constructor_is_keyword_only(rig: Rig) -> None:
    with pytest.raises(TypeError):
        CoachChatService(rig.settings, rig.claude, rig.decisions, rig.profiles, rig.sessions, rig.sample)  # type: ignore[call-arg]


async def test_the_clock_is_optional(rig: Rig) -> None:
    service = CoachChatService(
        settings=rig.settings,
        claude=rig.claude,
        decisions=rig.decisions,
        profiles=rig.profiles,
        sessions=rig.sessions,
        sample=rig.sample,
    )
    await rig.save_profile()
    rig.claude.script_chat(text_turn("ok"))
    events = [e async for e in service.stream_turn(rig.request("hi"), rig.rc)]
    assert kinds(events) == ["delta", "done"]
