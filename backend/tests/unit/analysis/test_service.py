"""`PipelineAnalysisService`: the whole stream, with fakes for Claude, Jev, the store and the clock."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest

from tayyari.analysis import firstread
from tayyari.analysis.service import PipelineAnalysisService
from tayyari.contract import (
    AnalyzeRequest,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    Profile,
    ReaderEvent,
    SignalEvent,
    SynthEvent,
)
from tayyari.core.settings import Settings
from tayyari.decisions.service import (
    DecisionContext,
    FirstReadResult,
    ShadowMaterials,
    TriageResult,
)
from tayyari.llm.types import StructuredResult
from tayyari.prompts import SYNTH_SYSTEM
from tayyari.sample import SAMPLE_GOAL
from tests.fakes.decisions import FakeDecisionService
from tests.unit.analysis.conftest import FakeWeb
from tests.unit.analysis.support import (
    Harness,
    assert_skeleton,
    log_texts,
    logs,
    request_context,
    sample_readers,
    sample_synth,
    synth_output,
    tiny_synth,
    types_of,
)

RESUME_TEXT = (
    "Test Person\nFrontend Engineer, Pune\n\nExperience\n"
    "Acme Labs, Frontend Engineer, 2023 to 2026. Built the checkout in React and TypeScript, shipped weekly, "
    "mentored two interns and cut the largest bundle by forty percent.\n\n"
    "Skills\nReact, TypeScript, Node.js, GraphQL, Figma, accessibility, Storybook, design systems.\n"
)

FIRST_READ = FirstReadResult(
    lines=["First read: Design and UX, early career", "Depth 3 of 5: competent", "Leads or mentors others"],
    signals=["Leads others", "Measured results"],
    facts={"career_family": "design_ux"},
)


def done_of(events: list[Any]) -> Profile:
    done = events[-1]
    assert isinstance(done, DoneEvent)
    return done.profile


def stored(h: Harness, profile_id: str) -> Any:
    """The fake store's record (owner and flags are only visible there)."""
    return h.repos.profiles._s.profiles[profile_id]


# ───────────────────────── a full sample run ─────────────────────────


async def test_a_sample_run_streams_the_whole_skeleton(harness: Harness) -> None:
    harness.script_readers(readers=sample_readers())
    harness.script_synth(sample_synth())
    events = await harness.run(AnalyzeRequest(sample=True))
    assert_skeleton(events)

    kinds = types_of(events)
    assert kinds.index("readers") == 0
    readers_event = events[0]
    assert [r.sourceId for r in readers_event.readers] == ["resume", "github", "lovable-1", "portfolio-1"]  # type: ignore[union-attr]
    assert {r.status for r in readers_event.readers} == {"queued"}  # type: ignore[union-attr]
    assert [r.agent for r in readers_event.readers] == [  # type: ignore[union-attr]
        "Resume reader",
        "GitHub analyst",
        "Lovable reviewer",
        "Link reader",
    ]

    phases = [(e.phase, e.label) for e in events if e.type == "phase"]  # type: ignore[union-attr]
    assert phases == [
        ("reading", "Readers in progress"),
        ("synthesizing", "Drawing your graph"),
        ("done", "Your graph is ready"),
    ]
    finished = {e.reader.sourceId for e in events if isinstance(e, ReaderEvent) and e.reader.status == "done"}
    assert finished == {"resume", "github", "lovable-1", "portfolio-1"}

    texts = log_texts(events, "graph")
    assert texts[0] == "Merging evidence across 4 sources"
    assert texts[-1].startswith("Graph ready · 6 domains, 21 skills, 5 roles")
    assert any(isinstance(e, SignalEvent) for e in events)

    synth_steps = [e for e in events if isinstance(e, SynthEvent)]
    assert (synth_steps[0].step, synth_steps[0].chars) == (1, 0)
    assert (synth_steps[-1].step, synth_steps[-1].chars) == (3, None)

    assert len(harness.claude.calls_for("reader")) == 4
    assert len(harness.claude.calls_for("synth")) == 1
    assert harness.claude.pending() == 0


async def test_nothing_the_screen_shows_names_a_model(harness: Harness) -> None:
    """Phase labels and log lines name what is happening, never a model: the screen once said "Sonnet is drawing your
    graph" while production ran Haiku, and which model plays each role is configuration."""
    harness.script_readers(readers=sample_readers())
    harness.script_synth(sample_synth())
    events = await harness.run(AnalyzeRequest(sample=True))
    shown = [e.label for e in events if e.type == "phase"]  # type: ignore[union-attr]
    shown += [f"{e.who} {e.text}" for e in events if isinstance(e, LogEvent)]
    assert len(shown) > 5
    assert [
        text for text in shown if re.search(r"\b(?:haiku|sonnet|opus|claude)\b", text, re.IGNORECASE)
    ] == []


async def test_a_sample_run_builds_the_profile_from_the_fixtures(harness: Harness) -> None:
    harness.script_readers(readers=sample_readers())
    harness.script_synth(sample_synth())
    events = await harness.run(AnalyzeRequest(sample=True))
    profile = done_of(events)
    assert re.fullmatch(r"sample-[0-9a-f]{32}", profile.id)
    assert profile.goal == SAMPLE_GOAL
    assert profile.createdAt == "2026-10-07T12:00:00.000Z"  # the injected clock
    assert (profile.models.reader, profile.models.synth) == ("claude-haiku-4-5", "claude-sonnet-5-5")
    assert profile.person.name == "Riya Kapoor"
    assert [s.id for s in profile.sources] == ["resume", "github", "lovable-1", "portfolio-1"]
    assert all(s.ok for s in profile.sources)
    assert Profile.from_dict(profile.to_dict()) == profile


async def test_the_synth_prompt_carries_the_goal_the_sources_and_the_resume(harness: Harness) -> None:
    harness.script_readers(readers=sample_readers())
    harness.script_synth(sample_synth())
    await harness.run(AnalyzeRequest(sample=True))
    (call,) = harness.claude.calls_for("synth")
    assert f'"{SAMPLE_GOAL}"' in call.user_text
    for sid in ("resume", "github", "lovable-1", "portfolio-1"):
        assert f'<reader source_id="{sid}"' in call.user_text
    assert "<resume_text>" in call.user_text
    assert [b.text for b in call.system] == [SYNTH_SYSTEM]
    assert call.model == "claude-sonnet-5-5"
    assert call.effort == "medium"


async def test_every_claude_call_and_decision_shares_the_ids_of_the_run(harness: Harness) -> None:
    harness.rebuild(decisions=FakeDecisionService(first_read=FIRST_READ))
    harness.script_readers(readers=sample_readers())
    harness.script_synth(sample_synth())
    events = await harness.run(AnalyzeRequest(sample=True))
    profile = done_of(events)
    contexts = [c.ctx for c in harness.claude.structured_calls]
    assert {c.profile_id for c in contexts} == {profile.id}
    assert len({c.analysis_id for c in contexts}) == 1
    assert {c.request_id for c in contexts} == {"req-test"}
    assert {c.ip_hash for c in contexts} == {"iphash0123456789"}
    assert sorted(c.purpose for c in contexts) == ["reader"] * 4 + ["synth"]
    analysis_id = contexts[0].analysis_id
    decision_ctxs: list[DecisionContext] = [
        *(c for _, c in harness.decisions.first_read_calls),
        *(c for _, c in harness.decisions.evidence_calls),
        *(c for _, c in harness.decisions.shadow_calls),
    ]
    assert decision_ctxs
    assert {(c.profile_id, c.analysis_id, c.is_sample, c.request_id) for c in decision_ctxs} == {
        (profile.id, analysis_id, True, "req-test")
    }


async def test_the_profile_is_saved_with_its_context(harness: Harness) -> None:
    harness.script_readers(readers=sample_readers())
    harness.script_synth(sample_synth())
    events = await harness.run(AnalyzeRequest(sample=True), request_context(ip_hash="owner-hash"))
    profile = done_of(events)
    record = stored(harness, profile.id)
    assert record.owner_hash == "owner-hash"
    assert record.is_sample is True
    assert record.goal == SAMPLE_GOAL
    assert record.profile == profile.to_dict()
    loaded = await harness.repos.profiles.get_for_chat(profile.id)
    assert loaded is not None
    saved_profile, context = loaded
    assert saved_profile == profile.to_dict()
    assert [(s.id, s.kind) for s in context.sources] == [
        ("resume", "resume"),
        ("github", "github"),
        ("lovable-1", "lovable"),
        ("portfolio-1", "portfolio"),
    ]
    assert all(s.content for s in context.sources)
    assert set(context.readers) == {"resume", "github", "lovable-1", "portfolio-1"}
    assert context.readers["resume"]["sub"] == sample_readers()["resume"]["sub"]


# ───────────────────────── a resume and a link ─────────────────────────


def resume_and_link() -> AnalyzeRequest:
    return AnalyzeRequest(
        resume_bytes=RESUME_TEXT.encode(),
        resume_name="resume.txt",
        links=("https://example.com/portfolio",),
        goal="  Become a staff engineer  ",
        notes="I prefer small product teams.",
    )


async def test_a_resume_link_and_notes_run(harness: Harness, web: FakeWeb) -> None:
    harness.rebuild(decisions=FakeDecisionService(first_read=FIRST_READ))
    harness.script_readers()
    harness.script_synth(synth_output(["resume", "portfolio-1", "notes"]))
    events = await harness.run(resume_and_link())
    assert_skeleton(events)

    assert [r.sourceId for r in events[0].readers] == ["resume", "portfolio-1", "notes"]  # type: ignore[union-attr]
    assert web.web_calls == ["https://example.com/portfolio"]
    profile = done_of(events)
    assert re.fullmatch(r"[0-9a-f]{32}", profile.id)
    assert profile.goal == "Become a staff engineer"
    assert [s.id for s in profile.sources] == ["resume", "portfolio-1", "notes"]
    assert stored(harness, profile.id).is_sample is False
    assert len(harness.claude.calls_for("reader")) == 3

    (synth_call,) = harness.claude.calls_for("synth")
    assert '"Become a staff engineer"' in synth_call.user_text
    assert "<resume_text>" in synth_call.user_text and "Acme Labs" in synth_call.user_text
    assert "<user_notes>" in synth_call.user_text and "small product teams" in synth_call.user_text


async def test_jev_lines_and_chips_appear_before_the_resume_reader_finishes(
    harness: Harness, web: FakeWeb
) -> None:
    harness.rebuild(decisions=FakeDecisionService(first_read=FIRST_READ))
    harness.script_readers()
    harness.script_synth(synth_output(["resume", "portfolio-1", "notes"]))
    events = await harness.run(resume_and_link())

    jev = [(i, e) for i, e in enumerate(events) if isinstance(e, LogEvent) and e.who == "Jev"]
    first_read_lines = [e for _, e in jev if e.kind == "resume"]
    assert [e.text for e in first_read_lines] == FIRST_READ.lines
    chips = [(i, e) for i, e in enumerate(events) if isinstance(e, SignalEvent) and e.src == "first read"]
    assert [e.label for _, e in chips] == FIRST_READ.signals
    assert {e.kind for _, e in chips} == {"resume"}

    resume_done = next(
        i
        for i, e in enumerate(events)
        if isinstance(e, ReaderEvent) and e.reader.sourceId == "resume" and e.reader.status == "done"
    )
    assert jev[0][0] < resume_done
    assert chips[-1][0] < resume_done

    ((inp, _),) = harness.decisions.first_read_calls
    assert inp.resume_text is not None and "Acme Labs" in inp.resume_text
    assert inp.notes_text == "I prefer small product teams."
    assert str(inp.today) == "2026-10-07"


async def test_only_web_sources_are_triaged(harness: Harness, web: FakeWeb) -> None:
    harness.script_readers()
    harness.script_synth(synth_output(["resume", "github", "portfolio-1", "notes"]))
    req = AnalyzeRequest(
        resume_bytes=RESUME_TEXT.encode(),
        resume_name="resume.txt",
        links=("https://github.com/someone", "https://example.com/portfolio"),
        notes="Notes",
    )
    events = await harness.run(req)
    assert_skeleton(events)
    triaged = [inp.source_id for inp, _ in harness.decisions.triage_calls]
    assert triaged == ["portfolio-1"]
    assert harness.decisions.triage_calls[0][0].host == "example.com"
    assert harness.decisions.triage_calls[0][0].title == "Title of https://example.com/portfolio"


async def test_the_github_token_reaches_the_ingester(
    make_settings: Callable[..., Settings], web: FakeWeb
) -> None:
    from pydantic import SecretStr

    h = Harness(make_settings(github_token=SecretStr("ghp_test_token")))
    h.script_readers()
    h.script_synth(synth_output(["github"]))
    await h.run(AnalyzeRequest(links=("https://github.com/someone",)))
    assert web.github_calls == [("https://github.com/someone", "ghp_test_token")]


async def test_without_a_token_github_is_called_unauthenticated(harness: Harness, web: FakeWeb) -> None:
    harness.script_readers()
    harness.script_synth(synth_output(["github"]))
    await harness.run(AnalyzeRequest(links=("https://github.com/someone",)))
    assert web.github_calls == [("https://github.com/someone", "")]  # "" means: do not read $GITHUB_TOKEN


# ───────────────────────── triage in a run ─────────────────────────


async def test_a_skipped_page_is_a_failed_source_and_the_rest_carries_on(
    harness: Harness, web: FakeWeb
) -> None:
    verdict = TriageResult(skip=True, skip_reason="This page is behind a login wall.")
    harness.rebuild(decisions=FakeDecisionService(triage=verdict))
    harness.script_readers()
    harness.script_synth(synth_output(["resume", "portfolio-1"]))
    events = await harness.run(
        AnalyzeRequest(
            resume_bytes=RESUME_TEXT.encode(),
            resume_name="resume.txt",
            links=("https://example.com/portfolio",),
        )
    )
    assert_skeleton(events)
    profile = done_of(events)
    page = next(s for s in profile.sources if s.id == "portfolio-1")
    assert (page.ok, page.error) == (False, "This page is behind a login wall.")
    assert next(s for s in profile.sources if s.id == "resume").ok
    card = [e.reader for e in events if isinstance(e, ReaderEvent) and e.reader.sourceId == "portfolio-1"][-1]
    assert card.status == "error"
    # the reader of the skipped page was cancelled before it could finish; the other one ran
    (synth_call,) = harness.claude.calls_for("synth")
    assert "could NOT be read" in synth_call.user_text and "portfolio-1" in synth_call.user_text


async def test_a_label_and_notes_from_triage_reach_the_profile(harness: Harness, web: FakeWeb) -> None:
    verdict = TriageResult(
        label="Blog",
        notes=["Not clearly about one person's work."],
        log_lines=["Looks like a blog, not a portfolio"],
    )
    harness.rebuild(decisions=FakeDecisionService(triage=verdict))
    harness.script_readers()
    harness.script_synth(synth_output(["resume", "portfolio-1"]))
    events = await harness.run(
        AnalyzeRequest(
            resume_bytes=RESUME_TEXT.encode(),
            resume_name="resume.txt",
            links=("https://example.com/portfolio",),
        )
    )
    profile = done_of(events)
    page = next(s for s in profile.sources if s.id == "portfolio-1")
    assert page.label == "Blog"
    assert page.notes[-1] == "Not clearly about one person's work."
    assert page.ok
    assert "Looks like a blog, not a portfolio" in log_texts(events, "web")
    card = [e.reader for e in events if isinstance(e, ReaderEvent) and e.reader.sourceId == "portfolio-1"][-1]
    assert card.kindLabel == "Blog"
    context = (await harness.repos.profiles.get_for_chat(profile.id))[1]  # type: ignore[index]
    assert next(s for s in context.sources if s.id == "portfolio-1").label == "Blog"


# ───────────────────────── the evidence check ─────────────────────────


async def test_evidence_that_fails_the_check_is_dropped_and_summarised(harness: Harness) -> None:
    harness.rebuild(decisions=FakeDecisionService(evidence={("skill-1", "resume"): False}))
    harness.script_readers()
    harness.script_synth(synth_output(["resume", "notes"]))
    events = await harness.run(
        AnalyzeRequest(resume_bytes=RESUME_TEXT.encode(), resume_name="resume.txt", notes="N")
    )
    assert_skeleton(events)
    profile = done_of(events)
    skill1 = next(s for s in profile.skills if s.id == "skill-1")
    assert skill1.evidence == []
    assert next(s for s in profile.skills if s.id == "skill-2").evidence  # others stay
    ((claims, _),) = harness.decisions.evidence_calls
    assert {c.skill_id for c in claims} == {"skill-1", "skill-2", "skill-3", "skill-4", "skill-5", "skill-6"}
    assert all(c.source_text for c in claims)
    jev = [e.text for e in logs(events, "Jev")]
    assert jev == [f"Checked {len(claims)} evidence quotes, dropped 1"]
    # the summary line comes before "Graph ready"
    texts = [e.text for e in logs(events)]
    assert texts.index(jev[0]) < next(i for i, t in enumerate(texts) if t.startswith("Graph ready"))
    # what is stored is the checked profile
    assert (await harness.repos.profiles.get_profile(profile.id)) == profile.to_dict()


async def test_the_checked_graph_ready_line_counts_the_final_graph(harness: Harness) -> None:
    harness.script_readers()
    harness.script_synth(synth_output(["resume"]))
    events = await harness.run(AnalyzeRequest(resume_bytes=RESUME_TEXT.encode(), resume_name="resume.txt"))
    assert "Graph ready · 3 domains, 8 skills, 3 roles" in log_texts(events, "graph")


async def test_a_run_with_decisions_off_shows_no_jev_lines_and_checks_nothing(harness: Harness) -> None:
    from tayyari.decisions.service import NullDecisionService

    harness.decisions = NullDecisionService()  # type: ignore[assignment]
    harness.rebuild()
    harness.script_readers()
    harness.script_synth(synth_output(["resume", "notes"]))
    events = await harness.run(
        AnalyzeRequest(resume_bytes=RESUME_TEXT.encode(), resume_name="resume.txt", notes="N")
    )
    assert_skeleton(events)  # behaves like the legacy pipeline: readers, synthesis, done
    assert logs(events, "Jev") == []
    assert not [e for e in events if isinstance(e, SignalEvent) and e.src == "first read"]
    skills = done_of(events).skills
    assert all(s.evidence for s in skills if s.score >= 45)  # nothing was dropped


# ───────────────────────── shadow decisions ─────────────────────────


async def test_shadow_compare_gets_the_final_profile_after_done(harness: Harness) -> None:
    harness.script_readers(readers=sample_readers())
    harness.script_synth(sample_synth())
    events = await harness.run(AnalyzeRequest(sample=True))
    profile = done_of(events)
    ((materials, ctx),) = harness.decisions.shadow_calls
    assert isinstance(materials, ShadowMaterials)
    assert materials.profile == profile
    assert set(materials.reader_outputs) == {"resume", "github", "lovable-1", "portfolio-1"}
    assert materials.reader_outputs["github"]["sub"] == sample_readers()["github"]["sub"]
    assert materials.resume_text is not None and "Kitebird" in materials.resume_text
    assert (ctx.profile_id, ctx.is_sample) == (profile.id, True)


async def test_shadow_compare_is_not_called_when_the_analysis_fails(harness: Harness) -> None:
    harness.script_readers()
    harness.script_synth(tiny_synth(), tiny_synth())
    events = await harness.run(AnalyzeRequest(resume_bytes=RESUME_TEXT.encode(), resume_name="resume.txt"))
    assert isinstance(events[-1], ErrorEvent)
    assert harness.decisions.shadow_calls == []


async def test_a_shadow_failure_cannot_break_the_stream(harness: Harness) -> None:
    harness.rebuild(decisions=FakeDecisionService().fail("shadow_compare", RuntimeError("shadow broke")))
    harness.script_readers()
    harness.script_synth(synth_output(["resume"]))
    events = await harness.run(AnalyzeRequest(resume_bytes=RESUME_TEXT.encode(), resume_name="resume.txt"))
    assert_skeleton(events)
    assert types_of(events).count("error") == 0


# ───────────────────────── the graph-size gate ─────────────────────────


async def test_a_graph_that_is_too_small_is_retried_once(harness: Harness) -> None:
    harness.script_readers()
    harness.script_synth(tiny_synth(), synth_output(["resume"]))
    events = await harness.run(AnalyzeRequest(resume_bytes=RESUME_TEXT.encode(), resume_name="resume.txt"))
    assert_skeleton(events)
    assert log_texts(events, "graph").count("Tightening the graph, one more pass") == 1
    first, second = harness.claude.calls_for("synth")
    assert "IMPORTANT — your previous attempt failed" not in first.user_text
    assert "IMPORTANT — your previous attempt failed" in second.user_text
    assert "graph too small (1 domains, 1 skills, 0 roles)" in second.user_text
    assert done_of(events).skills


async def test_a_graph_that_stays_too_small_ends_in_an_error(harness: Harness) -> None:
    harness.script_readers()
    harness.script_synth(tiny_synth(), tiny_synth())
    events = await harness.run(AnalyzeRequest(resume_bytes=RESUME_TEXT.encode(), resume_name="resume.txt"))
    assert_skeleton(events, expect_done=False)
    assert (
        events[-1].message == "Couldn't build your graph: The model returned an incomplete answer. Try again."
    )  # type: ignore[union-attr]
    assert len(harness.claude.calls_for("synth")) == 2
    assert harness.repos.profiles._s.profiles == {}


# ───────────────────────── pacing ─────────────────────────


async def test_pacing_scale_multiplies_every_delay(make_settings: Callable[..., Settings]) -> None:
    h = Harness(make_settings(pacing_scale=1.0))
    h.rebuild(decisions=FakeDecisionService(first_read=FIRST_READ))
    h.script_readers(readers=sample_readers())
    h.script_synth(sample_synth())
    events = await h.run(AnalyzeRequest(sample=True))
    assert_skeleton(events)
    sleeps = h.clock.sleeps
    assert sleeps.count(0.25) == len(FIRST_READ.lines)  # the first-read lines, 0.25 s apart
    assert 0.15 in sleeps  # signal chips
    assert any(s >= 0.18 for s in sleeps if s != 0.25)  # the sample replay delays
    assert h.clock.total_slept > 2.0


async def test_no_pacing_means_the_clock_is_never_asked_to_wait(harness: Harness) -> None:
    harness.rebuild(decisions=FakeDecisionService(first_read=FIRST_READ))
    harness.script_readers(readers=sample_readers())
    harness.script_synth(sample_synth())
    await harness.run(AnalyzeRequest(sample=True))
    assert harness.clock.sleeps == []


# ───────────────────────── the first read never holds anything up ─────────────────────────


async def test_a_first_read_that_never_answers_delays_neither_readers_nor_synthesis(
    harness: Harness, web: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    monkeypatch.setattr(firstread, "FIRST_READ_GRACE_S", 0.05)
    gate = asyncio.Event()

    class Hanging(FakeDecisionService):
        cancelled = False

        async def first_read(self, inp: Any, ctx: Any) -> Any:
            try:
                await gate.wait()
            except asyncio.CancelledError:
                Hanging.cancelled = True
                raise
            return FIRST_READ

    harness.rebuild(decisions=Hanging())
    harness.script_readers()
    harness.script_synth(synth_output(["resume", "portfolio-1"]))
    events = await asyncio.wait_for(
        harness.run(
            AnalyzeRequest(
                resume_bytes=RESUME_TEXT.encode(),
                resume_name="resume.txt",
                links=("https://example.com/portfolio",),
            )
        ),
        timeout=5,
    )
    assert_skeleton(events)
    assert Hanging.cancelled  # dropped when synthesis started
    assert all(e.kind != "resume" for e in logs(events, "Jev"))  # none of the first read was shown
    assert all(e.reader.status in ("queued", "reading", "done") for e in events if isinstance(e, ReaderEvent))


# ───────────────────────── a scanned resume ─────────────────────────


async def test_a_scanned_resume_is_read_from_the_pdf_itself(harness: Harness) -> None:
    from tests.unit.ingest.helpers import make_pdf

    harness.rebuild(decisions=FakeDecisionService(first_read=FIRST_READ))
    harness.script_readers()
    harness.script_synth(synth_output(["resume"]))
    pdf = make_pdf([[]])  # one page, no selectable text
    events = await harness.run(AnalyzeRequest(resume_bytes=pdf, resume_name="scan.pdf"))
    assert_skeleton(events)
    (reader_call,) = harness.claude.calls_for("reader")
    assert reader_call.pdf == pdf
    assert "original PDF is attached" in reader_call.user_text
    assert harness.decisions.first_read_calls == []  # no text for Jev to read
    (synth_call,) = harness.claude.calls_for("synth")
    assert "<resume_text>" not in synth_call.user_text
    profile = done_of(events)
    assert profile.sources[0].ok
    context = (await harness.repos.profiles.get_for_chat(profile.id))[1]  # type: ignore[index]
    assert context.sources[0].content == ""
    assert "resume" in context.readers


# ───────────────────────── structure of the stream ─────────────────────────


async def test_the_stream_is_lazy_events_arrive_while_work_continues(harness: Harness) -> None:
    import asyncio

    release = asyncio.Event()

    async def slow(call: Any) -> StructuredResult:
        await release.wait()
        raise AssertionError("not reached before the test lets go")

    harness.claude.script_structured("reader", slow)
    agen = harness.service.run(
        AnalyzeRequest(resume_bytes=RESUME_TEXT.encode(), resume_name="resume.txt"), request_context()
    )
    first = await asyncio.wait_for(agen.__anext__(), timeout=5)
    second = await asyncio.wait_for(agen.__anext__(), timeout=5)
    assert (first.type, second.type) == ("readers", "phase")  # type: ignore[union-attr]
    await agen.aclose()
    release.set()


async def test_the_service_satisfies_the_analysis_port(harness: Harness) -> None:
    from tayyari.analysis.ports import AnalysisService

    assert isinstance(harness.service, AnalysisService)
    assert isinstance(harness.service, PipelineAnalysisService)
