"""Chat history replay: a stored answer that used web tools carries a short note of them for the model.

After a restart the model saw its earlier cited answer but no record of the search behind it. The note is
built from the stored ``tools`` column while the history is assembled; it is never stored and never goes
on the turn being answered.
"""

from __future__ import annotations

from tayyari.chat.service import RESET_NOTE
from tayyari.contract import ChatDoneEvent
from tayyari.llm.types import MessageParam, ToolUse
from tayyari.storage.ports import ToolEvent
from tests.fakes.claude import text_turn
from tests.unit.chat.support import PROFILE_ID, Rig

NOTE_START = "\n\n[Tools used for this answer: "


async def seed_answer(rig: Rig, tools: list[ToolEvent], *, question: str = "q0", answer: str = "a0") -> str:
    """A session holding one finished exchange whose answer used ``tools``."""
    session = await rig.fakes.sessions.create(PROFILE_ID, None, None)
    await rig.fakes.sessions.append_turn(
        session.id, user=question, assistant=answer, tools=tools, llm_call_id=None
    )
    return session.id


async def replayed(rig: Rig, sid: str, message: str = "and then?") -> list[MessageParam]:
    """The messages the next turn of ``sid`` sends to Claude (history first, the new user text last)."""
    rig.claude.script_chat(text_turn("ok"))
    await rig.run(message, sessionId=sid)
    return list(rig.claude.chat_calls[-1].messages)


def note_of(content: str, answer: str = "a0") -> str:
    """What follows the stored answer text in a replayed message."""
    assert content.startswith(answer)
    return content[len(answer) :]


# ───────────────────────── the note ─────────────────────────


async def test_an_answer_that_used_tools_is_replayed_with_a_note_of_them(stored_rig: Rig) -> None:
    rig = stored_rig
    sid = await seed_answer(
        rig,
        [
            ToolEvent("WebSearch", "design engineer salary Bengaluru"),
            ToolEvent("WebFetch", "https://example.com/page"),
        ],
        answer="Roughly 25 to 40 LPA.",
    )

    messages = await replayed(rig, sid)

    assert messages[0] == {"role": "user", "content": "q0"}
    assert messages[1] == {
        "role": "assistant",
        "content": (
            "Roughly 25 to 40 LPA.\n\n[Tools used for this answer: "
            'searched the web for "design engineer salary Bengaluru"; read example.com/page]'
        ),
    }
    assert messages[2] == {"role": "user", "content": "and then?"}


async def test_the_raw_server_tool_names_read_the_same_as_the_ui_names(stored_rig: Rig) -> None:
    rig = stored_rig
    sid = await seed_answer(rig, [ToolEvent("web_search", "q"), ToolEvent("web_fetch", "https://a.dev/x")])

    note = note_of((await replayed(rig, sid))[1]["content"])

    assert note == NOTE_START + 'searched the web for "q"; read a.dev/x]'


async def test_an_answer_without_tools_is_replayed_unchanged(stored_rig: Rig) -> None:
    rig = stored_rig
    sid = await seed_answer(rig, [], answer="Plain answer.")

    messages = await replayed(rig, sid)

    assert messages[1] == {"role": "assistant", "content": "Plain answer."}


async def test_the_note_is_only_for_assistant_messages(stored_rig: Rig) -> None:
    """A turn with no visible text keeps its tools on the user message: nothing is appended to it."""
    rig = stored_rig
    session = await rig.fakes.sessions.create(PROFILE_ID, None, None)
    await rig.fakes.sessions.append_turn(
        session.id,
        user="search please",
        assistant=None,
        tools=[ToolEvent("WebSearch", "q")],
        llm_call_id=None,
    )

    messages = await replayed(rig, session.id)

    assert [m["content"] for m in messages] == ["search please", "and then?"]


async def test_the_turn_being_answered_never_carries_a_note(stored_rig: Rig) -> None:
    rig = stored_rig
    sid = await seed_answer(rig, [ToolEvent("WebSearch", "q")])

    messages = await replayed(rig, sid, "what did you search for?")

    assert messages[-1] == {"role": "user", "content": "what did you search for?"}
    assert "Tools used" not in messages[-1]["content"]


async def test_unknown_tools_are_left_out_and_an_empty_note_is_not_added(stored_rig: Rig) -> None:
    rig = stored_rig
    sid = await seed_answer(rig, [ToolEvent("Bash", "rm -rf /")])
    assert (await replayed(rig, sid))[1]["content"] == "a0"

    mixed = await seed_answer(rig, [ToolEvent("Bash", "ls"), ToolEvent("WebSearch", "q")])
    assert note_of((await replayed(rig, mixed))[1]["content"]) == NOTE_START + 'searched the web for "q"]'


async def test_a_tool_without_detail_and_messy_details_are_tidied(stored_rig: Rig) -> None:
    rig = stored_rig
    sid = await seed_answer(
        rig,
        [
            ToolEvent("WebSearch", ""),
            ToolEvent("WebSearch", '  best "react"\n  courses  '),
            ToolEvent("WebFetch", ""),
            ToolEvent("WebFetch", "HTTP://Example.com/a?x=1"),
        ],
    )

    note = note_of((await replayed(rig, sid))[1]["content"])

    assert note == NOTE_START + (
        "searched the web; searched the web for \"best 'react' courses\"; read a web page; read Example.com/a?x=1]"
    )


# ───────────────────────── dedupe and caps ─────────────────────────


async def test_identical_entries_are_listed_once_in_first_seen_order(stored_rig: Rig) -> None:
    rig = stored_rig
    sid = await seed_answer(
        rig,
        [
            ToolEvent("WebSearch", "salary"),
            ToolEvent("WebFetch", "https://a.dev"),
            ToolEvent("WebSearch", "salary"),
            ToolEvent("web_search", "salary"),
            ToolEvent("WebFetch", "https://a.dev"),
            ToolEvent("WebSearch", "equity"),
        ],
    )

    note = note_of((await replayed(rig, sid))[1]["content"])

    assert note == NOTE_START + 'searched the web for "salary"; read a.dev; searched the web for "equity"]'


async def test_a_search_and_a_fetch_of_the_same_text_are_different_entries(stored_rig: Rig) -> None:
    rig = stored_rig
    sid = await seed_answer(rig, [ToolEvent("WebSearch", "a.dev"), ToolEvent("WebFetch", "a.dev")])

    note = note_of((await replayed(rig, sid))[1]["content"])

    assert note == NOTE_START + 'searched the web for "a.dev"; read a.dev]'


async def test_at_most_five_entries_are_listed(stored_rig: Rig) -> None:
    rig = stored_rig
    sid = await seed_answer(rig, [ToolEvent("WebSearch", f"q{i}") for i in range(8)])

    note = note_of((await replayed(rig, sid))[1]["content"])

    assert note == NOTE_START + "; ".join(f'searched the web for "q{i}"' for i in range(5)) + "]"
    assert "q5" not in note


async def test_the_cap_counts_distinct_entries_not_tool_uses(stored_rig: Rig) -> None:
    rig = stored_rig
    tools = [ToolEvent("WebSearch", "same")] * 6 + [ToolEvent("WebSearch", f"q{i}") for i in range(4)]
    sid = await seed_answer(rig, tools)

    note = note_of((await replayed(rig, sid))[1]["content"])

    assert note.count("searched the web") == 5
    assert 'for "q3"' in note


async def test_the_note_stays_within_300_characters_by_dropping_trailing_entries(stored_rig: Rig) -> None:
    rig = stored_rig
    tools = [ToolEvent("WebSearch", f"{i} " + "word " * 40) for i in range(5)]
    sid = await seed_answer(rig, tools)

    note = note_of((await replayed(rig, sid))[1]["content"])

    assert note.startswith(NOTE_START) and note.endswith("]")
    assert len(note) <= 300  # separator and brackets included
    assert 'for "0 word' in note  # the first entry survives
    assert 'for "4 word' not in note  # the trailing ones were dropped to fit
    assert "..." in note  # an over-long detail is cut with a mark


async def test_a_single_huge_detail_is_cut_not_dropped(stored_rig: Rig) -> None:
    rig = stored_rig
    sid = await seed_answer(rig, [ToolEvent("WebFetch", "https://a.dev/" + "p" * 200)])

    note = note_of((await replayed(rig, sid))[1]["content"])

    assert note.startswith(NOTE_START + "read a.dev/ppp")
    assert note.endswith("...]")
    assert len(note) <= 300


# ───────────────────────── nothing else changes ─────────────────────────


async def test_the_stored_rows_are_never_changed(stored_rig: Rig) -> None:
    rig = stored_rig
    tools = [ToolEvent("WebSearch", "salary"), ToolEvent("WebFetch", "https://a.dev")]
    sid = await seed_answer(rig, tools, answer="Cited answer.")

    await replayed(rig, sid)

    stored = await rig.stored(sid)
    assert [(m.role, m.content) for m in stored.messages] == [
        ("user", "q0"),
        ("assistant", "Cited answer."),
        ("user", "and then?"),
        ("assistant", "ok"),
    ]
    assert stored.messages[1].tools == tools
    assert all("Tools used" not in m.content for m in stored.messages)
    assert rig.sessions.appends[-1]["user"] == "and then?"


async def test_a_live_turn_that_used_tools_is_noted_on_the_next_turn_only_in_the_replay(
    stored_rig: Rig,
) -> None:
    rig = stored_rig
    rig.claude.script_chat(
        text_turn("Here is what I found.", tools=[ToolUse("WebSearch", "react testing courses")]),
        text_turn("The search was for courses."),
    )

    events = await rig.run("Find me a course")
    sid = events[-1].sessionId if isinstance(events[-1], ChatDoneEvent) else ""
    assert sid
    await rig.run("what did you search?", sessionId=sid)

    first, second = rig.claude.chat_calls
    assert [m["content"] for m in first.messages] == ["Find me a course"]
    assert [m["content"] for m in second.messages] == [
        "Find me a course",
        'Here is what I found.\n\n[Tools used for this answer: searched the web for "react testing courses"]',
        "what did you search?",
    ]
    stored = await rig.stored(sid)
    assert stored.messages[1].content == "Here is what I found."
    assert stored.messages[1].tools == [ToolEvent("WebSearch", "react testing courses")]


async def test_the_replay_is_identical_on_every_turn_so_the_prefix_stays_cacheable(stored_rig: Rig) -> None:
    rig = stored_rig
    sid = await seed_answer(rig, [ToolEvent("WebSearch", "salary"), ToolEvent("WebFetch", "https://a.dev")])
    rig.claude.script_chat(text_turn("one"), text_turn("two"))

    await rig.run("first follow-up", sessionId=sid)
    await rig.run("second follow-up", sessionId=sid)

    first, second = rig.claude.chat_calls
    assert list(second.messages)[:2] == list(first.messages)[:2]
    assert list(second.messages)[2:] == [
        {"role": "user", "content": "first follow-up"},
        {"role": "assistant", "content": "one"},
        {"role": "user", "content": "second follow-up"},
    ]


async def test_the_forty_message_cap_and_ordering_are_unchanged(stored_rig: Rig) -> None:
    rig = stored_rig
    session = await rig.fakes.sessions.create(PROFILE_ID, None, None)
    for i in range(25):  # 50 messages: q0 a0 ... q24 a24, every answer used a tool
        await rig.fakes.sessions.append_turn(
            session.id,
            user=f"q{i}",
            assistant=f"a{i}",
            tools=[ToolEvent("WebSearch", f"s{i}")],
            llm_call_id=None,
        )

    messages = await replayed(rig, session.id, "one more")

    assert len(messages) == 41
    assert [m["role"] for m in messages] == ["user", "assistant"] * 20 + ["user"]
    assert messages[0] == {"role": "user", "content": "q5"}  # messages 0..9 fell off the front
    assert messages[1]["content"] == 'a5\n\n[Tools used for this answer: searched the web for "s5"]'
    assert messages[39]["content"] == 'a24\n\n[Tools used for this answer: searched the web for "s24"]'
    assert messages[40] == {"role": "user", "content": "one more"}


async def test_a_window_that_starts_on_a_reply_is_still_trimmed_to_start_on_a_question(
    stored_rig: Rig,
) -> None:
    rig = stored_rig
    session = await rig.fakes.sessions.create(PROFILE_ID, None, None)
    for i in range(20):
        await rig.fakes.sessions.append_turn(
            session.id,
            user=f"q{i}",
            assistant=f"a{i}",
            tools=[ToolEvent("WebSearch", f"s{i}")],
            llm_call_id=None,
        )
    await rig.fakes.sessions.append_turn(
        session.id, user="tools only", assistant=None, tools=[], llm_call_id=None
    )

    messages = await replayed(rig, session.id, "next")

    assert messages[0] == {"role": "user", "content": "q1"}  # a0 fell to the cap and was trimmed away
    assert messages[1]["content"] == 'a1\n\n[Tools used for this answer: searched the web for "s1"]'
    assert len(messages) == 40
    assert messages[-2:] == [
        {"role": "user", "content": "tools only"},
        {"role": "user", "content": "next"},
    ]


async def test_a_session_that_cannot_be_resumed_still_gets_the_reset_note_and_no_history(
    stored_rig: Rig,
) -> None:
    rig = stored_rig
    foreign = await rig.fakes.sessions.create("aa11bb22cc33dd44ee55ff6600778899", None, None)
    await rig.fakes.sessions.append_turn(
        foreign.id, user="q", assistant="a", tools=[ToolEvent("WebSearch", "q")], llm_call_id=None
    )
    rig.claude.script_chat(text_turn("ok"))

    await rig.run("hello", sessionId=foreign.id)

    (call,) = rig.claude.chat_calls
    assert list(call.messages) == [{"role": "user", "content": RESET_NOTE + "hello"}]
