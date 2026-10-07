"""Helpers for the wiring tests: fake analysis and chat services, a running-app harness, NDJSON parsing.

`running_app(settings, overrides)` starts the real app (`create_app`) through `asgi-lifespan`, so the lifespan
really builds the container, migrates a real SQLite file under the test's data folder and seeds the sample.
Requests go through `httpx.ASGITransport`, which buffers a whole response: the fake services therefore end
their streams (or are released by the test) and the tests read the complete NDJSON body.

The route tests use fake analysis and chat services (the real ones are tested in their own areas);
`test_container.py` checks that the container builds the real classes with the constructors they expect.
`spy_all` records the shutdown steps of the real container, in the order they run.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from tayyari.api.app import create_app
from tayyari.container import Container, ContainerOverrides
from tayyari.contract.events import (
    AnalysisEvent,
    ChatEvent,
    ev_chat_done,
    ev_delta,
    ev_done,
    ev_phase,
    ev_tool,
)
from tayyari.contract.requests import AnalyzeRequest, ChatRequestBody, RequestContext
from tayyari.core.ids import new_profile_id, new_session_id
from tayyari.core.settings import GuardSettings, Settings
from tayyari.decisions.recorder import DecisionRecorder
from tayyari.decisions.runner import DecisionRunner
from tayyari.guard.spend import SpendGuard
from tayyari.llm.anthropic_client import AnthropicClaudeClient
from tayyari.llm.jev_http import HttpJevClient
from tayyari.sample.provider import SampleProvider
from tayyari.storage.db import Database
from tayyari.storage.ports import ProfileRepo
from tests.fakes.claude import FakeClaudeClient
from tests.fakes.clock import FakeClock
from tests.fakes.decisions import FakeDecisionService

BASE_URL = "http://testserver"
STARTUP_TIMEOUT_S = 30.0


def roomy_guard(**overrides: Any) -> GuardSettings:
    """Guard settings that never trip on their own: tests that want a limit pass it in ``overrides``."""
    values: dict[str, Any] = {
        "analyze_per_minute": 1000,
        "analyze_per_hour": 1000,
        "analyze_per_day": 1000,
        "analyze_global_per_hour": 1000,
        "chat_per_minute": 1000,
        "chat_per_hour": 1000,
        "chat_per_day": 1000,
        "daily_ceiling_usd": 1000.0,
    }
    values.update(overrides)
    return GuardSettings(**values)


Calls = list[tuple[str, tuple[Any, ...], dict[str, Any]]]


def spy(
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    cls: type,
    method: str,
    label: str,
    *,
    raises: BaseException | None = None,
) -> None:
    """Record ``label`` (with the arguments) whenever ``cls.method`` runs, then run it (or raise ``raises``)."""
    original = getattr(cls, method)

    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append((label, args, kwargs))
        if raises is not None:
            raise raises
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(cls, method, wrapper)


def spy_all(monkeypatch: pytest.MonkeyPatch) -> Calls:
    """Spies on every shutdown step; the list fills in the order they run."""
    calls: Calls = []
    spy(monkeypatch, calls, DecisionRunner, "drain", "runner.drain")
    spy(monkeypatch, calls, DecisionRecorder, "aclose", "recorder.aclose")
    spy(monkeypatch, calls, HttpJevClient, "aclose", "jev.aclose")
    spy(monkeypatch, calls, AnthropicClaudeClient, "aclose", "claude.aclose")
    spy(monkeypatch, calls, SpendGuard, "aclose", "spend_guard.aclose")
    spy(monkeypatch, calls, Database, "dispose", "db.dispose")
    return calls


def ndjson(response: httpx.Response) -> list[dict[str, Any]]:
    """The events of a complete NDJSON body (blank heartbeat lines skipped)."""
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


class FakeAnalysis:
    """An `AnalysisService`: streams a phase and a ``done`` event, saving the sample profile under a new id.

    ``block`` holds the stream after the first event until the test sets it; ``fail`` raises mid-stream.
    The profile repository is bound after startup (it only exists once the lifespan has built the container).
    """

    def __init__(self, *, block: asyncio.Event | None = None, fail: BaseException | None = None) -> None:
        self.profiles: ProfileRepo | None = None
        self.block = block
        self.fail = fail
        self.requests: list[tuple[AnalyzeRequest, RequestContext]] = []
        self.started = asyncio.Event()
        self.cancelled = 0
        self.finished = 0
        self.profile_id: str | None = None

    async def run(self, req: AnalyzeRequest, rc: RequestContext) -> AsyncIterator[AnalysisEvent]:
        self.requests.append((req, rc))
        self.started.set()
        try:
            yield ev_phase("reading", "Reading your sources")
            if self.block is not None:
                await self.block.wait()
            if self.fail is not None:
                raise self.fail
            sample = SampleProvider()
            profile = sample.sample_profile
            assert profile is not None
            self.profile_id = new_profile_id(sample=True)
            saved = profile.model_copy(update={"id": self.profile_id})
            if self.profiles is not None:
                await self.profiles.save(
                    self.profile_id,
                    saved.to_dict(),
                    sample.sample_context,
                    goal=saved.goal,
                    owner_hash=rc.ip_hash,
                    is_sample=True,
                )
            yield ev_done(saved)
            self.finished += 1
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


class FakeChat:
    """A `ChatService`: a few deltas, one tool use, then ``done`` with a session id."""

    def __init__(self) -> None:
        self.requests: list[tuple[ChatRequestBody, RequestContext]] = []

    async def stream_turn(self, req: ChatRequestBody, rc: RequestContext) -> AsyncIterator[ChatEvent]:
        self.requests.append((req, rc))
        yield ev_delta("Hello, ")
        yield ev_tool("WebSearch", "data analyst jobs")
        yield ev_delta("here is a plan.")
        yield ev_chat_done(req.sessionId or new_session_id())


@dataclass
class Harness:
    """A running app and the fakes behind it."""

    app: FastAPI
    client: httpx.AsyncClient
    container: Container
    settings: Settings
    claude: FakeClaudeClient
    decisions: FakeDecisionService
    analysis: FakeAnalysis
    chat: FakeChat
    clock: FakeClock
    extra: dict[str, Any] = field(default_factory=dict)

    async def analyze_sample(self, **headers: str) -> httpx.Response:
        """POST /api/analyze as the web app's "Try a sample profile" does (a multipart form with sample=1)."""
        return await self.client.post("/api/analyze", files={"sample": (None, "1")}, headers=headers or None)

    async def chat_turn(self, message: str = "What should I learn next?", **body: Any) -> httpx.Response:
        payload = {"profileId": self.sample_id(), "message": message, **body}
        return await self.client.post("/api/chat", json=payload)

    def sample_id(self) -> str:
        profile_id = self.container.sample.sample_profile_id()
        assert profile_id is not None
        return profile_id


def make_overrides(
    *,
    analysis: FakeAnalysis | None = None,
    chat: FakeChat | None = None,
    claude: FakeClaudeClient | None = None,
    decisions: FakeDecisionService | None = None,
    clock: FakeClock | None = None,
    **extra: Any,
) -> tuple[ContainerOverrides, dict[str, Any]]:
    """`ContainerOverrides` made of fakes (created when not given) plus the fakes by name."""
    fakes: dict[str, Any] = {
        "analysis": analysis or FakeAnalysis(),
        "chat": chat or FakeChat(),
        "claude": claude or FakeClaudeClient(),
        "decisions": decisions or FakeDecisionService(),
        "clock": clock or FakeClock(),
    }
    return ContainerOverrides(**fakes, **extra), fakes


@asynccontextmanager
async def running_app(
    settings: Settings,
    *,
    analysis: FakeAnalysis | None = None,
    chat: FakeChat | None = None,
    claude: FakeClaudeClient | None = None,
    decisions: FakeDecisionService | None = None,
    clock: FakeClock | None = None,
    configure: Callable[[ContainerOverrides], ContainerOverrides] | None = None,
    **extra_overrides: Any,
) -> AsyncIterator[Harness]:
    """Start the real app with fake services and yield a `Harness`; the lifespan shuts down on exit."""
    overrides, fakes = make_overrides(
        analysis=analysis, chat=chat, claude=claude, decisions=decisions, clock=clock, **extra_overrides
    )
    if configure is not None:
        overrides = configure(overrides)
    app = create_app(settings, overrides=overrides)
    async with LifespanManager(app, startup_timeout=STARTUP_TIMEOUT_S, shutdown_timeout=STARTUP_TIMEOUT_S):
        container: Container = app.state.container
        fakes["analysis"].profiles = container.repos.profiles
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            yield Harness(
                app=app,
                client=client,
                container=container,
                settings=settings,
                claude=fakes["claude"],
                decisions=fakes["decisions"],
                analysis=fakes["analysis"],
                chat=fakes["chat"],
                clock=fakes["clock"],
            )
