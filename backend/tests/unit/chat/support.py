"""Helpers shared by the chat tests: constants, recording repositories and the `Rig` that wires the service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from tayyari.chat.service import CoachChatService
from tayyari.contract import ChatEvent, StoredContext
from tayyari.contract.requests import ChatRequestBody, RequestContext
from tayyari.core.settings import Settings
from tayyari.llm.claude import ClaudeClient
from tayyari.sample.provider import SampleProvider
from tayyari.storage.ports import ChatSession, ToolEvent
from tests.fakes.claude import FakeClaudeClient
from tests.fakes.clock import FakeClock
from tests.fakes.decisions import FakeDecisionService
from tests.fakes.repos import FakeProfileRepo, FakeRepositories, FakeSessionRepo

PROFILE_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
OTHER_PROFILE_ID = "aa11bb22cc33dd44ee55ff6600778899"
IP_HASH = "iphash0123456789"


class RecordingLog:
    """Stands in for the service's structlog logger: records ``(level, event, fields)`` instead of writing."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, event: str, **fields: Any) -> None:
        self.records.append((level, event, fields))

    def debug(self, event: str, **fields: Any) -> None:
        self._record("debug", event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._record("info", event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._record("warning", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._record("error", event, **fields)

    def events(self, level: str | None = None) -> list[str]:
        return [event for lvl, event, _ in self.records if level is None or lvl == level]


class RecordingProfiles:
    """A `ProfileRepo` that forwards to the fake and remembers every ``save`` (and can be made to fail)."""

    def __init__(self, inner: FakeProfileRepo) -> None:
        self._inner = inner
        self.saves: list[dict[str, Any]] = []
        self.fail_save: BaseException | None = None

    async def save(self, profile_id: str, profile: dict[str, Any], context: StoredContext, **kw: Any) -> None:
        self.saves.append({"profile_id": profile_id, "profile": profile, "context": context, **kw})
        if self.fail_save is not None:
            raise self.fail_save
        await self._inner.save(profile_id, profile, context, **kw)

    async def get_profile(self, pid: str) -> dict[str, Any] | None:
        return await self._inner.get_profile(pid)

    async def get_for_chat(self, pid: str) -> tuple[dict[str, Any], StoredContext] | None:
        return await self._inner.get_for_chat(pid)

    async def purge_expired(self, cutoff: datetime) -> int:
        return await self._inner.purge_expired(cutoff)


class RecordingSessions:
    """A `SessionRepo` that forwards to the fake, remembers its calls and can be made to fail."""

    def __init__(self, inner: FakeSessionRepo) -> None:
        self._inner = inner
        self.loads: list[tuple[str, str, int]] = []
        self.creates: list[tuple[str, dict[str, Any] | None, str | None]] = []
        self.appends: list[dict[str, Any]] = []
        self.fail_load: BaseException | None = None
        self.fail_create: BaseException | None = None
        self.fail_append: BaseException | None = None

    async def load(self, sid: str, profile_id: str, limit: int = 40) -> ChatSession | None:
        self.loads.append((sid, profile_id, limit))
        if self.fail_load is not None:
            raise self.fail_load
        return await self._inner.load(sid, profile_id, limit)

    async def create(
        self, profile_id: str, origin: dict[str, Any] | None, ip_hash: str | None
    ) -> ChatSession:
        self.creates.append((profile_id, origin, ip_hash))
        if self.fail_create is not None:
            raise self.fail_create
        return await self._inner.create(profile_id, origin, ip_hash)

    async def append_turn(
        self,
        sid: str,
        *,
        user: str,
        assistant: str | None,
        tools: list[ToolEvent],
        llm_call_id: UUID | None,
    ) -> None:
        self.appends.append(
            {
                "sid": sid,
                "user": user,
                "assistant": assistant,
                "tools": list(tools),
                "llm_call_id": llm_call_id,
            }
        )
        if self.fail_append is not None:
            raise self.fail_append
        await self._inner.append_turn(
            sid, user=user, assistant=assistant, tools=tools, llm_call_id=llm_call_id
        )


@dataclass
class Rig:
    """Everything a chat test needs, wired the way the container wires the service."""

    settings: Settings
    clock: FakeClock
    fakes: FakeRepositories
    profiles: RecordingProfiles
    sessions: RecordingSessions
    claude: FakeClaudeClient
    decisions: FakeDecisionService
    sample: SampleProvider
    service: CoachChatService
    rc: RequestContext

    def request(self, message: str = "How do I get better at testing?", **fields: Any) -> ChatRequestBody:
        fields.setdefault("profileId", PROFILE_ID)
        return ChatRequestBody(message=message, **fields)

    async def run(self, message: str = "How do I get better at testing?", **fields: Any) -> list[ChatEvent]:
        """One whole turn; the events in order."""
        return [event async for event in self.service.stream_turn(self.request(message, **fields), self.rc)]

    def service_with(self, claude: ClaudeClient) -> CoachChatService:
        """A second service over the same repositories and fakes, talking to another Claude client."""
        return CoachChatService(
            settings=self.settings,
            claude=claude,
            decisions=self.decisions,
            profiles=self.profiles,
            sessions=self.sessions,
            sample=self.sample,
            clock=self.clock,
        )

    async def save_profile(self, profile_id: str = PROFILE_ID) -> None:
        """Store the sample persona's graph under a regular (non-sample) id."""
        profile = self.sample.sample_profile
        assert profile is not None
        document = profile.model_copy(update={"id": profile_id}).to_dict()
        await self.fakes.profiles.save(
            profile_id,
            document,
            self.sample.sample_context,
            goal=profile.goal,
            owner_hash=None,
            is_sample=False,
        )

    async def seed_turns(self, count: int, *, profile_id: str = PROFILE_ID) -> str:
        """A session of ``count`` finished exchanges (``q0``/``a0`` ...), written straight to the repository."""
        session = await self.fakes.sessions.create(profile_id, None, None)
        for i in range(count):
            await self.fakes.sessions.append_turn(
                session.id, user=f"q{i}", assistant=f"a{i}", tools=[], llm_call_id=None
            )
        return session.id

    async def stored(self, sid: str, profile_id: str = PROFILE_ID) -> ChatSession:
        """The session as the repository holds it (all messages)."""
        found = await self.fakes.sessions.load(sid, profile_id, 1_000)
        assert found is not None, f"session {sid} is not stored"
        return found
