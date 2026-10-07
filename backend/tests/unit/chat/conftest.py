"""Fixtures for the chat tests: fakes for every port and a stored profile."""

from __future__ import annotations

import pytest

from tayyari.chat.service import CoachChatService
from tayyari.contract.requests import RequestContext
from tayyari.core.settings import Settings
from tayyari.sample.provider import SampleProvider
from tests.fakes.claude import FakeClaudeClient
from tests.fakes.clock import FakeClock
from tests.fakes.decisions import FakeDecisionService
from tests.fakes.repos import FakeRepositories
from tests.unit.chat.support import IP_HASH, RecordingLog, RecordingProfiles, RecordingSessions, Rig


@pytest.fixture
def sample() -> SampleProvider:
    return SampleProvider()


@pytest.fixture
def rig(settings: Settings, sample: SampleProvider) -> Rig:
    clock = FakeClock()
    fakes = FakeRepositories(clock)
    profiles = RecordingProfiles(fakes.profiles)
    sessions = RecordingSessions(fakes.sessions)
    claude = FakeClaudeClient()
    decisions = FakeDecisionService()
    service = CoachChatService(
        settings=settings,
        claude=claude,
        decisions=decisions,
        profiles=profiles,
        sessions=sessions,
        sample=sample,
        clock=clock,
    )
    rc = RequestContext(request_id="req-chat-1", ip_hash=IP_HASH, client_ip=None, started_at=clock.now())
    return Rig(settings, clock, fakes, profiles, sessions, claude, decisions, sample, service, rc)


@pytest.fixture
async def stored_rig(rig: Rig) -> Rig:
    """A rig whose repository already holds a regular profile under `PROFILE_ID`."""
    await rig.save_profile()
    return rig


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    """The chat service's logger, replaced by a recorder (no dependence on how structlog is configured)."""
    recorder = RecordingLog()
    monkeypatch.setattr("tayyari.chat.service.log", recorder)
    return recorder
