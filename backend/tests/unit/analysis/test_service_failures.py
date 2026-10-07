"""`PipelineAnalysisService` when things go wrong: the failure gate, typed errors, Jev outages, cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest

from tayyari.analysis import firstread
from tayyari.contract import AnalyzeRequest, DoneEvent, ErrorEvent, ReaderEvent, RequestContext
from tayyari.core.errors import RateLimited
from tayyari.decisions.service import DecisionContext, EvidenceClaim, EvidenceVerdict, FirstReadInput
from tayyari.llm.errors import (
    AUTH_MESSAGE,
    BUSY_MESSAGE,
    INCOMPLETE_MESSAGE,
    NETWORK_MESSAGE,
    SPEND_CEILING_MESSAGE,
    ClaudeAuthError,
    ClaudeInvalidOutput,
    ClaudeNotConfigured,
    ClaudeOverloaded,
    ClaudeRateLimited,
    ClaudeRefusal,
    ClaudeTimeout,
    ClaudeTruncated,
    SpendCeilingReached,
)
from tests.fakes.claude import StructuredCall
from tests.fakes.decisions import FakeDecisionService
from tests.unit.analysis.conftest import FakeWeb
from tests.unit.analysis.support import (
    Harness,
    assert_skeleton,
    assert_valid_lines,
    other_tasks,
    request_context,
    synth_output,
    types_of,
)
from tests.unit.analysis.test_service import RESUME_TEXT, done_of

RESUME_ONLY = AnalyzeRequest(resume_bytes=RESUME_TEXT.encode(), resume_name="resume.txt")
RESUME_AND_LINK = AnalyzeRequest(
    resume_bytes=RESUME_TEXT.encode(), resume_name="resume.txt", links=("https://example.com/portfolio",)
)


def error_of(events: list[Any]) -> str:
    last = events[-1]
    assert isinstance(last, ErrorEvent), types_of(events)
    return last.message


# ───────────────────────── before any reader runs ─────────────────────────


async def test_nothing_to_analyse(harness: Harness) -> None:
    events = await harness.run(AnalyzeRequest())
    assert types_of(events) == ["error"]
    assert error_of(events) == "Add a resume, a link or some notes to analyse."
    assert harness.claude.calls == []


async def test_blank_notes_are_nothing_to_analyse(harness: Harness) -> None:
    events = await harness.run(AnalyzeRequest(notes="   "))
    assert error_of(events) == "Add a resume, a link or some notes to analyse."


# ───────────────────────── the reader failure gate ─────────────────────────


async def test_one_unreadable_source_says_why(harness: Harness) -> None:
    events = await harness.run(AnalyzeRequest(resume_bytes=b"", resume_name="resume.pdf"))
    assert_skeleton(events, expect_done=False)
    assert (
        error_of(events)
        == "We couldn't read that source: That file is empty. Please upload your resume again."
    )
    assert harness.claude.calls == []


async def test_several_unreadable_sources_get_the_generic_sentence(harness: Harness, web: FakeWeb) -> None:
    web.page("https://example.com/portfolio", error="Blocked")
    events = await harness.run(
        AnalyzeRequest(resume_bytes=b"", resume_name="resume.pdf", links=("https://example.com/portfolio",))
    )
    assert_skeleton(events, expect_done=False)
    assert (
        error_of(events) == "We couldn't read any of your sources. Check the file and links, then try again."
    )


async def test_every_reader_failing_blames_claude_with_the_typed_message(
    harness: Harness, web: FakeWeb
) -> None:
    harness.script_readers(per_source={"resume": ClaudeRateLimited(), "portfolio-1": ClaudeOverloaded()})
    events = await harness.run(RESUME_AND_LINK)
    assert_skeleton(events, expect_done=False)
    assert error_of(events) == f"The readers couldn't reach Claude: {BUSY_MESSAGE}"
    assert harness.claude.calls_for("synth") == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ClaudeTimeout(), f"The readers couldn't reach Claude: {NETWORK_MESSAGE}"),
        (ClaudeInvalidOutput(["x"]), f"The readers couldn't reach Claude: {INCOMPLETE_MESSAGE}"),
        (
            ClaudeRefusal("general_harms"),
            f"The readers couldn't reach Claude: {ClaudeRefusal('general_harms').user_message}",
        ),
    ],
)
async def test_other_typed_reader_errors_use_their_own_text(
    harness: Harness, error: Exception, expected: str
) -> None:
    harness.script_readers(per_source={"resume": error})
    events = await harness.run(RESUME_ONLY)
    assert error_of(events) == expected


@pytest.mark.parametrize("error", [ClaudeAuthError(), ClaudeNotConfigured()])
async def test_a_key_problem_is_reported_as_such(harness: Harness, web: FakeWeb, error: Exception) -> None:
    harness.script_readers(per_source={"resume": ClaudeRateLimited(), "portfolio-1": error})
    events = await harness.run(RESUME_AND_LINK)
    assert error_of(events) == AUTH_MESSAGE  # the key problem wins over the busy message


async def test_the_spend_ceiling_is_reported_as_such(harness: Harness, web: FakeWeb) -> None:
    harness.script_readers(per_source={"resume": ClaudeRateLimited(), "portfolio-1": SpendCeilingReached()})
    events = await harness.run(RESUME_AND_LINK)
    assert error_of(events) == SPEND_CEILING_MESSAGE


async def test_the_failed_reader_cards_end_in_error(harness: Harness) -> None:
    harness.script_readers(per_source={"resume": ClaudeAuthError()})
    events = await harness.run(RESUME_ONLY)
    cards = [e.reader for e in events if isinstance(e, ReaderEvent)]
    assert cards[-1].status == "error"
    assert cards[-1].line == "AI service unavailable · skipped"


async def test_one_failed_reader_does_not_stop_the_others(harness: Harness, web: FakeWeb) -> None:
    harness.script_readers(per_source={"portfolio-1": ClaudeRateLimited()})
    harness.script_synth(synth_output(["resume"]))
    events = await harness.run(RESUME_AND_LINK)
    assert_skeleton(events)
    profile = done_of(events)
    failed = next(s for s in profile.sources if s.id == "portfolio-1")
    assert failed.ok is False
    assert failed.error == BUSY_MESSAGE
    assert failed.summary == f"Could not be read: {BUSY_MESSAGE}"
    assert next(s for s in profile.sources if s.id == "resume").ok
    (synth_call,) = harness.claude.calls_for("synth")
    assert "could NOT be read" in synth_call.user_text


# ───────────────────────── synthesis failures ─────────────────────────


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ClaudeOverloaded(), f"Couldn't build your graph: {BUSY_MESSAGE}"),
        (ClaudeTimeout(), f"Couldn't build your graph: {NETWORK_MESSAGE}"),
        (ClaudeInvalidOutput(["bad"]), f"Couldn't build your graph: {INCOMPLETE_MESSAGE}"),
        (ClaudeTruncated(), f"Couldn't build your graph: {ClaudeTruncated().user_message}"),
        (ClaudeAuthError(), f"Couldn't build your graph: {AUTH_MESSAGE}"),
        (ClaudeRefusal("cyber"), f"Couldn't build your graph: {ClaudeRefusal('cyber').user_message}"),
        (RuntimeError("secret resume text"), "Couldn't build your graph: Something went wrong."),
    ],
)
async def test_a_synthesis_failure_is_prefixed_like_the_legacy_message(
    harness: Harness, error: Exception, expected: str
) -> None:
    harness.script_readers()
    harness.script_synth(error)
    events = await harness.run(RESUME_ONLY)
    assert_skeleton(events, expect_done=False)
    assert error_of(events) == expected
    assert "secret" not in error_of(events)
    assert len(harness.claude.calls_for("synth")) == 1  # no second call for a failed call
    assert harness.repos.profiles._s.profiles == {}


async def test_the_spend_ceiling_in_synthesis_is_shown_as_is(harness: Harness) -> None:
    harness.script_readers()
    harness.script_synth(SpendCeilingReached())
    events = await harness.run(RESUME_ONLY)
    assert error_of(events) == SPEND_CEILING_MESSAGE


async def test_other_app_errors_in_synthesis_are_prefixed_too(harness: Harness) -> None:
    harness.script_readers()
    harness.script_synth(RateLimited())
    events = await harness.run(RESUME_ONLY)
    assert error_of(events) == f"Couldn't build your graph: {RateLimited.user_message}"


async def test_an_unexpected_crash_is_a_generic_error_without_its_text(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("boom with secret resume text")

    monkeypatch.setattr("tayyari.analysis.service.to_profile", crash)
    harness.script_readers()
    harness.script_synth(synth_output(["resume"]))
    events = await harness.run(RESUME_ONLY)
    assert_skeleton(events, expect_done=False)
    assert error_of(events) == "Something went wrong."
    assert harness.repos.profiles._s.profiles == {}
    assert other_tasks() == set()


async def test_a_crash_while_planning_is_a_generic_error(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("planner broke")

    monkeypatch.setattr("tayyari.analysis.service.plan_sources_for", crash)
    events = await harness.run(RESUME_ONLY)
    assert types_of(events) == ["error"]
    assert error_of(events) == "Something went wrong."


# ───────────────────────── persistence and Jev outages never fail a run ─────────────────────────


class FailingStore:
    """A profile repository whose writes fail."""

    def __init__(self) -> None:
        self.calls = 0

    async def save(self, *args: Any, **kwargs: Any) -> None:
        self.calls += 1
        raise RuntimeError("database is down")


async def test_a_failed_save_is_logged_and_the_run_still_finishes(harness: Harness) -> None:
    from tayyari.analysis.service import PipelineAnalysisService
    from tayyari.sample.provider import SampleProvider

    store = FailingStore()
    harness.service = PipelineAnalysisService(
        settings=harness.settings,
        claude=harness.claude,
        decisions=harness.decisions,
        profiles=store,  # type: ignore[arg-type]
        sample=SampleProvider(),
        clock=harness.clock,
    )
    harness.script_readers()
    harness.script_synth(synth_output(["resume"]))
    events = await harness.run(RESUME_ONLY)
    assert_skeleton(events)
    assert store.calls == 1
    assert types_of(events).count("error") == 0


async def test_every_jev_failure_at_once_changes_nothing(harness: Harness, web: FakeWeb) -> None:
    decisions = (
        FakeDecisionService()
        .fail("first_read", RuntimeError("jev"))
        .fail("triage", RuntimeError("jev"))
        .fail("check_evidence", RuntimeError("jev"))
        .fail("shadow_compare", RuntimeError("jev"))
    )
    harness.rebuild(decisions=decisions)
    harness.script_readers()
    harness.script_synth(synth_output(["resume", "portfolio-1"]))
    events = await harness.run(RESUME_AND_LINK)
    assert_skeleton(events)
    assert all(
        next(iter(s.evidence), None) for s in done_of(events).skills if s.score >= 45 and s.id != "skill-8"
    )
    assert not [e for e in events if getattr(e, "who", None) == "Jev"]


async def test_a_slow_evidence_check_is_cut_off_at_the_budget(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tayyari.analysis import evidence

    monkeypatch.setattr(evidence, "EVIDENCE_TIMEOUT_S", 0.05)

    class Slow(FakeDecisionService):
        async def check_evidence(
            self, claims: Sequence[EvidenceClaim], ctx: DecisionContext
        ) -> list[EvidenceVerdict]:
            await asyncio.sleep(30)
            raise AssertionError("not reached")

    harness.rebuild(decisions=Slow())
    harness.script_readers()
    harness.script_synth(synth_output(["resume"]))
    events = await asyncio.wait_for(harness.run(RESUME_ONLY), timeout=5)
    assert_skeleton(events)
    assert all(s.evidence for s in done_of(events).skills if 45 <= s.score and s.id != "skill-8")


# ───────────────────────── exactly one closing event ─────────────────────────


async def test_one_closing_event_on_every_path(harness: Harness) -> None:
    for script in (
        lambda: (harness.script_readers(), harness.script_synth(synth_output(["resume"]))),
        lambda: (harness.script_readers(), harness.script_synth(ClaudeOverloaded())),
        lambda: harness.script_readers(per_source={"resume": ClaudeRateLimited()}),
    ):
        harness.rebuild(decisions=FakeDecisionService())
        harness.claude._queues.structured.clear()
        script()
        events = await harness.run(RESUME_ONLY)
        closing = [e for e in events if isinstance(e, DoneEvent | ErrorEvent)]
        assert len(closing) == 1 and events[-1] is closing[0]
        assert_valid_lines(events)


async def test_the_event_after_done_never_appears(harness: Harness) -> None:
    harness.script_readers()
    harness.script_synth(synth_output(["resume"]))
    agen = harness.service.run(RESUME_ONLY, request_context())
    events = [e async for e in agen]
    assert isinstance(events[-1], DoneEvent)
    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()


# ───────────────────────── cancellation ─────────────────────────


async def test_closing_the_stream_right_after_done_loses_nothing(harness: Harness) -> None:
    harness.script_readers()
    harness.script_synth(synth_output(["resume"]))
    agen = harness.service.run(RESUME_ONLY, request_context())
    async for event in agen:
        if isinstance(event, DoneEvent):
            break  # the client reads the profile and hangs up at once
    await agen.aclose()
    profile_id = event.profile.id
    assert profile_id in harness.repos.profiles._s.profiles  # saved before done
    assert len(harness.decisions.shadow_calls) == 1  # queued in the same step as done
    assert other_tasks() == set()


async def test_closing_the_stream_early_cancels_every_task(harness: Harness, web: FakeWeb) -> None:
    started = asyncio.Event()
    cancelled: list[str] = []

    async def hang(call: StructuredCall) -> Any:
        if len(harness.claude.structured_calls) == 3:
            started.set()  # all three readers are in flight
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append("reader")
            raise

    class Pending(FakeDecisionService):
        async def first_read(self, inp: FirstReadInput, ctx: DecisionContext) -> Any:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.append("first_read")
                raise

        async def triage(self, inp: Any, ctx: DecisionContext) -> Any:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.append("triage")
                raise

    harness.rebuild(decisions=Pending())
    harness.claude.script_structured("reader", hang, repeat=True)
    agen = harness.service.run(
        AnalyzeRequest(
            resume_bytes=RESUME_TEXT.encode(),
            resume_name="resume.txt",
            links=("https://example.com/portfolio",),
            notes="N",
        ),
        request_context(),
    )
    await agen.__anext__()
    await asyncio.wait_for(started.wait(), timeout=5)
    await agen.aclose()
    assert sorted(cancelled) == ["first_read", "reader", "reader", "reader", "triage"]
    assert other_tasks() == set()
    assert harness.repos.profiles._s.profiles == {}


async def test_cancelling_the_consumer_task_cleans_up(harness: Harness) -> None:
    started = asyncio.Event()

    async def hang(call: StructuredCall) -> Any:
        started.set()
        await asyncio.sleep(60)

    harness.claude.script_structured("reader", hang, repeat=True)

    async def consume() -> None:
        async for _ in harness.service.run(RESUME_ONLY, request_context()):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert other_tasks() == set()


async def test_closing_during_synthesis_cancels_the_synth_call_and_the_signal_batches(
    harness: Harness,
) -> None:
    in_synth = asyncio.Event()
    cancelled = asyncio.Event()

    async def hang(call: StructuredCall) -> Any:
        in_synth.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    harness.script_readers()
    harness.claude.script_structured("synth", hang)
    agen = harness.service.run(RESUME_ONLY, request_context())
    async for event in agen:
        if getattr(event, "type", None) == "phase" and event.phase == "synthesizing":  # type: ignore[union-attr]
            break
    await asyncio.wait_for(in_synth.wait(), timeout=5)
    await agen.aclose()
    assert cancelled.is_set()
    assert other_tasks() == set()


async def test_cancel_before_the_first_event_leaves_nothing_behind(harness: Harness) -> None:
    agen = harness.service.run(RESUME_ONLY, RequestContext("r", None, None, request_context().started_at))
    task = asyncio.ensure_future(agen.__anext__())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await agen.aclose()
    await asyncio.sleep(0)
    assert other_tasks() == set()


async def test_a_first_read_dropped_at_synthesis_leaves_no_task_behind(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(firstread, "FIRST_READ_GRACE_S", 0.01)

    class Hanging(FakeDecisionService):
        async def first_read(self, inp: FirstReadInput, ctx: DecisionContext) -> Any:
            await asyncio.sleep(60)

    harness.rebuild(decisions=Hanging())
    harness.script_readers()
    harness.script_synth(synth_output(["resume"]))
    events = await harness.run(RESUME_ONLY)
    assert_skeleton(events)
    assert other_tasks() == set()
