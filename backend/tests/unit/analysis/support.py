"""Helpers shared by the analysis tests: scripted answers, a service harness and stream assertions.

Harness              the service wired to FakeClaudeClient, FakeDecisionService, FakeRepositories and a FakeClock
collect              every event of one run, as a list
reader_output(id)    a plausible cleaned reader answer for one source
synth_output(ids)    a plausible synthesizer answer (valid graph; skills at 45+ carry evidence)
sample_synth()       the bundled sample profile used as the synthesizer's answer
assert_skeleton      the order guarantees of the stream (readers first, done last and once ...)
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tayyari.analysis.runctx import RunContext
from tayyari.analysis.service import PipelineAnalysisService
from tayyari.contract import (
    AnalysisEvent,
    AnalysisEventAdapter,
    AnalyzeRequest,
    LogEvent,
    ReaderEvent,
    RequestContext,
    encode_event,
)
from tayyari.core.ids import uuid7
from tayyari.core.settings import Settings
from tayyari.sample.provider import SampleProvider
from tests.fakes.claude import FakeClaudeClient, StructuredCall
from tests.fakes.clock import FakeClock
from tests.fakes.decisions import FakeDecisionService
from tests.fakes.repos import FakeRepositories

SAMPLE_DIR = Path(__file__).resolve().parents[3] / "tayyari" / "sample"
SAMPLE_IDS = ("resume", "github", "lovable-1", "portfolio-1")

_SOURCE_ID = re.compile(r"^Source id: (\S+)$", re.MULTILINE)


def request_context(**overrides: Any) -> RequestContext:
    values: dict[str, Any] = {
        "request_id": "req-test",
        "ip_hash": "iphash0123456789",
        "client_ip": None,
        "started_at": datetime(2026, 10, 7, 12, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return RequestContext(**values)


def source_id_of(call: StructuredCall) -> str:
    """The id of the source a reader call is about (read from the reader prompt)."""
    match = _SOURCE_ID.search(call.user_text)
    assert match, f"not a reader prompt: {call.user_text[:80]!r}"
    return match.group(1)


# ───────────────────────── scripted answers ─────────────────────────


def reader_output(source_id: str, **overrides: Any) -> dict[str, Any]:
    """A cleaned-reader-shaped answer (what the model returns before `clean_reader`)."""
    out: dict[str, Any] = {
        "sub": "3 things · 2 skills",
        "summary": f"What {source_id} shows about the person.",
        "signals": [
            {"label": f"Signal A of {source_id}", "detail": "first"},
            {"label": f"Signal B of {source_id}", "detail": ""},
            {"label": "React", "detail": f"seen in {source_id}"},
        ],
        "skills": [
            {"name": "React", "evidence": f"Used React in {source_id}", "strength": 4},
            {"name": "Testing", "evidence": f"Tests in {source_id}", "strength": 2},
        ],
        "notes": [f"Note from {source_id}: add numbers.", "Second note."],
    }
    if source_id == "resume":
        out["person"] = {
            "name": "Test Person",
            "currentRole": "Frontend Engineer",
            "yearsExperience": 3.0,
            "location": "Pune, India",
        }
    out.update(overrides)
    return out


def synth_output(source_ids: Sequence[str], **overrides: Any) -> dict[str, Any]:
    """A valid synthesizer answer: 3 domains, 8 skills (6 at 45+ with evidence), 3 roles."""
    first = source_ids[0]
    last = source_ids[-1]

    def skill(i: int, score: int, domain: str, source: str, evidence: bool = True) -> dict[str, Any]:
        return {
            "id": f"skill-{i}",
            "name": f"Skill {i}",
            "domainId": domain,
            "score": score,
            "evidence": [{"sourceId": source, "text": f"Evidence for skill {i} in {source}"}]
            if evidence
            else [],
            "unlocks": ["Senior Engineer"] if i % 2 == 0 else [],
        }

    out: dict[str, Any] = {
        "person": {
            "name": "Test Person",
            "headline": "Frontend Engineer · 3 yrs · Pune",
            "currentRole": "Frontend Engineer",
            "yearsExperience": 3.0,
            "location": "Pune, India",
            "summary": "A frontend engineer.",
        },
        "mastery": {"score": 66, "insight": "Solid UI work; tests are thin."},
        "domains": [
            {"id": "frontend", "name": "Frontend", "short": "Frontend", "score": 80, "summary": "UI work"},
            {
                "id": "practice",
                "name": "Engineering Practice",
                "short": "Practice",
                "score": 50,
                "summary": "Tests",
            },
            {"id": "ai", "name": "AI and LLM Apps", "short": "AI", "score": 30, "summary": "Early"},
        ],
        "skills": [
            skill(1, 88, "frontend", first),
            skill(2, 75, "frontend", last),
            skill(3, 62, "frontend", first),
            skill(4, 55, "practice", last),
            skill(5, 48, "practice", first),
            skill(6, 46, "practice", last),
            skill(7, 30, "ai", first),
            skill(8, 15, "ai", last, evidence=False),
        ],
        "roles": [
            {
                "id": "senior-engineer",
                "title": "Senior Engineer",
                "alignment": 72,
                "eta": "~6 months",
                "why": "Strong UI work, thin tests.",
                "requirements": [
                    {"name": "Skill 1", "skillId": "skill-1", "have": True, "current": 88, "target": 80},
                    {"name": "System design", "have": False, "current": 35, "target": 70},
                ],
            },
            {
                "id": "ai-engineer",
                "title": "AI Engineer",
                "alignment": 45,
                "eta": "~1 year",
                "why": "Needs evals.",
                "requirements": [
                    {"name": "Skill 7", "skillId": "skill-7", "have": False, "current": 30, "target": 70}
                ],
            },
            {
                "id": "design-engineer",
                "title": "Design Engineer",
                "alignment": 60,
                "eta": "~4 months",
                "why": "Motion and craft.",
                "requirements": [],
            },
        ],
        "sources": [
            {"id": sid, "sub": f"sub of {sid}", "notes": [f"synth note for {sid}"]} for sid in source_ids
        ],
    }
    out.update(overrides)
    return out


def tiny_synth() -> dict[str, Any]:
    """A graph below the minimums (1 domain, 1 skill, no roles)."""
    return {
        "person": {"name": "X", "currentRole": "Y"},
        "mastery": {"score": 1, "insight": ""},
        "domains": [{"id": "d", "name": "D", "short": "D", "score": 1, "summary": ""}],
        "skills": [
            {"id": "s", "name": "S", "domainId": "d", "score": 1, "evidence": [], "unlocks": []},
        ],
        "roles": [],
        "sources": [],
    }


def sample_synth() -> dict[str, Any]:
    """The bundled sample profile as a synthesizer answer (same shape, already valid)."""
    data: dict[str, Any] = json.loads((SAMPLE_DIR / "sample-profile.json").read_text(encoding="utf-8"))
    return data


def sample_readers() -> dict[str, dict[str, Any]]:
    context = json.loads((SAMPLE_DIR / "sample-context.json").read_text(encoding="utf-8"))
    readers: dict[str, dict[str, Any]] = context["readers"]
    return readers


# ───────────────────────── harness ─────────────────────────


@dataclass
class Harness:
    """The service and every fake it talks to, built the way the container would build it."""

    settings: Settings
    clock: FakeClock = field(default_factory=FakeClock)
    claude: FakeClaudeClient = field(init=False)
    decisions: FakeDecisionService = field(default_factory=FakeDecisionService)
    repos: FakeRepositories = field(init=False)
    service: PipelineAnalysisService = field(init=False)

    def __post_init__(self) -> None:
        self.claude = FakeClaudeClient()
        self.repos = FakeRepositories(self.clock)
        self.rebuild()

    def rebuild(self, *, decisions: FakeDecisionService | None = None) -> None:
        """(Re)create the service, optionally around another decision fake."""
        if decisions is not None:
            self.decisions = decisions
        self.service = PipelineAnalysisService(
            settings=self.settings,
            claude=self.claude,
            decisions=self.decisions,
            profiles=self.repos.profiles,
            sample=SampleProvider(),
            clock=self.clock,
        )

    def script_readers(
        self, *, per_source: Mapping[str, Any] | None = None, readers: Mapping[str, Any] | None = None
    ) -> None:
        """Every reader call is answered from ``readers`` (a source id to answer map), else a generic answer.

        ``per_source`` entries that are exceptions are raised for that source.
        """
        overrides = dict(per_source or {})

        def answer(call: StructuredCall) -> Any:
            sid = source_id_of(call)
            if sid in overrides:
                return overrides[sid]
            if readers is not None and sid in readers:
                return readers[sid]
            return reader_output(sid)

        self.claude.script_structured("reader", answer, repeat=True)

    def script_synth(self, *answers: Any) -> None:
        for a in answers:
            self.claude.script_structured("synth", a)

    async def run(self, req: AnalyzeRequest, rc: RequestContext | None = None) -> list[AnalysisEvent]:
        return await collect(self.service, req, rc or request_context())


async def collect(
    service: PipelineAnalysisService, req: AnalyzeRequest, rc: RequestContext | None = None
) -> list[AnalysisEvent]:
    return [event async for event in service.run(req, rc or request_context())]


# ───────────────────────── a bare run context ─────────────────────────


def make_run(
    settings: Settings,
    *,
    clock: FakeClock | None = None,
    claude: FakeClaudeClient | None = None,
    decisions: FakeDecisionService | None = None,
    req: AnalyzeRequest | None = None,
) -> tuple[RunContext, list[AnalysisEvent]]:
    """A `RunContext` over fakes whose events pile up in the returned list (for testing one stage alone)."""
    events: list[AnalysisEvent] = []
    run = RunContext(
        settings=settings,
        clock=clock or FakeClock(),
        claude=claude or FakeClaudeClient(),
        decisions=decisions or FakeDecisionService(),
        req=req or AnalyzeRequest(),
        rc=request_context(),
        profile_id="profile-test",
        analysis_id=uuid7(),
        send=events.append,
    )
    return run, events


# ───────────────────────── assertions ─────────────────────────


def types_of(events: Sequence[AnalysisEvent]) -> list[str]:
    return [e.type for e in events]


def logs(events: Sequence[AnalysisEvent], who: str | None = None) -> list[LogEvent]:
    return [e for e in events if isinstance(e, LogEvent) and (who is None or e.who == who)]


def log_texts(events: Sequence[AnalysisEvent], who: str | None = None) -> list[str]:
    return [e.text for e in logs(events, who)]


def reader_events(events: Sequence[AnalysisEvent], source_id: str | None = None) -> list[ReaderEvent]:
    return [
        e
        for e in events
        if isinstance(e, ReaderEvent) and (source_id is None or e.reader.sourceId == source_id)
    ]


def assert_valid_lines(events: Sequence[AnalysisEvent]) -> None:
    """Every event survives the wire: encoded as one NDJSON line and parsed back through the event union."""
    for event in events:
        line = encode_event(event)
        assert line.endswith(b"\n") and line.count(b"\n") == 1
        assert AnalysisEventAdapter.validate_json(line) == event


def assert_skeleton(events: Sequence[AnalysisEvent], *, expect_done: bool = True) -> None:
    """The order guarantees every successful run must keep."""
    kinds = types_of(events)
    assert kinds[0] == "readers", kinds[:3]
    assert kinds[1] == "phase" and events[1].phase == "reading"  # type: ignore[union-attr]
    terminal = [i for i, k in enumerate(kinds) if k in ("done", "error")]
    assert terminal == [len(kinds) - 1], f"exactly one closing event, last: {kinds}"
    if not expect_done:
        assert kinds[-1] == "error"
        return
    assert kinds[-1] == "done"
    first_done = next(
        i for i, e in enumerate(events) if isinstance(e, ReaderEvent) and e.reader.status in ("done", "error")
    )
    assert 1 < first_done, "phase reading must come before any reader finishes"
    last_reader = max(i for i, e in enumerate(events) if isinstance(e, ReaderEvent))
    synthesizing = next(i for i, e in enumerate(events) if e.type == "phase" and e.phase == "synthesizing")  # type: ignore[union-attr]
    assert synthesizing > last_reader, "synthesizing starts after every reader is done"
    phases = [e.phase for e in events if e.type == "phase"]  # type: ignore[union-attr]
    assert phases == ["reading", "synthesizing", "done"]
    assert kinds[-3:] == ["synth", "phase", "done"]
    assert_valid_lines(events)


async def settle_tasks() -> None:
    """Let cancelled children finish unwinding."""
    for _ in range(5):
        await asyncio.sleep(0)


def other_tasks() -> set[asyncio.Task[Any]]:
    """Tasks alive besides the current one."""
    current = asyncio.current_task()
    return {t for t in asyncio.all_tasks() if t is not current and not t.done()}


StructuredFactory = Callable[[StructuredCall], Any]
