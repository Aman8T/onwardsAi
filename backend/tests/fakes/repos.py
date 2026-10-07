"""Dict-backed fakes for every storage port, with the same observable behaviour as the SQL repositories.

Use `FakeRepositories` in service tests: it wires the six fakes over one shared state (so purging a
profile removes its chats and Jev costs reach the spend ledger the limit repo reads) and `.bundle` is the
`tayyari.storage.Repositories` the services are built with::

    fakes = FakeRepositories(clock)
    service = ChatService(profiles=fakes.profiles, sessions=fakes.sessions, ...)

The shared behaviour tests in tests/unit/storage run the same assertions against these fakes and against
the real repositories, so the two cannot drift apart silently.

Behaviour kept identical to the SQL implementation: documents are JSON round-tripped (copies in and out,
and values JSON cannot hold are rejected), ``created_at`` survives a re-save, profile reads touch
``last_accessed_at`` at most hourly, history is the last ``limit`` messages oldest-first, a session of
another profile is invisible, ``hit`` counts per fixed window, a call recorded twice is counted once, and
spend reaches ``total`` plus the call's own scope. Every method yields to the event loop once on entry, so
concurrently running tasks interleave as they would against a database (each method body is then atomic).

Fake-only knobs: `FakeSessionRepo.conflicts` makes the next appends lose their ``(session_id, seq)`` race.
Deliberate differences: no foreign keys (a session may be created for an unknown profile) and no
persistence between instances.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import zlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from tayyari.contract.records import DecisionRecord, JevCallRecord, LlmCallRecord, StateBlob
from tayyari.contract.stored import StoredContext
from tayyari.core.clock import Clock, SystemClock
from tayyari.core.ids import new_session_id
from tayyari.storage.ports import ChatMessage, ChatSession, SessionConflict, SessionNotFound, ToolEvent
from tayyari.storage.repos import Repositories

TOUCH_INTERVAL = timedelta(hours=1)
TOTAL = "total"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _roundtrip[T](value: T) -> T:
    """What a JSON column does to a value: a deep copy, and an error for anything JSON cannot hold."""
    return json.loads(json.dumps(value))  # type: ignore[no-any-return]


async def _yield() -> None:
    await asyncio.sleep(0)


def window_start(now: datetime, window_s: int) -> datetime:
    if window_s <= 0:
        raise ValueError("window_s must be positive")
    epoch = int(_utc(now).timestamp())
    return datetime.fromtimestamp(epoch - epoch % window_s, UTC)


# ───────────────────────────── shared state ─────────────────────────────


@dataclass
class _Profile:
    profile: dict[str, Any]
    context: dict[str, Any]
    created_at: datetime
    last_accessed_at: datetime
    owner_hash: str | None
    is_sample: bool
    schema_version: int
    goal: str | None


@dataclass
class _Session:
    id: str
    profile_id: str
    origin: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    turn_count: int
    ip_hash: str | None
    messages: list[ChatMessage] = field(default_factory=list)
    llm_call_ids: dict[int, UUID | None] = field(default_factory=dict)


@dataclass
class _State:
    clock: Clock
    profiles: dict[str, _Profile] = field(default_factory=dict)
    sessions: dict[str, _Session] = field(default_factory=dict)
    spend: dict[tuple[date, str], tuple[int, int]] = field(default_factory=dict)

    def add_spend(self, created_at: datetime, cost: int, scopes: tuple[str, ...]) -> None:
        day = _utc(created_at).date()
        for scope in scopes:
            micro, calls = self.spend.get((day, scope), (0, 0))
            self.spend[(day, scope)] = (micro + cost, calls + 1)


# ───────────────────────────── repositories ─────────────────────────────


class FakeProfileRepo:
    def __init__(self, state: _State) -> None:
        self._s = state

    async def save(
        self,
        profile_id: str,
        profile: dict[str, Any],
        context: StoredContext,
        *,
        goal: str | None,
        owner_hash: str | None,
        is_sample: bool,
        schema_version: int = 1,
    ) -> None:
        await _yield()
        now = self._s.clock.now()
        existing = self._s.profiles.get(profile_id)
        self._s.profiles[profile_id] = _Profile(
            profile=_roundtrip(profile),
            context=_roundtrip(context.to_dict()),
            created_at=existing.created_at if existing else now,
            last_accessed_at=now,
            owner_hash=owner_hash,
            is_sample=is_sample,
            schema_version=schema_version,
            goal=goal,
        )

    def _touch(self, record: _Profile) -> None:
        now = self._s.clock.now()
        if record.last_accessed_at <= now - TOUCH_INTERVAL:
            record.last_accessed_at = now

    async def get_profile(self, pid: str) -> dict[str, Any] | None:
        await _yield()
        record = self._s.profiles.get(pid)
        if record is None:
            return None
        self._touch(record)
        return _roundtrip(record.profile)

    async def get_for_chat(self, pid: str) -> tuple[dict[str, Any], StoredContext] | None:
        await _yield()
        record = self._s.profiles.get(pid)
        if record is None:
            return None
        self._touch(record)
        return _roundtrip(record.profile), StoredContext.model_validate(_roundtrip(record.context))

    async def purge_expired(self, cutoff: datetime) -> int:
        await _yield()
        expired = [
            pid
            for pid, p in self._s.profiles.items()
            if not p.is_sample and p.last_accessed_at < _utc(cutoff)
        ]
        for pid in expired:
            del self._s.profiles[pid]
            for sid in [sid for sid, sess in self._s.sessions.items() if sess.profile_id == pid]:
                del self._s.sessions[sid]
        return len(expired)

    # inspection helpers (not part of the port)
    def last_accessed_at(self, pid: str) -> datetime:
        return self._s.profiles[pid].last_accessed_at

    def created_at(self, pid: str) -> datetime:
        return self._s.profiles[pid].created_at


class FakeSessionRepo:
    def __init__(self, state: _State) -> None:
        self._s = state
        self.conflicts = 0
        """Appends that lose their ``(session_id, seq)`` race before one is allowed to win."""

    async def load(self, sid: str, profile_id: str, limit: int = 40) -> ChatSession | None:
        await _yield()
        sess = self._s.sessions.get(sid)
        if sess is None or sess.profile_id != profile_id:
            return None
        messages = sess.messages[-limit:] if limit > 0 else []
        return ChatSession(
            id=sess.id,
            profile_id=sess.profile_id,
            origin=_roundtrip(sess.origin),
            turn_count=sess.turn_count,
            messages=list(messages),
        )

    async def create(
        self, profile_id: str, origin: dict[str, Any] | None, ip_hash: str | None
    ) -> ChatSession:
        await _yield()
        now = self._s.clock.now()
        sess = _Session(
            id=new_session_id(),
            profile_id=profile_id,
            origin=_roundtrip(origin),
            created_at=now,
            updated_at=now,
            turn_count=0,
            ip_hash=ip_hash,
        )
        self._s.sessions[sess.id] = sess
        return ChatSession(
            id=sess.id, profile_id=profile_id, origin=_roundtrip(origin), turn_count=0, messages=[]
        )

    async def append_turn(
        self,
        sid: str,
        *,
        user: str,
        assistant: str | None,
        tools: list[ToolEvent],
        llm_call_id: UUID | None,
    ) -> None:
        for _attempt in (1, 2):
            await _yield()
            sess = self._s.sessions.get(sid)
            if sess is None:
                raise SessionNotFound(sid)
            if self.conflicts > 0:
                self.conflicts -= 1
                continue
            seq = sess.messages[-1].seq + 1 if sess.messages else 0
            user_tools = tools if assistant is None else []
            sess.messages.append(ChatMessage(seq=seq, role="user", content=user, tools=list(user_tools)))
            sess.llm_call_ids[seq] = llm_call_id if assistant is None else None
            if assistant is not None:
                sess.messages.append(
                    ChatMessage(seq=seq + 1, role="assistant", content=assistant, tools=list(tools))
                )
                sess.llm_call_ids[seq + 1] = llm_call_id
            sess.turn_count += 1
            sess.updated_at = self._s.clock.now()
            return
        raise SessionConflict(f"could not append to session {sid}: lost the (session_id, seq) race twice")

    # inspection helpers (not part of the port)
    def llm_call_id(self, sid: str, seq: int) -> UUID | None:
        return self._s.sessions[sid].llm_call_ids.get(seq)

    def ip_hash(self, sid: str) -> str | None:
        return self._s.sessions[sid].ip_hash


class FakeLlmCallRepo:
    def __init__(self, state: _State) -> None:
        self._s = state
        self.calls: dict[UUID, LlmCallRecord] = {}

    async def record(self, rec: LlmCallRecord) -> None:
        await _yield()
        if rec.id in self.calls:
            return
        self.calls[rec.id] = rec
        self._s.add_spend(rec.created_at, rec.cost_micro_usd, (TOTAL, "anthropic"))


@dataclass
class _StoredState:
    payload: bytes | None
    raw_bytes: int
    created_at: datetime
    last_ref_at: datetime
    is_sample: bool


class FakeDecisionLogRepo:
    def __init__(self, state: _State) -> None:
        self._s = state
        self.states: dict[str, _StoredState] = {}
        self.calls: dict[UUID, JevCallRecord] = {}
        self.rows: dict[UUID, DecisionRecord] = {}

    async def record_batch(
        self,
        states: list[StateBlob],
        calls: list[JevCallRecord],
        rows: list[DecisionRecord],
    ) -> None:
        await _yield()
        now = self._s.clock.now()
        for blob in states:
            known = self.states.get(blob.hash)
            if known is None:
                self.states[blob.hash] = _StoredState(blob.payload, blob.raw_bytes, now, now, blob.is_sample)
            else:
                known.last_ref_at = now
                if known.payload is None:
                    known.payload = blob.payload
        for call in calls:
            if call.id not in self.calls:
                self.calls[call.id] = call
                self._s.add_spend(call.created_at, call.cost_micro_usd, (TOTAL, "jev"))
        for row in rows:
            self.rows.setdefault(row.id, row)

    async def export(
        self, since: datetime, until: datetime | None = None, limit: int | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        await _yield()
        chosen = sorted(
            (
                r
                for r in self.rows.values()
                if _utc(r.created_at) >= _utc(since) and (until is None or _utc(r.created_at) < _utc(until))
            ),
            key=lambda r: (_utc(r.created_at), r.id),
        )
        if limit is not None:
            chosen = chosen[: max(limit, 0)]
        for row in chosen:
            yield self._export_row(row)

    def _export_row(self, row: DecisionRecord) -> dict[str, Any]:
        call = self.calls.get(row.call_id) if row.call_id is not None else None
        stored = self.states.get(call.state_hash) if call is not None else None
        state = None
        if stored is not None and stored.payload is not None:
            state = json.loads(zlib.decompress(stored.payload))
        return _roundtrip(
            {
                "id": str(row.id),
                "created_at": _utc(row.created_at).isoformat(),
                "spec_id": row.spec_id,
                "spec_version": row.spec_version,
                "pack": row.pack,
                "mode": row.mode,
                "used": row.used,
                "source_used": row.source_used,
                "escalated": row.escalated,
                "escalation_reason": row.escalation_reason,
                "call_id": None if row.call_id is None else str(row.call_id),
                "claude_call_id": None if row.claude_call_id is None else str(row.claude_call_id),
                "subject_ref": row.subject_ref,
                "jev_answer": row.jev_answer,
                "jev_confidence": row.jev_confidence,
                "threshold": row.threshold,
                "claude_answer": row.claude_answer,
                "incumbent": row.incumbent,
                "latency_ms": row.latency_ms,
                "cost_micro_usd": row.cost_micro_usd,
                "profile_id": row.profile_id,
                "analysis_id": None if row.analysis_id is None else str(row.analysis_id),
                "is_sample": row.is_sample,
                "extra": row.extra or {},
                "call": None if call is None else self._call_export(call),
                "state": state,
            }
        )

    @staticmethod
    def _call_export(call: JevCallRecord) -> dict[str, Any]:
        return {
            "id": str(call.id),
            "created_at": _utc(call.created_at).isoformat(),
            "pack": call.pack,
            "model_requested": call.model_requested,
            "model_served": call.model_served,
            "state_hash": call.state_hash,
            "questions": call.questions,
            "response": call.response,
            "status": call.status,
            "error_kind": call.error_kind,
            "request_id": call.request_id,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "cost_micro_usd": call.cost_micro_usd,
            "latency_ms": call.latency_ms,
            "payload_purged_at": None,
        }


class FakeLimitRepo:
    def __init__(self, state: _State) -> None:
        self._s = state
        self.hits: dict[tuple[str, datetime], int] = {}

    async def hit(self, bucket: str, window_s: int, now: datetime) -> int:
        await _yield()
        key = (bucket, window_start(now, window_s))
        self.hits[key] = self.hits.get(key, 0) + 1
        return self.hits[key]

    async def spent_today(self, day: date) -> int:
        await _yield()
        return self._s.spend.get((day, TOTAL), (0, 0))[0]

    async def purge_hits(self, before: datetime) -> int:
        await _yield()
        old = [k for k in self.hits if k[1] < _utc(before)]
        for key in old:
            del self.hits[key]
        return len(old)

    # inspection helper (not part of the port)
    def spend(self, day: date, scope: str) -> tuple[int, int]:
        """``(micro_usd, calls)`` of a ledger row, ``(0, 0)`` when it does not exist."""
        return self._s.spend.get((day, scope), (0, 0))


class FakeKvRepo:
    def __init__(self, state: _State) -> None:
        self._s = state
        self.values: dict[str, Any] = {}

    async def get(self, key: str) -> Any | None:
        await _yield()
        return _roundtrip(self.values.get(key))

    async def set(self, key: str, value: Any) -> None:
        await _yield()
        self.values[key] = _roundtrip(value)

    async def get_or_create_secret(self, key: str, nbytes: int = 32) -> bytes:
        if nbytes < 1:
            raise ValueError("nbytes must be at least 1")
        await _yield()
        stored = self.values.setdefault(key, secrets.token_bytes(nbytes).hex())
        if not isinstance(stored, str):
            raise ValueError(f"kv key {key!r} does not hold a hex-encoded secret")
        try:
            return bytes.fromhex(stored)
        except ValueError:
            raise ValueError(f"kv key {key!r} does not hold a hex-encoded secret") from None


class FakeRepositories:
    """The six fakes over one shared state; ``bundle`` is the `Repositories` services are constructed with."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._state = _State(clock=clock or SystemClock())
        self.profiles = FakeProfileRepo(self._state)
        self.sessions = FakeSessionRepo(self._state)
        self.llm_calls = FakeLlmCallRepo(self._state)
        self.decisions = FakeDecisionLogRepo(self._state)
        self.limits = FakeLimitRepo(self._state)
        self.kv = FakeKvRepo(self._state)
        self.bundle = Repositories(
            profiles=self.profiles,
            sessions=self.sessions,
            llm_calls=self.llm_calls,
            decisions=self.decisions,
            limits=self.limits,
            kv=self.kv,
        )


__all__ = [
    "FakeDecisionLogRepo",
    "FakeKvRepo",
    "FakeLimitRepo",
    "FakeLlmCallRepo",
    "FakeProfileRepo",
    "FakeRepositories",
    "FakeSessionRepo",
    "window_start",
]
