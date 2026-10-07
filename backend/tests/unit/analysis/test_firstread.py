"""Jev's first read: when it starts, what it shows, and how it is dropped."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest

from tayyari.analysis import firstread
from tayyari.analysis.firstread import FIRST_READ_LINE_PACE_S, FirstRead
from tayyari.analysis.runctx import RunContext
from tayyari.analysis.signals import SIGNAL_PACE_S, SignalStream
from tayyari.contract import AnalysisEvent, AnalyzeRequest, LogEvent, SignalEvent
from tayyari.core.settings import Settings
from tayyari.decisions.service import DecisionContext, FirstReadInput, FirstReadResult
from tayyari.ingest import PlannedSource, SourceDoc
from tests.fakes.clock import FakeClock
from tests.fakes.decisions import FakeDecisionService
from tests.unit.analysis.support import make_run

RESULT = FirstReadResult(
    lines=["First read: Design and UX, early career", "Depth 3 of 5: competent", "Leads or mentors others"],
    signals=["Leads others", "Measured results", "L" * 50],
    facts={"career_family": "design_ux"},
)


def planned(kind: str, sid: str | None = None) -> PlannedSource:
    sid = sid or kind
    return PlannedSource(sid, kind, sid.title(), f"{kind} page", sid, None, None)  # type: ignore[arg-type]


def doc(kind: str, text: str = "TEXT", **kw: Any) -> SourceDoc:
    return SourceDoc(id=kind, kind=kind, label=kind.title(), src=kind, kind_label=kind, text=text, **kw)


def build(
    settings: Settings,
    kinds: tuple[str, ...],
    *,
    decisions: FakeDecisionService | None = None,
    req: AnalyzeRequest | None = None,
    clock: FakeClock | None = None,
) -> tuple[FirstRead, SignalStream, RunContext, list[AnalysisEvent], FakeDecisionService]:
    decisions = decisions or FakeDecisionService(first_read=RESULT)
    run, events = make_run(settings, decisions=decisions, req=req, clock=clock)
    signals = SignalStream(run)
    return FirstRead(run, signals, [planned(k) for k in kinds]), signals, run, events, decisions


def jev_lines(events: list[AnalysisEvent]) -> list[LogEvent]:
    return [e for e in events if isinstance(e, LogEvent)]


def chips(events: list[AnalysisEvent]) -> list[SignalEvent]:
    return [e for e in events if isinstance(e, SignalEvent)]


# ───────────────────────── when it starts ─────────────────────────


async def test_the_resume_starts_it_with_the_notes_the_person_pasted(settings: Settings) -> None:
    req = AnalyzeRequest(notes="  I lead a small design team.  ")
    fr, _, _, events, decisions = build(settings, ("resume", "notes"), req=req)
    fr.on_ingested(planned("resume"), doc("resume", "RESUME TEXT"))
    assert fr.started
    await fr.settle()
    ((inp, ctx),) = decisions.first_read_calls
    assert inp == FirstReadInput(
        resume_text="RESUME TEXT", notes_text="I lead a small design team.", today=date(2026, 10, 7)
    )
    assert isinstance(ctx, DecisionContext) and ctx.profile_id == "profile-test" and ctx.is_sample is False
    assert [e.text for e in jev_lines(events)] == RESULT.lines


async def test_a_resume_without_notes_sends_none(settings: Settings) -> None:
    fr, _, _, _, decisions = build(settings, ("resume",))
    fr.on_ingested(planned("resume"), doc("resume", "RESUME TEXT"))
    await fr.settle()
    assert decisions.first_read_calls[0][0].notes_text is None


async def test_the_date_comes_from_the_injected_clock(settings: Settings) -> None:
    from datetime import UTC, datetime

    clock = FakeClock(datetime(2027, 3, 4, 23, 59, tzinfo=UTC))
    fr, _, _, _, decisions = build(settings, ("resume",), clock=clock)
    fr.on_ingested(planned("resume"), doc("resume"))
    await fr.settle()
    assert decisions.first_read_calls[0][0].today == date(2027, 3, 4)


async def test_without_a_resume_the_notes_start_it(settings: Settings) -> None:
    fr, _, _, _, decisions = build(settings, ("notes", "link"))
    fr.on_ingested(planned("link"), doc("link", "A PAGE"))
    assert not fr.started  # web pages never start it
    fr.on_ingested(planned("notes"), doc("notes", "MY NOTES"))
    await fr.settle()
    ((inp, _),) = decisions.first_read_calls
    assert (inp.resume_text, inp.notes_text) == (None, "MY NOTES")


async def test_when_a_resume_is_planned_the_notes_alone_do_not_start_it(settings: Settings) -> None:
    fr, _, _, _, decisions = build(settings, ("resume", "notes"))
    fr.on_ingested(planned("notes"), doc("notes", "MY NOTES"))
    assert not fr.started
    assert decisions.first_read_calls == []


@pytest.mark.parametrize("kind", ["github", "link", "portfolio", "lovable", "linkedin"])
async def test_no_other_source_starts_it(settings: Settings, kind: str) -> None:
    fr, _, _, _, decisions = build(settings, (kind,))
    fr.on_ingested(planned(kind), doc(kind, "TEXT"))
    assert not fr.started and decisions.first_read_calls == []


async def test_a_scanned_resume_has_no_text_and_does_not_start_it(settings: Settings) -> None:
    fr, _, _, _, decisions = build(settings, ("resume",))
    scanned = doc("resume", "", pdf_bytes=b"%PDF", meta={"scanned": True})
    fr.on_ingested(planned("resume"), scanned)
    assert not fr.started and decisions.first_read_calls == []


async def test_a_scanned_resume_falls_back_to_the_notes(settings: Settings) -> None:
    fr, _, _, _, decisions = build(settings, ("resume", "notes"), req=AnalyzeRequest(notes="Notes about me"))
    fr.on_ingested(planned("resume"), doc("resume", "", pdf_bytes=b"%PDF", meta={"scanned": True}))
    await fr.settle()
    ((inp, _),) = decisions.first_read_calls
    assert (inp.resume_text, inp.notes_text) == (None, "Notes about me")


async def test_a_resume_that_failed_to_read_falls_back_to_the_notes(settings: Settings) -> None:
    fr, _, _, _, decisions = build(settings, ("resume", "notes"), req=AnalyzeRequest(notes="Notes about me"))
    failed = SourceDoc.failure("resume", "resume", "Resume", "Resume", None, "cv.pdf", "That file is empty.")
    fr.on_ingested(planned("resume"), failed)
    await fr.settle()
    assert decisions.first_read_calls[0][0].resume_text is None


async def test_a_resume_that_failed_with_no_notes_does_not_start_it(settings: Settings) -> None:
    fr, _, _, _, decisions = build(settings, ("resume",))
    failed = SourceDoc.failure("resume", "resume", "Resume", "Resume", None, "cv.pdf", "That file is empty.")
    fr.on_ingested(planned("resume"), failed)
    assert not fr.started and decisions.first_read_calls == []


async def test_blank_notes_do_not_start_it(settings: Settings) -> None:
    fr, _, _, _, _ = build(settings, ("notes",))
    fr.on_ingested(planned("notes"), doc("notes", "  \n "))
    assert not fr.started


async def test_it_runs_at_most_once(settings: Settings) -> None:
    fr, _, _, _, decisions = build(settings, ("resume", "notes"), req=AnalyzeRequest(notes="N"))
    fr.on_ingested(planned("resume"), doc("resume", "R"))
    fr.on_ingested(planned("resume"), doc("resume", "R again"))
    fr.on_ingested(planned("notes"), doc("notes", "N"))
    await fr.settle()
    assert len(decisions.first_read_calls) == 1


# ───────────────────────── what it shows ─────────────────────────


async def test_lines_are_jev_log_lines_of_kind_resume(settings: Settings) -> None:
    fr, _, _, events, _ = build(settings, ("resume",))
    fr.on_ingested(planned("resume"), doc("resume"))
    await fr.settle()
    lines = jev_lines(events)
    assert [(e.who, e.text, e.kind) for e in lines] == [("Jev", t, "resume") for t in RESULT.lines]


async def test_chips_are_first_read_signals_clipped_to_32_characters(settings: Settings) -> None:
    fr, _, _, events, _ = build(settings, ("resume",))
    fr.on_ingested(planned("resume"), doc("resume"))
    await fr.settle()
    shown = chips(events)
    assert [(c.src, c.kind) for c in shown] == [("first read", "resume")] * 3
    assert [c.label for c in shown[:2]] == ["Leads others", "Measured results"]
    assert len(shown[2].label) <= 32 and shown[2].label.endswith("…")


async def test_chips_go_through_the_shared_dedupe_set(settings: Settings) -> None:
    fr, signals, _, events, _ = build(settings, ("resume",))
    await signals.emit([{"label": "leads-others", "detail": "resume"}], "resume")  # same loose key
    fr.on_ingested(planned("resume"), doc("resume"))
    await fr.settle()
    assert [c.label for c in chips(events)] == ["leads-others", "Measured results", chips(events)[2].label]


async def test_lines_and_chips_are_paced_on_the_clock(make_settings: Callable[..., Settings]) -> None:
    clock = FakeClock()
    fr, _, _, _, _ = build(make_settings(pacing_scale=2.0), ("resume",), clock=clock)
    fr.on_ingested(planned("resume"), doc("resume"))
    await fr.settle()
    assert clock.sleeps == [FIRST_READ_LINE_PACE_S * 2] * 3 + [SIGNAL_PACE_S * 2] * 3


async def test_nothing_is_paced_when_pacing_is_off(settings: Settings) -> None:
    clock = FakeClock()
    fr, _, _, _, _ = build(settings, ("resume",), clock=clock)
    fr.on_ingested(planned("resume"), doc("resume"))
    await fr.settle()
    assert clock.sleeps == []


async def test_no_answer_shows_nothing(settings: Settings) -> None:
    fr, _, _, events, decisions = build(settings, ("resume",), decisions=FakeDecisionService(first_read=None))
    fr.on_ingested(planned("resume"), doc("resume"))
    await fr.settle()
    assert len(decisions.first_read_calls) == 1
    assert events == []


async def test_a_failing_service_shows_nothing_and_does_not_raise(settings: Settings) -> None:
    decisions = FakeDecisionService(first_read=RESULT).fail("first_read", RuntimeError("jev down"))
    fr, _, _, events, _ = build(settings, ("resume",), decisions=decisions)
    fr.on_ingested(planned("resume"), doc("resume"))
    await fr.settle()
    assert events == []
    assert fr._task is not None and fr._task.exception() is None  # type: ignore[union-attr]


async def test_a_failure_while_showing_the_lines_is_contained(settings: Settings) -> None:
    fr, _, run, _, _ = build(settings, ("resume",))
    shown: list[AnalysisEvent] = []

    def flaky(event: AnalysisEvent) -> None:
        shown.append(event)
        raise RuntimeError("the stream is gone")

    object.__setattr__(run, "send", flaky)
    fr.on_ingested(planned("resume"), doc("resume"))
    await fr.settle()
    assert len(shown) == 1  # the first line was attempted, then the first read gave up quietly
    assert fr.task is not None and fr.task.done() and fr.task.exception() is None


# ───────────────────────── grace and drop ─────────────────────────


class Gated(FakeDecisionService):
    """A first read that waits for the test before it answers."""

    def __init__(self) -> None:
        super().__init__(first_read=RESULT)
        self.gate = asyncio.Event()
        self.cancelled = False

    async def first_read(self, inp: FirstReadInput, ctx: DecisionContext) -> FirstReadResult | None:
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return await super().first_read(inp, ctx)


async def test_a_first_read_that_finishes_within_the_grace_is_kept(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(firstread, "FIRST_READ_GRACE_S", 2.0)
    decisions = Gated()
    fr, _, _, events, _ = build(settings, ("resume",), decisions=decisions)
    fr.on_ingested(planned("resume"), doc("resume"))
    settling = asyncio.create_task(fr.settle())
    await asyncio.sleep(0.01)
    assert not settling.done()  # waiting for the first read
    decisions.gate.set()
    await settling
    assert [e.text for e in jev_lines(events)] == RESULT.lines
    assert not decisions.cancelled


async def test_a_first_read_still_running_after_the_grace_is_dropped(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(firstread, "FIRST_READ_GRACE_S", 0.05)
    decisions = Gated()
    fr, _, _, events, _ = build(settings, ("resume",), decisions=decisions)
    fr.on_ingested(planned("resume"), doc("resume"))
    await fr.settle()
    assert decisions.cancelled
    assert fr._task is not None and fr._task.cancelled()
    decisions.gate.set()
    await asyncio.sleep(0.01)
    assert events == []  # nothing shows up late


async def test_a_first_read_dropped_while_showing_stops_where_it_is(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    # With pacing on and the real loop, the lines are 0.25 s * scale apart; a tiny grace cuts the sequence short.
    monkeypatch.setattr(firstread, "FIRST_READ_GRACE_S", 0.0)

    class RealSleep:
        """A clock that really waits, so the first read is mid-way through its lines when it is dropped."""

        def now(self) -> Any:
            return FakeClock().now()

        def monotonic(self) -> float:
            return 0.0

        async def sleep(self, seconds: float) -> None:
            await asyncio.sleep(0.2)

    run, events = make_run(make_settings(pacing_scale=1.0), decisions=FakeDecisionService(first_read=RESULT))
    object.__setattr__(run, "clock", RealSleep())
    fr = FirstRead(run, SignalStream(run), [planned("resume")])
    fr.on_ingested(planned("resume"), doc("resume"))
    await asyncio.sleep(0.05)
    await fr.settle()
    assert [e.text for e in jev_lines(events)] == RESULT.lines[:1]
    assert chips(events) == []


async def test_settle_without_a_first_read_is_a_no_op(settings: Settings) -> None:
    fr, _, _, _, _ = build(settings, ("resume",))
    await fr.settle()
    await fr.cancel()


async def test_cancel_stops_a_running_first_read_and_waits_for_it(settings: Settings) -> None:
    decisions = Gated()
    fr, _, _, events, _ = build(settings, ("resume",), decisions=decisions)
    fr.on_ingested(planned("resume"), doc("resume"))
    await asyncio.sleep(0)
    await fr.cancel()
    assert decisions.cancelled and fr._task is not None and fr._task.done()
    assert events == []
