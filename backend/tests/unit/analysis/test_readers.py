"""`SourceReader`: one source from ingest to a `ReadResult`, with Jev's triage alongside for web pages."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from tayyari.analysis import readers
from tayyari.analysis.failures import READER_AUTH_LINE
from tayyari.analysis.firstread import FirstRead
from tayyari.analysis.planning import initial_states
from tayyari.analysis.readers import (
    CHANNEL_THROTTLE_S,
    SKIPPED_BY_TRIAGE,
    ReaderChannel,
    SourceReader,
)
from tayyari.analysis.runctx import RunContext
from tayyari.analysis.schemas import READER_SCHEMA
from tayyari.analysis.signals import SignalStream
from tayyari.contract import AnalysisEvent, AnalyzeRequest, LogEvent, ReaderEvent, SignalEvent
from tayyari.core.clock import SystemClock
from tayyari.core.settings import Settings
from tayyari.decisions.service import DecisionContext, TriageInput, TriageResult
from tayyari.ingest import PlannedSource, SourceDoc
from tayyari.llm.errors import (
    NETWORK_MESSAGE,
    ClaudeAuthError,
    ClaudeNotConfigured,
    ClaudeRateLimited,
)
from tayyari.prompts import READER_SYSTEM
from tests.fakes.claude import FakeClaudeClient, StructuredCall
from tests.fakes.decisions import FakeDecisionService
from tests.unit.analysis.support import make_run, other_tasks, reader_output, source_id_of


@dataclass
class Rig:
    reader: SourceReader
    run: RunContext
    events: list[AnalysisEvent]
    channels: dict[str, ReaderChannel]
    claude: FakeClaudeClient
    decisions: FakeDecisionService
    first_read: FirstRead
    signals: SignalStream


def planned(kind: str, sid: str, doc: SourceDoc | Callable[[], SourceDoc]) -> PlannedSource:
    async def ingest(log: Callable[[str], None]) -> SourceDoc:
        return doc() if callable(doc) else doc

    return PlannedSource(sid, kind, sid.title(), f"{kind} source", sid, None, ingest)


def doc(kind: str, sid: str, text: str = "PAGE TEXT", **kw: Any) -> SourceDoc:
    kw.setdefault("label", sid.title())
    kw.setdefault("kind_label", f"{kind} source")
    return SourceDoc(id=sid, kind=kind, src=sid, text=text, **kw)


def build(
    settings: Settings,
    sources: list[PlannedSource],
    *,
    claude: FakeClaudeClient | None = None,
    decisions: FakeDecisionService | None = None,
    scale_clock: bool = False,
    req: AnalyzeRequest | None = None,
) -> Rig:
    claude = claude or FakeClaudeClient()
    decisions = decisions or FakeDecisionService()
    run, events = make_run(
        settings,
        claude=claude,
        decisions=decisions,
        req=req,
        clock=SystemClock() if scale_clock else None,  # type: ignore[arg-type]
    )
    states = initial_states(sources)
    channels = {
        pid: ReaderChannel(s, run.send, now=run.clock.monotonic, throttle_s=CHANNEL_THROTTLE_S * run.scale)
        for pid, s in states.items()
    }
    signals = SignalStream(run)
    first_read = FirstRead(run, signals, sources)
    reader = SourceReader(
        run, channels, signals, first_read, asyncio.Semaphore(settings.concurrency.reader_concurrency)
    )
    return Rig(reader, run, events, channels, claude, decisions, first_read, signals)


def cards(rig: Rig, sid: str) -> list[ReaderEvent]:
    return [e for e in rig.events if isinstance(e, ReaderEvent) and e.reader.sourceId == sid]


def log_texts(rig: Rig) -> list[tuple[str, str, str | None]]:
    return [(e.who, e.text, e.kind) for e in rig.events if isinstance(e, LogEvent)]


def chips(rig: Rig) -> list[tuple[str, str]]:
    return [(e.label, e.src) for e in rig.events if isinstance(e, SignalEvent)]


# ───────────────────────── the happy path ─────────────────────────


async def test_a_source_is_read_and_reported_step_by_step(settings: Settings) -> None:
    source = doc(
        "github",
        "github",
        label="GitHub",
        quick_signals=["TypeScript · 17 repos", "CI"],
        meta={"topRepos": ["motion-kit"]},
    )
    rig = build(settings, [planned("github", "github", source)])
    rig.claude.script_structured("reader", reader_output("github"))
    result = await rig.reader.read(planned("github", "github", source))
    assert result.error is None and result.ai_error is None
    assert result.reader is not None and result.reader["skills"][0]["name"] == "React"
    assert result.raw is not None and result.planned.id == "github"

    states = [(e.reader.status, e.reader.progress) for e in cards(rig, "github")]
    assert states[0] == ("reading", 6)
    assert states[-1] == ("done", 100)
    final = cards(rig, "github")[-1].reader
    assert final.line == "3 things · 2 skills extracted"

    texts = log_texts(rig)
    assert ("github", "GitHub read · 2 skills, 3 signals", "github") in texts
    assert ("github", "Note from github: add numbers.", "github") in texts  # the first note only
    assert not any(t == "Second note." for _, t, _ in texts)

    await rig.signals.settle()
    labels = [label for label, _ in chips(rig)]
    assert labels[:2] == ["TypeScript", "CI"]  # the quick (deterministic) chips come first
    assert "Signal A of github" in labels and "React" in labels
    assert dict(chips(rig))["TypeScript"] == "17 repos"
    assert (
        dict(chips(rig))["Signal B of github"] == "GitHub"
    )  # an empty detail falls back to the source's name


async def test_the_reader_call_has_the_right_shape(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(reader_model="claude-haiku-4-5", reader_max_tokens=4321)
    source = doc("portfolio", "portfolio-1", url="https://example.com/work", meta={"pages": 3})
    rig = build(settings, [planned("portfolio", "portfolio-1", source)])
    rig.claude.script_structured("reader", reader_output("portfolio-1"))
    await rig.reader.read(planned("portfolio", "portfolio-1", source))
    (call,) = rig.claude.structured_calls
    assert call.model == "claude-haiku-4-5"
    assert (call.effort, call.max_tokens, call.pdf) == ("low", 4321, None)
    assert call.schema is READER_SCHEMA
    assert [b.text for b in call.system] == [READER_SYSTEM("portfolio")]
    assert [b.cache for b in call.system] == [False]  # Haiku prompts are below the cacheable minimum
    assert "Source id: portfolio-1" in call.user_text
    assert "<source_content>\nPAGE TEXT\n</source_content>" in call.user_text
    assert "URL: https://example.com/work" in call.user_text
    ctx = call.ctx
    assert (ctx.purpose, ctx.request_id, ctx.profile_id, ctx.ip_hash) == (
        "reader",
        "req-test",
        "profile-test",
        "iphash0123456789",
    )
    assert ctx.analysis_id == rig.run.analysis_id


async def test_ingest_log_lines_become_log_events_and_move_the_card(settings: Settings) -> None:
    async def ingest(log: Callable[[str], None]) -> SourceDoc:
        log("Fetched profile, 34 public repos")
        log("Ranked repos by recency and stars")
        return doc("github", "github")

    p = PlannedSource("github", "github", "GitHub", "GitHub profile", "github.com/x", None, ingest)
    rig = build(settings, [p])
    rig.claude.script_structured("reader", reader_output("github"))
    await rig.reader.read(p)
    texts = log_texts(rig)
    assert texts[:2] == [
        ("github", "Fetched profile, 34 public repos", "github"),
        ("github", "Ranked repos by recency and stars", "github"),
    ]
    lines = [e.reader.line for e in cards(rig, "github")]
    assert "Fetched profile, 34 public repos" in lines
    progress = [e.reader.progress for e in cards(rig, "github") if e.reader.status == "reading"]
    assert progress == sorted(progress)  # never goes back


async def test_log_lines_use_the_short_who_name_of_the_kind(settings: Settings) -> None:
    for kind, who in [
        ("resume", "resume"),
        ("link", "web"),
        ("portfolio", "web"),
        ("lovable", "lovable"),
        ("notes", "notes"),
    ]:
        rig = build(settings, [planned(kind, "x", doc(kind, "x"))])
        rig.claude.script_structured("reader", reader_output("x"))
        await rig.reader.read(planned(kind, "x", doc(kind, "x")))
        assert {w for w, _, _ in log_texts(rig)} == {who}


async def test_the_card_takes_the_label_and_src_the_ingest_found(settings: Settings) -> None:
    source = SourceDoc(
        id="x", kind="link", label="Kaggle", src="kaggle.com/riya", kind_label="Kaggle profile", text="T"
    )
    rig = build(settings, [planned("link", "link-1", source)])
    rig.claude.script_structured("reader", reader_output("link-1"))
    await rig.reader.read(planned("link", "link-1", source))
    final = cards(rig, "link-1")[-1].reader
    assert (final.kindLabel, final.src) == ("Kaggle profile", "kaggle.com/riya")


async def test_reader_progress_follows_the_characters_generated(settings: Settings) -> None:
    source = doc("link", "link-1")
    rig = build(settings, [planned("link", "link-1", source)])
    seen: list[int] = []

    def answer(call: StructuredCall) -> dict[str, Any]:
        assert call.on_progress is not None
        card = rig.channels["link-1"].state
        for chars in (900, 1800, 500, 9000):
            call.on_progress(chars)
            seen.append(card.progress)
        return reader_output("link-1")

    rig.claude.script_structured("reader", answer)
    await rig.reader.read(planned("link", "link-1", source))
    assert seen[1] == 35 + round(57 * (1 - math.exp(-1)))
    assert seen[2] == seen[1]  # a smaller count never lowers the bar
    assert seen == sorted(seen)
    assert seen[3] <= 92


# ───────────────────────── scanned PDFs ─────────────────────────


async def test_a_scanned_resume_attaches_the_pdf(settings: Settings) -> None:
    source = doc("resume", "resume", text="", pdf_bytes=b"%PDF-scan", meta={"scanned": True})
    rig = build(settings, [planned("resume", "resume", source)])
    rig.claude.script_structured("reader", reader_output("resume"))
    result = await rig.reader.read(planned("resume", "resume", source))
    (call,) = rig.claude.structured_calls
    assert call.pdf == b"%PDF-scan"
    assert "original PDF is attached" in call.user_text
    assert result.reader is not None and result.error is None
    assert rig.decisions.first_read_calls == []  # nothing to read for Jev


async def test_the_pdf_is_not_attached_when_the_text_was_extracted(settings: Settings) -> None:
    source = doc("resume", "resume", text="Real extracted text", pdf_bytes=b"%PDF-1")
    rig = build(settings, [planned("resume", "resume", source)])
    rig.claude.script_structured("reader", reader_output("resume"))
    await rig.reader.read(planned("resume", "resume", source))
    assert rig.claude.structured_calls[0].pdf is None


# ───────────────────────── failures ─────────────────────────


async def test_a_source_that_failed_to_ingest_never_reaches_the_reader(settings: Settings) -> None:
    failed = SourceDoc.failure(
        "link-1",
        "link",
        "Web",
        "Web page",
        None,
        "example.com",
        "That page needs a login. Try a public link.",
    )
    p = planned("link", "link-1", failed)
    rig = build(settings, [p])
    result = await rig.reader.read(p)
    assert rig.claude.structured_calls == []
    assert rig.decisions.triage_calls == []
    assert result.reader is None and result.ai_error is None
    assert result.error == "That page needs a login. Try a public link."
    card = cards(rig, "link-1")[-1].reader
    assert (card.status, card.line) == ("error", "That page needs a login")
    assert ("web", "Could not read Web: That page needs a login. Try a public link.", "link") in log_texts(
        rig
    )


async def test_a_doc_that_is_not_ok_but_has_no_message_gets_a_generic_one(settings: Settings) -> None:
    bad = doc("link", "link-1", ok=True)
    bad.ok = False
    p = planned("link", "link-1", bad)
    rig = build(settings, [p])
    result = await rig.reader.read(p)
    assert result.error == "Could not read this source."
    assert cards(rig, "link-1")[-1].reader.line == "Could not read this source"


async def test_an_ingest_that_raises_is_a_failed_source(settings: Settings) -> None:
    async def boom(log: Callable[[str], None]) -> SourceDoc:
        raise RuntimeError("parser exploded")

    p = PlannedSource("link-1", "link", "Web", "Web page", "x", None, boom)
    rig = build(settings, [p])
    result = await rig.reader.read(p)
    assert result.error == "Could not read this source."
    assert result.reader is None and rig.claude.structured_calls == []


async def test_a_failed_reader_is_reported_with_its_typed_message(settings: Settings) -> None:
    p = planned("github", "github", doc("github", "github"))
    rig = build(settings, [p])
    rig.claude.script_structured("reader", ClaudeRateLimited())
    result = await rig.reader.read(p)
    assert result.reader is None
    assert isinstance(result.ai_error, ClaudeRateLimited)
    assert result.error == "Claude is busy right now (rate limited or overloaded). Try again in a minute."
    card = cards(rig, "github")[-1].reader
    assert card.status == "error" and card.line.startswith("Claude is busy right now")
    assert len(card.line) <= 64
    assert ("github", f"Reader failed: {card.line}", "github") in log_texts(rig)
    assert result.raw is not None  # the fetched document is kept for the synthesis prompt


@pytest.mark.parametrize("error", [ClaudeAuthError(), ClaudeNotConfigured()])
async def test_a_key_problem_shows_the_skipped_line(settings: Settings, error: Exception) -> None:
    p = planned("github", "github", doc("github", "github"))
    rig = build(settings, [p])
    rig.claude.script_structured("reader", error)
    result = await rig.reader.read(p)
    assert cards(rig, "github")[-1].reader.line == READER_AUTH_LINE
    assert result.ai_error is error


async def test_an_unexpected_exception_is_reported_without_its_text(settings: Settings) -> None:
    p = planned("github", "github", doc("github", "github"))
    rig = build(settings, [p])
    rig.claude.script_structured("reader", RuntimeError("secret resume text in an error"))
    result = await rig.reader.read(p)
    assert result.error == "Something went wrong."
    assert "secret" not in str(log_texts(rig))
    assert "secret" not in cards(rig, "github")[-1].reader.line


async def test_a_reader_that_takes_too_long_times_out(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(concurrency={"reader_timeout_s": 0.05})
    p = planned("github", "github", doc("github", "github"))
    rig = build(settings, [p])

    async def hang(call: StructuredCall) -> Any:
        await asyncio.sleep(30)

    rig.claude.script_structured("reader", hang)
    result = await rig.reader.read(p)
    assert result.reader is None
    assert result.error == NETWORK_MESSAGE
    assert cards(rig, "github")[-1].reader.status == "error"


async def test_cancelling_a_read_cancels_its_claude_call(settings: Settings) -> None:
    p = planned("github", "github", doc("github", "github"))
    rig = build(settings, [p])
    started, finished = asyncio.Event(), asyncio.Event()

    async def hang(call: StructuredCall) -> Any:
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            finished.set()

    rig.claude.script_structured("reader", hang)
    task = asyncio.create_task(rig.reader.read(p))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    assert other_tasks() == set()


async def test_the_semaphore_limits_concurrent_reader_calls(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(concurrency={"reader_concurrency": 2})
    sources = [planned("link", f"link-{i}", doc("link", f"link-{i}")) for i in range(1, 6)]
    rig = build(settings, sources)
    active = peak = 0

    async def slow(call: StructuredCall) -> dict[str, Any]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return reader_output(source_id_of(call))

    rig.claude.script_structured("reader", slow, repeat=True)
    results = await asyncio.gather(*(rig.reader.read(p) for p in sources))
    assert all(r.reader for r in results)
    assert peak == 2


# ───────────────────────── the activity ticker ─────────────────────────


async def test_the_card_line_rotates_while_a_reader_works(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(pacing_scale=0.02)  # a tick every 28 ms, a throttle of 5 ms
    source = doc("notes", "notes", text="Some notes")
    p = planned("notes", "notes", source)
    rig = build(settings, [p], scale_clock=True)
    release = asyncio.Event()

    async def slow(call: StructuredCall) -> dict[str, Any]:
        await release.wait()
        return reader_output("notes")

    rig.claude.script_structured("reader", slow)
    task = asyncio.create_task(rig.reader.read(p))
    await asyncio.sleep(0.25)
    mid = [e.reader.line for e in cards(rig, "notes")]
    assert {"Reading your notes", "Separating claims from goals"} <= set(mid)
    progress = [e.reader.progress for e in cards(rig, "notes")]
    assert progress == sorted(progress) and progress[-1] <= 90
    release.set()
    await task
    after = len(cards(rig, "notes"))
    await asyncio.sleep(0.1)
    assert len(cards(rig, "notes")) == after  # the ticker stopped with the reader
    assert cards(rig, "notes")[-1].reader.status == "done"


async def test_there_is_no_ticker_when_pacing_is_off(settings: Settings) -> None:
    source = doc("notes", "notes", text="Some notes")
    p = planned("notes", "notes", source)
    rig = build(settings, [p])
    release = asyncio.Event()

    async def slow(call: StructuredCall) -> dict[str, Any]:
        await release.wait()
        return reader_output("notes")

    rig.claude.script_structured("reader", slow)
    task = asyncio.create_task(rig.reader.read(p))
    await asyncio.sleep(0.05)
    before = len(cards(rig, "notes"))
    await asyncio.sleep(0.05)
    assert len(cards(rig, "notes")) == before
    assert len(other_tasks()) <= 3  # the read, its reader task and the quick-signals batch; no ticker
    release.set()
    await task


# ───────────────────────── the first read hook ─────────────────────────


async def test_reading_a_resume_starts_the_first_read(settings: Settings) -> None:
    source = doc("resume", "resume", text="RESUME TEXT")
    p = planned("resume", "resume", source)
    decisions = FakeDecisionService()
    rig = build(settings, [p], decisions=decisions)
    rig.claude.script_structured("reader", reader_output("resume"))
    await rig.reader.read(p)
    await rig.first_read.settle()
    assert decisions.first_read_calls[0][0].resume_text == "RESUME TEXT"


# ───────────────────────── triage ─────────────────────────


@pytest.mark.parametrize("kind", ["link", "portfolio", "lovable", "linkedin"])
async def test_web_sources_are_triaged_with_their_host_title_and_text(settings: Settings, kind: str) -> None:
    source = doc(
        kind, f"{kind}-1", text="PAGE BODY", url="https://www.example.com/work?x=1", meta={"title": "My Work"}
    )
    p = planned(kind, f"{kind}-1", source)
    rig = build(settings, [p])
    rig.claude.script_structured("reader", reader_output(f"{kind}-1"))
    await rig.reader.read(p)
    ((inp, ctx),) = rig.decisions.triage_calls
    assert inp == TriageInput(
        source_id=f"{kind}-1", kind=kind, host="www.example.com", title="My Work", text="PAGE BODY"
    )
    assert isinstance(ctx, DecisionContext) and ctx.analysis_id == rig.run.analysis_id


@pytest.mark.parametrize("kind", ["resume", "notes", "github"])
async def test_resume_notes_and_github_are_never_triaged(settings: Settings, kind: str) -> None:
    p = planned(kind, kind, doc(kind, kind))
    rig = build(settings, [p])
    rig.claude.script_structured("reader", reader_output(kind))
    await rig.reader.read(p)
    assert rig.decisions.triage_calls == []


async def test_a_page_without_a_title_or_url_is_triaged_with_none(settings: Settings) -> None:
    p = planned("link", "link-1", doc("link", "link-1", meta={"title": 5}))
    rig = build(settings, [p])
    rig.claude.script_structured("reader", reader_output("link-1"))
    await rig.reader.read(p)
    inp = rig.decisions.triage_calls[0][0]
    assert (inp.host, inp.title) == (None, None)


class SlowTriage(FakeDecisionService):
    """Triage that answers when the test says so."""

    def __init__(self, result: TriageResult) -> None:
        super().__init__(triage=result)
        self.gate = asyncio.Event()
        self.cancelled = False

    async def triage(self, inp: TriageInput, ctx: DecisionContext) -> TriageResult:
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return await super().triage(inp, ctx)


async def test_a_skip_verdict_cancels_the_reader_and_fails_the_source(settings: Settings) -> None:
    verdict = TriageResult(
        skip=True, skip_reason="This page is behind a login wall.", text_quality="login_or_block_wall"
    )
    decisions = FakeDecisionService(triage=verdict)
    source = doc("linkedin", "linkedin", text="Sign in to see more")
    p = planned("linkedin", "linkedin", source)
    rig = build(settings, [p], decisions=decisions)
    reader_cancelled = asyncio.Event()

    async def slow(call: StructuredCall) -> dict[str, Any]:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            reader_cancelled.set()
            raise
        return {}

    rig.claude.script_structured("reader", slow)
    result = await rig.reader.read(p)
    assert reader_cancelled.is_set()
    assert result.reader is None and result.ai_error is None
    assert result.error == "This page is behind a login wall."
    card = cards(rig, "linkedin")[-1].reader
    assert (card.status, card.line) == ("error", "This page is behind a login wall")
    assert (
        "linkedin",
        "Could not read Linkedin: This page is behind a login wall.",
        "linkedin",
    ) in log_texts(rig)
    assert other_tasks() == set()


async def test_a_skip_without_a_reason_uses_the_default_reason(settings: Settings) -> None:
    decisions = FakeDecisionService(triage=TriageResult(skip=True))
    p = planned("link", "link-1", doc("link", "link-1"))
    rig = build(settings, [p], decisions=decisions)
    rig.claude.script_structured("reader", reader_output("link-1"))
    result = await rig.reader.read(p)
    assert result.error == SKIPPED_BY_TRIAGE


async def test_a_skip_that_arrives_with_the_reader_still_wins(settings: Settings) -> None:
    # Both answer in the same loop turn here (instant fakes): the verdict is looked at first.
    decisions = FakeDecisionService(triage=TriageResult(skip=True, skip_reason="Empty page."))
    p = planned("link", "link-1", doc("link", "link-1"))
    rig = build(settings, [p], decisions=decisions)
    rig.claude.script_structured("reader", reader_output("link-1"))
    result = await rig.reader.read(p)
    assert result.reader is None and result.error == "Empty page."


async def test_a_skip_that_arrives_after_the_reader_finished_is_ignored(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readers, "TRIAGE_GRACE_S", 2.0)
    decisions = SlowTriage(
        TriageResult(
            skip=True,
            skip_reason="Too late.",
            label="Blog",
            notes=["Looks thin."],
            log_lines=["Jev says wall"],
        )
    )
    p = planned("link", "link-1", doc("link", "link-1", label="Kaggle"))
    rig = build(settings, [p], decisions=decisions)
    rig.claude.script_structured("reader", reader_output("link-1"))
    task = asyncio.create_task(rig.reader.read(p))
    await asyncio.sleep(0.02)
    assert not task.done()  # the reader is done, the source waits a moment for its verdict
    decisions.gate.set()
    result = await task
    assert result.reader is not None and result.error is None  # the page was read: the late skip is ignored
    assert (
        result.raw is not None and result.raw.label == "Kaggle"
    )  # and so is everything else in that verdict
    assert result.extra_notes == []
    assert all(text != "Jev says wall" for _, text, _ in log_texts(rig))


async def test_a_skip_stops_the_chips_of_the_page_nobody_can_read(
    make_settings: Callable[..., Settings],
) -> None:
    quick = ["Chip A", "Chip B", "Chip C", "Chip D"]
    source = doc("link", "link-1", quick_signals=quick)
    p = planned("link", "link-1", source)
    decisions = FakeDecisionService(triage=TriageResult(skip=True, skip_reason="Login wall."))
    rig = build(
        make_settings(pacing_scale=1.0), [p], decisions=decisions, scale_clock=True
    )  # 0.15 s between chips
    rig.claude.script_structured("reader", reader_output("link-1"))
    result = await rig.reader.read(p)
    assert result.error == "Login wall."
    await rig.signals.settle()
    assert len(chips(rig)) < len(quick)
    assert other_tasks() == set()


async def test_a_verdict_that_arrives_within_the_grace_still_labels_the_source(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readers, "TRIAGE_GRACE_S", 2.0)
    decisions = SlowTriage(TriageResult(label="Blog", notes=["A note."], log_lines=["Looks like a blog"]))
    p = planned("link", "link-1", doc("link", "link-1", label="Kaggle"))
    rig = build(settings, [p], decisions=decisions)
    rig.claude.script_structured("reader", reader_output("link-1"))
    task = asyncio.create_task(rig.reader.read(p))
    await asyncio.sleep(0.02)
    assert not task.done()  # the reader is done; the source waits a moment for its label
    decisions.gate.set()
    result = await task
    assert result.reader is not None
    assert result.raw is not None and result.raw.label == "Blog"
    assert result.extra_notes == ["A note."]
    assert ("web", "Looks like a blog", "link") in log_texts(rig)


async def test_a_url_that_cannot_be_parsed_gives_no_host(settings: Settings) -> None:
    source = doc("link", "link-1", url="http://[::1")  # urlsplit raises ValueError for this
    p = planned("link", "link-1", source)
    rig = build(settings, [p])
    rig.claude.script_structured("reader", reader_output("link-1"))
    await rig.reader.read(p)
    assert rig.decisions.triage_calls[0][0].host is None


async def test_the_label_notes_and_log_lines_of_a_verdict_are_applied(settings: Settings) -> None:
    verdict = TriageResult(
        label="Blog",
        notes=["Not clearly about one person's work.", "  "],
        log_lines=["Looks like a blog, not a portfolio"],
        text_quality="readable",
    )
    source = doc("link", "link-1", label="Web page", kind_label="Web page")
    p = planned("link", "link-1", source)
    rig = build(settings, [p], decisions=FakeDecisionService(triage=verdict))
    rig.claude.script_structured("reader", reader_output("link-1"))
    result = await rig.reader.read(p)
    assert result.reader is not None
    assert result.raw is not None and (result.raw.label, result.raw.kind_label) == ("Blog", "Blog")
    assert result.extra_notes == ["Not clearly about one person's work."]
    assert ("web", "Looks like a blog, not a portfolio", "link") in log_texts(rig)
    assert cards(rig, "link-1")[-1].reader.kindLabel == "Blog"
    assert ("web", "Blog read · 2 skills, 3 signals", "link") in log_texts(rig)


async def test_a_verdict_without_a_label_leaves_the_label_alone(settings: Settings) -> None:
    source = doc("link", "link-1", label="Kaggle")
    p = planned("link", "link-1", source)
    rig = build(settings, [p], decisions=FakeDecisionService(triage=TriageResult()))
    rig.claude.script_structured("reader", reader_output("link-1"))
    result = await rig.reader.read(p)
    assert result.raw is not None and result.raw.label == "Kaggle"
    assert result.extra_notes == []


async def test_a_verdict_that_takes_longer_than_the_grace_is_dropped(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readers, "TRIAGE_GRACE_S", 0.05)
    decisions = SlowTriage(TriageResult(label="Blog", skip=True, skip_reason="x"))
    source = doc("link", "link-1", label="Kaggle")
    p = planned("link", "link-1", source)
    rig = build(settings, [p], decisions=decisions)
    rig.claude.script_structured("reader", reader_output("link-1"))
    result = await rig.reader.read(p)
    assert decisions.cancelled
    assert result.reader is not None
    assert result.raw is not None and result.raw.label == "Kaggle"
    assert result.extra_notes == []
    await rig.signals.settle()
    assert other_tasks() == set()


async def test_a_failing_triage_is_a_neutral_verdict(settings: Settings) -> None:
    decisions = FakeDecisionService(triage=TriageResult(label="Blog")).fail(
        "triage", RuntimeError("jev down")
    )
    p = planned("link", "link-1", doc("link", "link-1", label="Kaggle"))
    rig = build(settings, [p], decisions=decisions)
    rig.claude.script_structured("reader", reader_output("link-1"))
    result = await rig.reader.read(p)
    assert result.reader is not None and result.error is None
    assert result.raw is not None and result.raw.label == "Kaggle"


async def test_a_failing_reader_still_applies_nothing_and_cleans_up_triage(settings: Settings) -> None:
    decisions = SlowTriage(TriageResult(label="Blog"))
    p = planned("link", "link-1", doc("link", "link-1"))
    rig = build(settings, [p], decisions=decisions)
    rig.claude.script_structured("reader", ClaudeRateLimited())
    result = await rig.reader.read(p)
    assert isinstance(result.ai_error, ClaudeRateLimited)
    assert decisions.cancelled
    assert other_tasks() == set()


async def test_cancelling_a_read_cancels_the_reader_and_the_triage(settings: Settings) -> None:
    decisions = SlowTriage(TriageResult())
    p = planned("link", "link-1", doc("link", "link-1"))
    rig = build(settings, [p], decisions=decisions)
    started = asyncio.Event()

    async def hang(call: StructuredCall) -> Any:
        started.set()
        await asyncio.sleep(30)

    rig.claude.script_structured("reader", hang)
    task = asyncio.create_task(rig.reader.read(p))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert decisions.cancelled
    assert other_tasks() == set()
