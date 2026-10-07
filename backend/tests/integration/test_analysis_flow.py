"""End-to-end analyses through POST /api/analyze: the sample's golden skeleton, and a DOCX resume + link + notes
run with Jev's triage and evidence check. Real SQLite, real services and decision runner; only Claude, Jev and the
web fetchers are scripted."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable

from tayyari.analysis.evidence import EVIDENCE_MIN_SCORE
from tayyari.contract import LogEvent, Profile, ReaderEvent, ReadersEvent, SignalEvent
from tayyari.core.settings import Settings
from tayyari.decisions.packs.triage import NOT_PERSON_NOTE, SKIP_REASONS, WALL
from tayyari.storage.tables import LlmCallRow
from tests.fakes.claude import StructuredCall
from tests.fakes.jev import FakeJev
from tests.integration.scenarios import BOGUS_CLAIM, EMAIL, LINK_URL, PHONE, run_composite
from tests.integration.support import (
    FIRST_READ_CHIPS,
    FIRST_READ_LINES,
    FakeWeb,
    JevScript,
    RecordingClaude,
    analysis_events,
    assert_skeleton,
    done_of,
    eventually,
    index_of,
    logs_by,
    questions_asked,
    running,
    sample_readers,
    sample_synth,
    signals_from,
    source_id_of,
    types_of,
)

MakeSettings = Callable[..., Settings]


# ───────────────────────────── 1. the sample's golden skeleton ─────────────────────────────


async def test_a_sample_analysis_streams_the_golden_skeleton(build_settings: MakeSettings) -> None:
    jev = FakeJev(JevScript())
    claude = RecordingClaude()
    readers = sample_readers()

    async def reader(call: StructuredCall) -> dict[str, object]:
        # Jev's first read is meant to land before any reader finishes, so hold the readers until it was asked.
        await eventually(lambda: bool(questions_asked(jev, "first_read.")), what="the first read to be asked")
        await asyncio.sleep(0.1)
        return readers[source_id_of(call)]

    claude.script_structured("reader", reader, repeat=True)
    claude.script_structured("synth", lambda _call: sample_synth(), repeat=True)

    async with running(build_settings(), claude=claude, jev=jev) as env:
        response = await env.analyze(sample=True, headers={"x-request-id": "golden-1"})
        events = analysis_events(response)
        profile_response = await env.client.get(f"/api/profile/{done_of(events).profile.id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-ndjson; charset=utf-8"
    assert response.headers["x-request-id"] == "golden-1"
    assert_skeleton(events)

    # readers first: one queued card per source, in reading order
    announced = events[0]
    assert isinstance(announced, ReadersEvent)
    assert [r.sourceId for r in announced.readers] == ["resume", "github", "lovable-1", "portfolio-1"]
    assert {r.status for r in announced.readers} == {"queued"}

    # Jev's first read: the log lines (who "Jev") and the chips arrive before any reader finishes, so also
    # before the synthesizing phase
    synthesizing = index_of(events, lambda e: e.type == "phase" and e.phase == "synthesizing")
    first_reader_done = index_of(events, lambda e: isinstance(e, ReaderEvent) and e.reader.status == "done")
    first_read_lines = [e.text for e in logs_by(events, "Jev") if not e.text.startswith("Checked ")]
    chips = signals_from(events, "first read")
    assert first_read_lines == FIRST_READ_LINES
    assert [c.label for c in chips] == FIRST_READ_CHIPS
    first_read_positions = [
        i
        for i, e in enumerate(events)
        if (isinstance(e, LogEvent) and e.who == "Jev" and e.text in FIRST_READ_LINES)
        or (isinstance(e, SignalEvent) and e.src == "first read")
    ]
    assert len(first_read_positions) == len(FIRST_READ_LINES) + len(FIRST_READ_CHIPS)
    assert max(first_read_positions) < first_reader_done < synthesizing

    # the evidence summary line sits between the synthesizing phase and "Graph ready", and nothing was dropped
    summary = [e for e in logs_by(events, "Jev") if e.text.startswith("Checked ")]
    assert len(summary) == 1
    assert re.fullmatch(r"Checked \d+ evidence quotes?, dropped 0", summary[0].text), summary[0].text
    graph_ready = index_of(events, lambda e: isinstance(e, LogEvent) and e.text.startswith("Graph ready"))
    assert synthesizing < events.index(summary[0]) < graph_ready

    # "Graph ready", then synth 3, phase done and one done, last and exactly once
    assert types_of(events[graph_ready:]) == ["log", "synth", "phase", "done"]
    assert types_of(events).count("done") == 1

    # the stored profile is the one the stream ended with
    done = done_of(events)
    assert done.profile.id.startswith("sample-")
    assert profile_response.status_code == 200
    assert Profile.from_dict(profile_response.json()) == done.profile

    # every Jev answer was confident enough: Claude was never asked to stand in
    assert claude.calls_for("decision_fallback") == []
    assert len(claude.calls_for("reader")) == 4
    assert len(claude.calls_for("synth")) == 1


# ───────────────────────────── 2. resume + link + notes ─────────────────────────────


async def test_a_docx_resume_with_a_link_and_notes_is_labelled_and_checked(
    build_settings: MakeSettings, web: FakeWeb
) -> None:
    async with run_composite(build_settings(), web) as c:
        events = c.events
        stored = await c.env.client.get(f"/api/profile/{c.profile.id}")
        resume_call = next(
            call for call in c.env.claude.calls_for("reader") if source_id_of(call) == "resume"
        )

    assert c.response.status_code == 200
    assert_skeleton(events)
    announced = events[0]
    assert isinstance(announced, ReadersEvent)
    assert [r.sourceId for r in announced.readers] == ["resume", "link-1", "notes"]
    assert "DOCX" in announced.readers[0].kindLabel
    assert web.web_calls == [LINK_URL]

    # triage: the generic "Web page" link got the label Jev found, on the card and in the profile
    link_cards = [e.reader for e in events if isinstance(e, ReaderEvent) and e.reader.sourceId == "link-1"]
    assert link_cards[0].kindLabel != "Blog"
    assert link_cards[-1].status == "done"
    assert link_cards[-1].kindLabel == "Blog"
    link_source = next(s for s in c.profile.sources if s.id == "link-1")
    assert link_source.label == "Blog"
    assert link_source.ok
    # Jev was unsure whether the page shows personal work, so Claude answered; "yes" adds no warning note
    fallbacks = c.env.claude.calls_for("decision_fallback")
    assert len(fallbacks) == 1
    assert fallbacks[0].schema["properties"].keys() == {"answer", "confidence"}
    assert NOT_PERSON_NOTE not in link_source.notes

    # the evidence check dropped the one quote no source backs, and only that one
    quotes = {(s.id, e.text) for s in c.profile.skills for e in s.evidence}
    assert BOGUS_CLAIM not in {text for _, text in quotes}
    react = next(s for s in c.profile.skills if s.name == "React")
    assert [e.text for e in react.evidence] == ["Built the checkout in React and TypeScript"]
    kept = sum(len(s.evidence) for s in c.profile.skills if s.score >= EVIDENCE_MIN_SCORE)
    summary = [e.text for e in logs_by(events, "Jev") if e.text.startswith("Checked ")]
    assert summary == [f"Checked {kept + 1} evidence quotes, dropped 1"]
    assert len(questions_asked(c.jev, "evidence.support")) == 1  # the code settled every other quote itself
    assert stored.status_code == 200
    assert Profile.from_dict(stored.json()) == c.profile

    # Jev never saw the contact data of the resume (the Haiku reader did: it is the person's own document)
    first_read_state = next(
        call.state for call in c.jev.calls if any(q.startswith("first_read.") for q in call.question_ids)
    )
    assert isinstance(first_read_state, dict)
    assert "Acme Labs" in first_read_state["resume"]
    assert EMAIL not in first_read_state["resume"]
    assert PHONE not in first_read_state["resume"]
    assert EMAIL in resume_call.user_text


async def test_triage_skips_a_login_wall_and_cancels_its_reader(
    build_settings: MakeSettings, web: FakeWeb
) -> None:
    async with run_composite(build_settings(), web, walled=True) as c:
        events = c.events
        reader_rows = await c.env.rows(LlmCallRow, LlmCallRow.purpose == "reader")

    assert c.response.status_code == 200
    assert_skeleton(events)
    reason = SKIP_REASONS[WALL]

    # the card ends in error with (the start of) the reason, and the log says why
    link_cards = [e.reader for e in events if isinstance(e, ReaderEvent) and e.reader.sourceId == "link-1"]
    assert link_cards[-1].status == "error"
    assert link_cards[-1].line.startswith("This page is behind a login or a bot check")
    could_not_read = [e for e in events if isinstance(e, LogEvent) and e.text.startswith("Could not read")]
    assert len(could_not_read) == 1
    assert "This page is behind a login or a bot check" in could_not_read[0].text

    # the source is marked failed with the skip reason, and was never read by Haiku
    link_source = next(s for s in c.profile.sources if s.id == "link-1")
    assert not link_source.ok
    assert link_source.error == reason
    assert c.reader_cancelled.is_set()
    assert len(reader_rows) == 2  # resume and notes: the cancelled reader never produced a usage row
    assert next(s for s in c.profile.sources if s.id == "resume").ok

    # the rest of the analysis went on normally
    assert events[-1].type == "done"
