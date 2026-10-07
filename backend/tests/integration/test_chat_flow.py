"""End-to-end chat through POST /api/chat: sessions and history in a real SQLite file, the web-search gate decided
by a real `DecisionRunner` over a scripted Jev, and the streamed Claude turn scripted per call."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy import delete

from tayyari.chat.service import RESET_NOTE
from tayyari.contract import ChatDoneEvent, ChatEvent, DeltaEvent, ToolEvent
from tayyari.core.ids import is_valid_id
from tayyari.core.settings import Settings
from tayyari.llm.errors import JevError
from tayyari.llm.models import WEB_SEARCH_MICRO_USD
from tayyari.llm.types import ToolUse
from tayyari.storage.tables import (
    ChatMessageRow,
    ChatSessionRow,
    DecisionLogRow,
    LlmCallRow,
    ProfileContextRow,
    ProfileRow,
)
from tests.fakes.claude import text_turn
from tests.fakes.jev import FakeJev, noul_answer
from tests.integration.scenarios import run_composite
from tests.integration.support import (
    FakeWeb,
    JevScript,
    RecordingClaude,
    chat_events,
    fallback_answer,
    questions_asked,
    running,
    types_of,
)

MakeSettings = Callable[..., Settings]

FIRST_ANSWER = "Learn SQL first, then dbt."
SECOND_ANSWER = "After that, orchestration."


def _done(events: Sequence[ChatEvent]) -> ChatDoneEvent:
    last = events[-1]
    assert isinstance(last, ChatDoneEvent), events
    return last


async def test_the_first_turn_creates_a_session_and_the_second_continues_it_with_history(
    build_settings: MakeSettings,
) -> None:
    claude = RecordingClaude()
    claude.script_chat(
        text_turn(
            FIRST_ANSWER,
            chunk_chars=8,
            tools=[ToolUse("WebSearch", "sql courses"), ToolUse("web_fetch", "https://example.com/sql")],
        ),
        text_turn(SECOND_ANSWER),
    )
    async with running(build_settings(), claude=claude, jev=FakeJev(JevScript())) as env:
        first = await env.chat("What should I learn next?")
        first_events = chat_events(first)
        session_id = _done(first_events).sessionId
        second = await env.chat("And after that?", session_id=session_id)
        second_events = chat_events(second)
        sessions = await env.rows(ChatSessionRow)
        messages = sorted(await env.rows(ChatMessageRow), key=lambda m: m.seq)
        usage = await env.rows(LlmCallRow)

    # turn 1: tool events (the UI's names, also for a raw API name), the text in pieces, one done with the session id
    assert first.status_code == 200
    assert first.headers["content-type"] == "application/x-ndjson; charset=utf-8"
    assert types_of(first_events)[:2] == ["tool", "tool"] and types_of(first_events)[-1] == "done"
    tools = [e for e in first_events if isinstance(e, ToolEvent)]
    assert [(t.name, t.detail) for t in tools] == [
        ("WebSearch", "sql courses"),
        ("WebFetch", "https://example.com/sql"),
    ]
    assert "".join(e.text for e in first_events if isinstance(e, DeltaEvent)) == FIRST_ANSWER
    assert is_valid_id(session_id) and len(session_id) == 32

    # turn 2: the same session; Claude received the whole conversation so far
    assert _done(second_events).sessionId == session_id
    assert [e for e in second_events if isinstance(e, ToolEvent)] == []
    turn1, turn2 = claude.chat_calls
    assert turn1.messages == ({"role": "user", "content": "What should I learn next?"},)
    history = turn2.messages
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert history[0]["content"] == "What should I learn next?"
    assert history[1]["content"].startswith(FIRST_ANSWER)
    # the replayed answer says which tools it used, so the model knows where its own facts came from ...
    assert "sql courses" in history[1]["content"] and "example.com/sql" in history[1]["content"]
    assert history[2]["content"] == "And after that?"
    assert turn1.tools == turn2.tools == "web"
    assert turn1.model == turn2.model == env.settings.chat_model
    assert turn1.ctx.session_id == turn2.ctx.session_id == session_id
    assert turn1.ctx.profile_id == turn2.ctx.profile_id == env.sample_id
    # the profile block is the cached one, and it is byte-identical on every turn (so the prefix stays cached)
    profile_block = next(b for b in turn1.system if "Riya" in b.text)
    assert profile_block.cache is True
    assert turn1.system[:2] == turn2.system[:2]

    # the database holds one session of two turns, with the tool events next to the answer they belong to ...
    assert [(s.id, s.turn_count, s.profile_id) for s in sessions] == [(session_id, 2, env.sample_id)]
    assert [(m.seq, m.role, m.content) for m in messages] == [
        (0, "user", "What should I learn next?"),
        (1, "assistant", FIRST_ANSWER),
        (2, "user", "And after that?"),
        (3, "assistant", SECOND_ANSWER),
    ]
    # ... and the stored answer is the plain text: the note above exists only in the copy sent to Claude
    assert messages[1].tools == [
        {"name": "WebSearch", "detail": "sql courses"},
        {"name": "WebFetch", "detail": "https://example.com/sql"},
    ]
    assert messages[3].tools is None

    # two chat usage rows; the web searches of the first turn are billed on top of its tokens
    assert sorted(r.web_searches for r in usage) == [0, 2]
    assert {r.purpose for r in usage} == {"chat"}
    assert {r.session_id for r in usage} == {session_id}
    searched = next(r for r in usage if r.web_searches)
    assert searched.cost_micro_usd >= 2 * WEB_SEARCH_MICRO_USD


async def test_a_chat_on_the_sample_profile_seeds_it_into_the_database(build_settings: MakeSettings) -> None:
    claude = RecordingClaude()
    claude.script_chat(text_turn("Welcome, Riya."))
    async with running(build_settings(), claude=claude, jev=FakeJev(JevScript())) as env:
        sample_id = env.sample_id
        assert len(await env.rows(ProfileRow, ProfileRow.id == sample_id)) == 1  # the lifespan seeded it

        # as if that seed had been lost: the first chat puts it back
        assert env.container.db is not None
        async with env.container.db.session() as session:
            await session.execute(delete(ProfileRow).where(ProfileRow.id == sample_id))
        assert await env.rows(ProfileRow, ProfileRow.id == sample_id) == []

        response = await env.chat("Hello?")
        (profile_row,) = await env.rows(ProfileRow, ProfileRow.id == sample_id)
        (context_row,) = await env.rows(ProfileContextRow, ProfileContextRow.profile_id == sample_id)
        (session_row,) = await env.rows(ChatSessionRow)

    assert _done(chat_events(response)).sessionId == session_row.id
    assert profile_row.is_sample is True and profile_row.owner_hash is None
    assert profile_row.profile["id"] == sample_id
    assert set(context_row.context["sources"][0]) >= {"id", "kind", "content"}
    assert session_row.profile_id == sample_id
    assert "Riya" in claude.chat_calls[0].system_text


async def test_an_unknown_session_id_starts_a_new_session_with_the_reset_note(
    build_settings: MakeSettings,
) -> None:
    claude = RecordingClaude()
    claude.script_chat(text_turn("Fresh start."), text_turn("Still here."))
    stale = "f" * 32
    async with running(build_settings(), claude=claude, jev=FakeJev(JevScript())) as env:
        response = await env.chat("Where were we?", session_id=stale)
        new_id = _done(chat_events(response)).sessionId
        follow_up = await env.chat("And now?", session_id=new_id)
        sessions = await env.rows(ChatSessionRow)

    assert response.status_code == 200
    assert new_id != stale and is_valid_id(new_id)
    assert claude.chat_calls[0].messages == ({"role": "user", "content": RESET_NOTE + "Where were we?"},)
    assert [s.id for s in sessions] == [new_id]  # the stale id was not invented in the database
    # the new session continues normally; the note is part of what was stored for the first turn
    assert _done(chat_events(follow_up)).sessionId == new_id
    assert claude.chat_calls[1].messages[0] == {"role": "user", "content": RESET_NOTE + "Where were we?"}
    assert claude.chat_calls[1].messages[-1] == {"role": "user", "content": "And now?"}


async def test_chat_is_grounded_in_an_analysed_profile_and_sessions_do_not_cross_profiles(
    build_settings: MakeSettings, web: FakeWeb
) -> None:
    async with run_composite(build_settings(), web) as c:
        env = c.env
        env.claude.script_chat(text_turn("Sample answer."), text_turn("Profile answer."))
        sample_chat = await env.chat("Hi there")
        sample_session = _done(chat_events(sample_chat)).sessionId
        # the same session id, but on the analysed profile: it belongs to another profile, so it is not resumed
        crossed = await env.chat(
            "What about my React work?", profile_id=c.profile.id, session_id=sample_session
        )
        crossed_id = _done(chat_events(crossed)).sessionId
        sessions = await env.rows(ChatSessionRow)

    call = env.claude.chat_calls[1]
    assert crossed_id != sample_session
    assert call.ctx.profile_id == c.profile.id
    assert "Test Person" in call.system_text  # the profile the analysis stored
    assert "Acme Labs" in call.system_text  # and the raw source text it kept for chat
    assert call.messages == ({"role": "user", "content": RESET_NOTE + "What about my React work?"},)
    assert {s.id: s.profile_id for s in sessions} == {sample_session: env.sample_id, crossed_id: c.profile.id}


async def test_history_survives_a_full_restart_of_the_app(build_settings: MakeSettings) -> None:
    settings = build_settings()  # one data folder, so both apps open the same SQLite file
    first_claude = RecordingClaude()
    first_claude.script_chat(text_turn("Data engineering it is."))
    async with running(settings, claude=first_claude, jev=FakeJev(JevScript())) as env:
        session_id = _done(chat_events(await env.chat("My goal is data engineering."))).sessionId
        key_before = env.app.state.ip_hash_key

    second_claude = RecordingClaude()
    second_claude.script_chat(text_turn("You said data engineering."))
    async with running(settings, claude=second_claude, jev=FakeJev(JevScript())) as env2:
        response = await env2.chat("What did I say my goal was?", session_id=session_id)
        messages = sorted(await env2.rows(ChatMessageRow), key=lambda m: m.seq)
        key_after = env2.app.state.ip_hash_key

    assert _done(chat_events(response)).sessionId == session_id
    assert second_claude.chat_calls[0].messages == (
        {"role": "user", "content": "My goal is data engineering."},
        {"role": "assistant", "content": "Data engineering it is."},
        {"role": "user", "content": "What did I say my goal was?"},
    )
    assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]
    assert key_before == key_after  # the IP-hash key lives in the database too


# ───────────────────────────── the web-search gate ─────────────────────────────


async def test_the_gate_withholds_web_tools_only_for_a_sure_profile_question(
    build_settings: MakeSettings,
) -> None:
    claude = RecordingClaude()
    claude.script_chat(text_turn("From your profile."), text_turn("Looked it up."), text_turn("Opened it."))
    claude.script_structured("decision_fallback", fallback_answer, repeat=True)  # says: yes, it needs the web
    gate_says_no = FakeJev(JevScript(gate=noul_answer(0.05)))
    async with running(build_settings(), claude=claude, jev=gate_says_no) as env:
        await env.chat("Which of my skills is strongest?")
        gate_calls_after_first = len(questions_asked(gate_says_no, "chat.web_gate"))
        # a link in the message: code allows the tools without asking Jev at all
        await env.chat("Can you read https://example.com/post and tell me what it shows?")
        gate_calls_after_link = len(questions_asked(gate_says_no, "chat.web_gate"))
        # Jev unsure: Claude decides that question instead, and the tools stay on
        gate_says_no.script(JevScript(gate=noul_answer(0.4)))
        await env.chat("What salary can I ask for?")
        await env.settle()
        rows = await env.rows(DecisionLogRow, DecisionLogRow.pack == "chat_gate")

    assert [c.tools for c in claude.chat_calls] == ["none", "web", "web"]
    assert gate_calls_after_first == 1
    assert gate_calls_after_link == 1  # the message with a link did not use the gate
    assert len(rows) == 2
    assert {r.extra["purpose_tag"] for r in rows} == {"chat"}
    assert sorted((r.source_used, r.escalation_reason or "") for r in rows) == [
        ("claude", "low_confidence"),
        ("jev", ""),
    ]
    assert len(claude.calls_for("decision_fallback")) == 1


async def test_a_jev_outage_never_blocks_chat(build_settings: MakeSettings) -> None:
    claude = RecordingClaude()
    claude.script_chat(text_turn("Here you go."))
    claude.script_structured("decision_fallback", fallback_answer, repeat=True)
    jev = FakeJev(JevScript())
    jev.fail_with(JevError("server", "Jev is down"))
    async with running(build_settings(), claude=claude, jev=jev) as env:
        response = await env.chat("Which courses should I take?")
        await env.settle()
        rows = await env.rows(DecisionLogRow, DecisionLogRow.pack == "chat_gate")

    events = chat_events(response)
    assert response.status_code == 200
    assert types_of(events)[-1] == "done"
    assert claude.chat_calls[0].tools == "web"
    # Claude answered the gate's question instead of Jev, and the log says so
    assert len(claude.calls_for("decision_fallback")) == 1
    assert [(r.source_used, r.escalated, r.escalation_reason) for r in rows] == [
        ("claude", True, "jev_error")
    ]
