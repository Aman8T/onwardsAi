"""Helpers for the end-to-end tests: a running app with a real database, fakes for the network edges, readers
of the NDJSON stream and the database, and a raw-ASGI exchange for the disconnect tests.

    running(settings, ...)   the real app (lifespan, migrations, sample seed) behind `httpx`, as an `Env`
    Env                      the client, the container and the fakes, with ``analyze`` / ``chat`` / ``rows`` helpers
    RecordingClaude          `FakeClaudeClient` that also writes the ``llm_calls`` row the real client writes
    JevScript                a `FakeJev` script that answers every pack the app asks, overridable per test
    FakeWeb                  in-memory stand-in for the link and GitHub fetchers
    analysis_events(resp)    the complete NDJSON body as typed events (each line validated)
    AsgiPost                 one POST driven by hand, so a test decides when the client hangs up
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import docx
import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy import select
from starlette.types import ASGIApp, Message, Scope

from tayyari.api.app import create_app
from tayyari.container import Container, ContainerOverrides
from tayyari.contract import (
    AnalysisEvent,
    AnalysisEventAdapter,
    ChatEvent,
    ChatEventAdapter,
    DoneEvent,
    LogEvent,
    PhaseEvent,
    ReaderEvent,
    SignalEvent,
    encode_event,
)
from tayyari.contract.records import LlmCallRecord
from tayyari.core.ids import uuid7
from tayyari.core.paths import SAMPLE_DIR
from tayyari.core.settings import GuardSettings, Settings
from tayyari.ingest import SourceDoc
from tayyari.llm.types import CallContext, StructuredResult, TurnEnd, Usage, WebSearchRun
from tayyari.storage.ports import LlmCallRepo
from tayyari.storage.tables import SpendDailyRow
from tests.fakes.claude import FakeClaudeClient, StructuredCall
from tests.fakes.jev import FakeJev, RawAnswer, choice_answer, noul_answer, score_answer

BASE_URL = "http://testserver"
STARTUP_TIMEOUT_S = 30.0
BOUNDARY = "tayyari-integration-boundary"
NEWLINE = b"\n"

# ───────────────────────────── settings ─────────────────────────────


def roomy_guard(**overrides: Any) -> GuardSettings:
    """Guard settings that never trip on their own: a test that wants a limit passes it in ``overrides``."""
    values: dict[str, Any] = {
        "analyze_per_minute": 1000,
        "analyze_per_hour": 1000,
        "analyze_per_day": 1000,
        "analyze_global_per_hour": 1000,
        "chat_per_minute": 1000,
        "chat_per_hour": 1000,
        "chat_per_day": 1000,
        "chat_global_per_hour": 0,
        "chat_streams_per_ip": 0,
        "chat_streams_per_process": 0,
        "jobsearch_per_minute": 1000,
        "jobsearch_per_hour": 1000,
        "jobsearch_per_day": 1000,
        "jobsearch_global_per_hour": 0,
        "anon_jobsearch_per_day": 1000,
        "interview_per_minute": 1000,
        "interview_per_hour": 1000,
        "interview_per_day": 1000,
        "interview_global_per_hour": 0,
        "interview_again_per_day": 1000,
        "speech_per_minute": 1000,
        "prepare_per_minute": 1000,
        "prepare_per_hour": 1000,
        "prepare_per_day": 1000,
        "prepare_global_per_hour": 0,
        "auth_bridge_per_minute": 1000,
        "auth_bridge_per_hour": 1000,
        "auth_bridge_global_per_hour": 0,
        "auth_bridge_per_sub_per_day": 1000,
        "daily_ceiling_usd": 1000.0,
    }
    values.update(overrides)
    return GuardSettings(**values)


def quiet_audits() -> dict[str, dict[str, Any]]:
    """``decision_overrides`` that switch off the random 5 % Claude audit of used Jev answers."""
    return {"*": {"audit_rate": 0.0}}


# ───────────────────────────── the NDJSON stream ─────────────────────────────


def ndjson_lines(response: httpx.Response) -> list[bytes]:
    """The non-blank lines of a complete NDJSON body (blank lines are heartbeats)."""
    return [line for line in response.content.split(b"\n") if line.strip()]


def analysis_events(response: httpx.Response) -> list[AnalysisEvent]:
    """Every line of an /api/analyze body parsed through `AnalysisEventAdapter` (a bad line fails the test).

    Each line must also be in the canonical wire form: encoding the parsed event gives the same bytes back.
    """
    events: list[AnalysisEvent] = []
    for line in ndjson_lines(response):
        event = AnalysisEventAdapter.validate_json(line)
        assert encode_event(event) == line + NEWLINE, line[:120]
        events.append(event)
    return events


def chat_events(response: httpx.Response) -> list[ChatEvent]:
    """Every line of a /api/chat body parsed through `ChatEventAdapter`, in canonical wire form."""
    events: list[ChatEvent] = []
    for line in ndjson_lines(response):
        event = ChatEventAdapter.validate_json(line)
        assert encode_event(event) == line + NEWLINE, line[:120]
        events.append(event)
    return events


def types_of(events: Sequence[Any]) -> list[str]:
    return [e.type for e in events]


def done_of(events: Sequence[AnalysisEvent]) -> DoneEvent:
    """The closing ``done`` event of a successful analysis (fails the test when the stream ended otherwise)."""
    last = events[-1]
    assert isinstance(last, DoneEvent), f"the stream did not end with done: {last!r}"
    return last


def index_of(events: Sequence[Any], predicate: Callable[[Any], bool]) -> int:
    """The position of the first event that satisfies ``predicate`` (fails the test when there is none)."""
    for i, event in enumerate(events):
        if predicate(event):
            return i
    raise AssertionError("no event matches")


def logs_by(events: Sequence[AnalysisEvent], who: str) -> list[LogEvent]:
    return [e for e in events if isinstance(e, LogEvent) and e.who == who]


def signals_from(events: Sequence[AnalysisEvent], src: str) -> list[SignalEvent]:
    return [e for e in events if isinstance(e, SignalEvent) and e.src == src]


def assert_skeleton(events: Sequence[AnalysisEvent]) -> None:
    """The order guarantees every successful analysis keeps (see `PipelineAnalysisService`)."""
    kinds = types_of(events)
    assert kinds[0] == "readers", kinds[:3]
    second = events[1]
    assert isinstance(second, PhaseEvent) and second.phase == "reading"
    assert kinds.count("done") == 1 and kinds.count("error") == 0, kinds
    assert kinds[-1] == "done"
    first_finished = index_of(
        events, lambda e: isinstance(e, ReaderEvent) and e.reader.status in ("done", "error")
    )
    assert first_finished > 1, "the phase 'reading' comes before any reader finishes"
    last_reader = max(i for i, e in enumerate(events) if isinstance(e, ReaderEvent))
    synthesizing = index_of(events, lambda e: isinstance(e, PhaseEvent) and e.phase == "synthesizing")
    assert synthesizing > last_reader, "synthesizing starts after every reader is done"
    assert [e.phase for e in events if isinstance(e, PhaseEvent)] == ["reading", "synthesizing", "done"]
    assert kinds[-3:] == ["synth", "phase", "done"]


# ───────────────────────────── multipart bodies ─────────────────────────────


def multipart_body(
    fields: Sequence[tuple[str, str]] = (),
    files: Sequence[tuple[str, str, bytes, str | None]] = (),
    *,
    boundary: str = BOUNDARY,
) -> tuple[bytes, str]:
    """A multipart/form-data body and its Content-Type: text ``fields`` (name, value), then ``files``
    (field name, file name, bytes, content type)."""
    out = bytearray()
    for name, value in fields:
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += value.encode() + b"\r\n"
    for name, filename, data, content_type in files:
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        if content_type is not None:
            out += f"Content-Type: {content_type}\r\n".encode()
        out += b"\r\n" + data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def make_docx(paragraphs: Sequence[str]) -> bytes:
    """A real DOCX file in memory, one paragraph per entry."""
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ───────────────────────────── fake Claude that writes llm_calls ─────────────────────────────


class RecordingClaude(FakeClaudeClient):
    """`FakeClaudeClient` that also writes the ``llm_calls`` row (and the spend it adds to the ledger) that the
    real `AnthropicClaudeClient` writes after each call, from the usage the script gave.

    The real client is the network edge and owns that bookkeeping; with a scripted fake in its place, this
    class keeps the rest of the chain (ledger, spend guard, cost reconciliation) honest. The sink is bound after
    startup, because the repositories only exist once the lifespan has built the container.
    """

    def __init__(self) -> None:
        super().__init__()
        self._sink: LlmCallRepo | None = None
        self.recorded: list[LlmCallRecord] = []

    def bind(self, sink: LlmCallRepo) -> None:
        self._sink = sink

    async def _record(self, ctx: CallContext, usage: Usage, stop_reason: str | None) -> None:
        if self._sink is None:
            return
        record = LlmCallRecord(
            id=uuid7(),
            created_at=datetime.now(UTC),
            purpose=ctx.purpose,
            model=usage.model,
            status="ok",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            web_searches=usage.web_searches,
            cost_micro_usd=usage.cost_micro_usd,
            request_id=usage.request_id,
            stop_reason=stop_reason,
            profile_id=ctx.profile_id,
            session_id=ctx.session_id,
            analysis_id=ctx.analysis_id,
            ip_hash=ctx.ip_hash,
        )
        await self._sink.record(record)
        self.recorded.append(record)

    async def structured(self, **kwargs: Any) -> StructuredResult:
        result = await super().structured(**kwargs)
        await self._record(kwargs["ctx"], result.usage, result.stop_reason)
        return result

    async def search_web(self, **kwargs: Any) -> WebSearchRun:
        run = await super().search_web(**kwargs)
        await self._record(kwargs["ctx"], run.usage, run.stop_reason)
        return run

    def stream_chat(self, **kwargs: Any) -> AsyncIterator[Any]:
        inner = super().stream_chat(**kwargs)
        assert isinstance(inner, AsyncGenerator)
        return self._recording_stream(inner, kwargs["ctx"])

    async def _recording_stream(
        self, inner: AsyncGenerator[Any, None], ctx: CallContext
    ) -> AsyncIterator[Any]:
        try:
            async for event in inner:
                if isinstance(event, TurnEnd):
                    await self._record(ctx, event.usage, event.stop_reason)
                yield event
        finally:
            await inner.aclose()


# ───────────────────────────── fake Jev, answering every pack ─────────────────────────────

FIRST_READ_ANSWERS: dict[str, RawAnswer] = {
    "first_read.career_family": choice_answer("software_engineering", confidence=0.9),
    "first_read.career_stage": choice_answer("established", confidence=0.9),
    "first_read.depth": score_answer(3, confidence=0.8),
    "first_read.leads_or_mentors": noul_answer(0.95),
    "first_read.measured_outcomes": noul_answer(0.92),
    "first_read.progression_promoted": noul_answer(0.05),  # a confident "no": no line, no chip
    "first_read.public_proof": noul_answer(0.9),
    "first_read.ai_experience": noul_answer(0.5),  # unsure: nothing shown
    "first_read.progression_scope": noul_answer(0.9),
    "first_read.progression_switch": noul_answer(0.1),
    "first_read.resume_like": noul_answer(0.97),
}
"""A confident first read of a developer's resume: 5 lines (family and stage, depth, three yes answers) and 3 chips."""

FIRST_READ_LINES = [
    "First read: Software engineering, established",
    "Depth 3 of 5: owns real work end to end",
    "Leads or mentors others",
    "States measured results",
    "Shares public work",
]
FIRST_READ_CHIPS = ["Leads or mentors others", "Measured results", "Public work", "Growing scope"]


def readable_page(state: Mapping[str, Any]) -> dict[str, RawAnswer]:
    """Triage answers for an ordinary page: readable, about the person, English, not relabelled."""
    return {
        "triage.text_quality": choice_answer("readable", confidence=0.95),
        "triage.about_person_work": noul_answer(0.9),
        "triage.mainly_english": noul_answer(0.95),
        "triage.page_type": choice_answer("other", confidence=0.9),
    }


@dataclass
class JevScript:
    """The answers of a `FakeJev`, by pack. The defaults are confident enough that no Claude fallback is needed
    (``decision_fallback`` is never called); each test overrides the part it is about. ``shadow`` questions are
    left unanswered by the script, so `FakeJev` gives them an uninformative answer (they are logged, never used).
    """

    first_read: Mapping[str, RawAnswer] = field(default_factory=lambda: dict(FIRST_READ_ANSWERS))
    triage: Callable[[Mapping[str, Any]], Mapping[str, RawAnswer]] = readable_page
    evidence: Callable[[str], str] = lambda claim: "supports"
    gate: RawAnswer = field(default_factory=lambda: noul_answer(0.9))
    """``chat.web_gate``: p of "needs outside information". 0.9 allows tools, 0.05 withholds them."""
    on_triage: Callable[[Mapping[str, Any]], None] | None = None
    """Called with the state of every triage request (to know when a verdict is on its way)."""

    def __call__(self, state: Any, questions: Mapping[str, Any]) -> Mapping[str, RawAnswer]:
        bases = {qid.split("#", 1)[0] for qid in questions}
        out: dict[str, RawAnswer] = {}
        if any(b.startswith("first_read.") for b in bases):
            out.update(self.first_read)
        if any(b.startswith("triage.") for b in bases):
            if self.on_triage is not None:
                self.on_triage(state)
            out.update(self.triage(state))
        if "evidence.support" in bases:
            out["evidence.support"] = choice_answer(self.evidence(state["claim"]), confidence=0.95)
        if "chat.web_gate" in bases:
            out["chat.web_gate"] = self.gate
        return out


def questions_asked(jev: FakeJev, prefix: str) -> list[str]:
    """Every question id the fake received whose id starts with ``prefix``."""
    return [qid for call in jev.calls for qid in call.question_ids if qid.startswith(prefix)]


# ───────────────────────────── fake web ─────────────────────────────


@dataclass
class FakeWeb:
    """Stands in for the network ingesters (`ingest_web`, `ingest_github`): pages come from a dict."""

    pages: dict[str, tuple[str, str]] = field(default_factory=dict)
    """url -> (title, text); a url that is not listed gets a plain readable page."""
    web_calls: list[str] = field(default_factory=list)
    github_calls: list[str] = field(default_factory=list)

    def page(self, url: str, text: str, *, title: str = "A page") -> None:
        self.pages[url] = (title, text)


DEFAULT_PAGE_TEXT = "A short, readable page about one person's work. " * 12


def install_fake_web(monkeypatch: pytest.MonkeyPatch) -> FakeWeb:
    fake = FakeWeb()

    async def ingest_web(
        url: str, kind: str, label: str, kind_label: str, log: Callable[[str], None]
    ) -> SourceDoc:
        fake.web_calls.append(url)
        log(f"Fetched {url}")
        title, text = fake.pages.get(url, ("A page", DEFAULT_PAGE_TEXT))
        return SourceDoc(
            id="x",
            kind=kind,
            label=label,
            kind_label=kind_label,
            url=url,
            text=text,
            meta={"title": title},
        )

    async def ingest_github(
        url: str, log: Callable[[str], None], github_token: str | None = None
    ) -> SourceDoc:
        fake.github_calls.append(url)
        log("Fetched GitHub profile")
        return SourceDoc(
            id="x",
            kind="github",
            label="GitHub",
            kind_label="GitHub profile",
            url=url,
            text="GITHUB TEXT " * 40,
            meta={"repos": 3, "languages": ["Python"]},
        )

    monkeypatch.setattr("tayyari.ingest.ingest_web", ingest_web)
    monkeypatch.setattr("tayyari.ingest.ingest_github", ingest_github)
    return fake


# ───────────────────────────── scripted Claude answers ─────────────────────────────

_SOURCE_ID = re.compile(r"^Source id: (\S+)$", re.MULTILINE)


def source_id_of(call: StructuredCall) -> str:
    """The id of the source a reader call is about (read from the reader prompt)."""
    match = _SOURCE_ID.search(call.user_text)
    assert match, f"not a reader prompt: {call.user_text[:80]!r}"
    return match.group(1)


def reader_output(source_id: str, **overrides: Any) -> dict[str, Any]:
    """A reader-shaped answer for one source (what the model returns before `clean_reader`)."""
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


def sample_synth() -> dict[str, Any]:
    """The bundled sample profile used as the synthesizer's answer (a valid graph already)."""
    data: dict[str, Any] = json.loads((SAMPLE_DIR / "sample-profile.json").read_text(encoding="utf-8"))
    return data


def sample_readers() -> dict[str, dict[str, Any]]:
    """The bundled reader outputs of the sample persona, by source id."""
    context = json.loads((SAMPLE_DIR / "sample-context.json").read_text(encoding="utf-8"))
    readers: dict[str, dict[str, Any]] = context["readers"]
    return readers


def fallback_answer(call: StructuredCall) -> dict[str, Any]:
    """An answer for any Claude decision fallback, read off the schema: yes (high), the first option, level 2."""
    props = call.schema["properties"]
    if "choice" in props:
        return {"choice": props["choice"]["enum"][0]}
    if "answer" in props:
        return {"answer": True, "confidence": "high"}
    return {"level": 2}


# ───────────────────────────── the running app ─────────────────────────────


@dataclass
class Env:
    """A running app and the fakes behind it."""

    app: FastAPI
    client: httpx.AsyncClient
    container: Container
    settings: Settings
    claude: RecordingClaude
    jev: FakeJev | None

    @property
    def sample_id(self) -> str:
        profile_id = self.container.sample.sample_profile_id()
        assert profile_id is not None
        return profile_id

    async def analyze(
        self,
        *,
        resume: tuple[str, bytes, str] | None = None,
        links: Sequence[str] = (),
        notes: str | None = None,
        goal: str | None = None,
        sample: bool = False,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """POST /api/analyze as the web app does; ``resume`` is ``(file name, bytes, content type)``."""
        parts: list[tuple[str, Any]] = []
        if resume is not None:
            parts.append(("resume", resume))
        if links:
            parts.append(("links", (None, json.dumps(list(links)))))
        for name, value in (("notes", notes), ("goal", goal)):
            if value:
                parts.append((name, (None, value)))
        if sample:
            parts.append(("sample", (None, "1")))
        return await self.client.post("/api/analyze", files=parts, headers=dict(headers or {}))

    async def chat(
        self,
        message: str = "What should I learn next?",
        *,
        profile_id: str | None = None,
        session_id: str | None = None,
        origin: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        body: dict[str, Any] = {"profileId": profile_id or self.sample_id, "message": message}
        if session_id is not None:
            body["sessionId"] = session_id
        if origin is not None:
            body["origin"] = dict(origin)
        return await self.client.post("/api/chat", json=body, headers=dict(headers or {}))

    async def settle(self, timeout_s: float = 10.0) -> None:
        """Wait for the decision runner's background work (shadow and audit calls) and flush the decision log,
        so the database holds everything the last request caused."""
        await self.container.runner.drain(timeout_s)

    async def rows(self, table: Any, *where: Any) -> list[Any]:
        """Every row of an ORM ``table`` (optionally filtered), straight from the database."""
        assert self.container.db is not None
        stmt = select(table)
        for clause in where:
            stmt = stmt.where(clause)
        async with self.container.db.session() as session:
            return list((await session.execute(stmt)).scalars())

    async def spend(self) -> dict[str, tuple[int, int]]:
        """The spend ledger summed over all days: ``{scope: (micro_usd, calls)}``."""
        out: dict[str, tuple[int, int]] = {}
        for row in await self.rows(SpendDailyRow):
            micro, calls = out.get(row.scope, (0, 0))
            out[row.scope] = (micro + row.micro_usd, calls + row.calls)
        return out


@asynccontextmanager
async def running(
    settings: Settings,
    *,
    claude: RecordingClaude | None = None,
    jev: FakeJev | None = None,
    **overrides: Any,
) -> AsyncIterator[Env]:
    """Start the real app (lifespan: migrations, sample seed, services) and yield an `Env`; shut it down on exit.

    ``claude`` and ``jev`` are the fakes at the network edges (the container still wraps the Claude fake in its
    spend-checking client). Every other part is built by the container from ``settings``.
    """
    fake_claude = claude if claude is not None else RecordingClaude()
    app = create_app(settings, overrides=ContainerOverrides(claude=fake_claude, jev=jev, **overrides))
    async with LifespanManager(app, startup_timeout=STARTUP_TIMEOUT_S, shutdown_timeout=STARTUP_TIMEOUT_S):
        container: Container = app.state.container
        fake_claude.bind(container.repos.llm_calls)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL, timeout=60) as client:
            yield Env(
                app=app, client=client, container=container, settings=settings, claude=fake_claude, jev=jev
            )


# ───────────────────────────── waiting ─────────────────────────────


async def eventually(check: Callable[[], bool], *, within: float = 5.0, what: str = "the condition") -> None:
    """Wait until ``check()`` is true; fail the test after ``within`` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + within
    while not check():
        if loop.time() > deadline:
            pytest.fail(f"timed out after {within}s waiting for {what}")
        await asyncio.sleep(0.01)


def alive_tasks(ignore: set[asyncio.Task[Any]]) -> list[asyncio.Task[Any]]:
    """Tasks that are still running, other than the current one and those in ``ignore``."""
    current = asyncio.current_task()
    return [t for t in asyncio.all_tasks() if t is not current and t not in ignore and not t.done()]


async def assert_tasks_drained(before: set[asyncio.Task[Any]], *, within: float = 5.0) -> None:
    """Fail unless every task started since ``before`` was taken has finished (within ``within`` seconds)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + within
    while True:
        left = alive_tasks(before)
        if not left:
            return
        if loop.time() > deadline:
            names = ", ".join(f"{t.get_name()} ({t.get_coro()!r})" for t in left)
            pytest.fail(f"stray tasks still running {within}s later: {names}")
        await asyncio.sleep(0.02)


# ───────────────────────────── a POST driven by hand ─────────────────────────────


@dataclass
class AsgiPost:
    """One POST to an ASGI app, driven by hand: the body arrives at once and the test decides when the client
    hangs up. `httpx.ASGITransport` buffers a whole response and cannot do that.

    ``spec_version`` is what the server announces: uvicorn says ``2.3`` (Starlette then watches for the
    disconnect itself), a newer server says ``2.4`` (only a failed send or the app's own polling shows it).
    """

    path: str
    body: bytes
    content_type: str
    spec_version: str = "2.3"
    headers: Sequence[tuple[str, str]] = ()
    messages: list[Message] = field(default_factory=list)
    _disconnect: asyncio.Event = field(default_factory=asyncio.Event)
    _sent_body: bool = False
    _body_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def scope(self) -> Scope:
        raw_headers = [
            (b"content-type", self.content_type.encode()),
            (b"content-length", str(len(self.body)).encode()),
            *((k.lower().encode(), v.encode()) for k, v in self.headers),
        ]
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": self.spec_version},
            "http_version": "1.1",
            "method": "POST",
            "path": self.path,
            "raw_path": self.path.encode(),
            "query_string": b"",
            "headers": raw_headers,
            "client": ("203.0.113.9", 4000),
            "server": ("testserver", 80),
            "scheme": "http",
        }

    def disconnect(self) -> None:
        """The client closes the connection."""
        self._disconnect.set()

    async def receive(self) -> Message:
        if not self._sent_body:
            self._sent_body = True
            return {"type": "http.request", "body": self.body, "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: Message) -> None:
        self.messages.append(message)
        if message["type"] == "http.response.body":
            self._body_event.set()

    @property
    def status(self) -> int:
        start = next(m for m in self.messages if m["type"] == "http.response.start")
        return int(start["status"])

    @property
    def lines(self) -> list[bytes]:
        """The non-blank NDJSON lines sent so far."""
        parts = [m["body"] for m in self.messages if m["type"] == "http.response.body" and m.get("body")]
        return [line for part in parts for line in part.split(b"\n") if line.strip()]

    def run(self, app: ASGIApp) -> asyncio.Task[None]:
        """Start ``app`` as a task; the caller awaits or cancels it."""
        return asyncio.create_task(app(self.scope, self.receive, self.send))
