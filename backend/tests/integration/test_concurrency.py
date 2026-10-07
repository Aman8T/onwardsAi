"""Several requests at once over one SQLite file: analyses and chat turns write usage rows, the decision log,
profiles, sessions and the spend ledger at the same time, and none of those writes may be lost or fail."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable

from tayyari.core.settings import Settings
from tayyari.storage.tables import (
    ChatMessageRow,
    ChatSessionRow,
    DecisionLogRow,
    JevCallRow,
    LlmCallRow,
    ProfileRow,
)
from tests.fakes.claude import text_turn
from tests.fakes.jev import FakeJev
from tests.integration.scenarios import NOTES, script_basic_analysis
from tests.integration.support import (
    JevScript,
    RecordingClaude,
    analysis_events,
    chat_events,
    done_of,
    running,
    types_of,
)

MakeSettings = Callable[..., Settings]
ANALYSES = 4  # the default number of analyses one process runs at once
CHATS = 6


async def test_concurrent_analyses_and_chats_lose_no_write(build_settings: MakeSettings) -> None:
    claude = RecordingClaude()
    script_basic_analysis(claude)
    claude.script_chat(text_turn("Sure, here is a plan."), repeat=True)
    async with running(build_settings(), claude=claude, jev=FakeJev(JevScript())) as env:
        responses = await asyncio.gather(
            *(env.analyze(notes=f"{NOTES} Variant {i}.") for i in range(ANALYSES)),
            *(env.chat(f"Question {i}?") for i in range(CHATS)),
        )
        await env.settle()
        profiles = await env.rows(ProfileRow)
        sessions = await env.rows(ChatSessionRow)
        messages = await env.rows(ChatMessageRow)
        llm = await env.rows(LlmCallRow)
        jev_calls = await env.rows(JevCallRow)
        decisions = await env.rows(DecisionLogRow)
        spend = await env.spend()

    analyses, chats = responses[:ANALYSES], responses[ANALYSES:]
    assert [r.status_code for r in responses] == [200] * (ANALYSES + CHATS)

    # every analysis finished with exactly one done, and each profile was stored
    analysis_streams = [analysis_events(r) for r in analyses]
    assert all(types_of(events).count("done") == 1 for events in analysis_streams)
    assert all("error" not in types_of(events) for events in analysis_streams)
    profile_ids = {done_of(events).profile.id for events in analysis_streams}
    assert len(profile_ids) == ANALYSES
    assert {p.id for p in profiles} == profile_ids | {env.sample_id}

    # every chat turn got its own session with its two messages
    assert all(types_of(chat_events(r))[-1] == "done" for r in chats)
    assert len(sessions) == CHATS and {s.turn_count for s in sessions} == {1}
    assert len(messages) == 2 * CHATS

    # no usage row was lost, and the ledger adds up to all of them
    assert Counter(r.purpose for r in llm) == {"reader": ANALYSES, "synth": ANALYSES, "chat": CHATS}
    assert jev_calls and decisions  # first reads, the chat gate and the shadow questions were logged too
    anthropic_micro = sum(r.cost_micro_usd for r in llm)
    jev_micro = sum(r.cost_micro_usd for r in jev_calls)
    assert spend["anthropic"] == (anthropic_micro, len(llm))
    assert spend["jev"] == (jev_micro, len(jev_calls))
    assert spend["total"] == (anthropic_micro + jev_micro, len(llm) + len(jev_calls))
