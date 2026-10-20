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

`FakeUserRepo` mirrors `SqlUserRepo`: lower-cased unique emails, a Google identity found by ``sub`` and then by
email, a link that can be spent once, claims that refuse sample runs and other people's profiles, and an account
delete that takes its profiles (with their chats) and portfolios along. A claimed profile survives `purge_expired`.

`FakeMatchRepo` and its three companions mirror `repos.match`: postings shared by hash (the first reading stays),
facts per profile replaced when the sources change, user facts that are one row per ``(profile, fact id)``, and
matches that only their owner can read or change, with at most one resume and one cover note each. Deleting an
account takes its matches, variants and user facts along (postings stay).

`FakeBillingRepo` mirrors `SqlBillingRepo`: orders found by the provider's id and settled once, a balance that only
moves with a ledger row (so ``credits == sum(delta)``), uses idempotent per ``ref``, the free allowance counted per
calendar month, and a give-back that reverses exactly one use. Unlike the database it has no foreign keys, so deleting
an account does not clear its orders or balance.

Fake-only knobs: `FakeSessionRepo.conflicts` makes the next appends lose their ``(session_id, seq)`` race.
Deliberate differences: no foreign keys (a session may be created for an unknown profile) and no
persistence between instances.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import secrets
import zlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from tayyari.contract.match import Fact, Posting
from tayyari.contract.portfolio import Fact as PortfolioFact
from tayyari.contract.records import DecisionRecord, JevCallRecord, LlmCallRecord, StateBlob
from tayyari.contract.stored import StoredContext
from tayyari.core.clock import Clock, SystemClock
from tayyari.core.ids import new_session_id
from tayyari.storage.ports import (
    ChatMessage,
    ChatSession,
    ConsumeOutcome,
    ConsumeSource,
    EmailTaken,
    MatchListing,
    OpenProfile,
    OwnedProfile,
    PortfolioConflict,
    ProfileAccess,
    ProfileNotClaimable,
    ProfileOwnedByAnother,
    SessionConflict,
    SessionNotFound,
    SettleResult,
    StoredAuthSession,
    StoredEntitlements,
    StoredMagicLink,
    StoredMatch,
    StoredOrder,
    StoredPortfolio,
    StoredUser,
    StoredVariant,
    ToolEvent,
    VariantKind,
)
from tayyari.storage.repos import Repositories
from tayyari.storage.repos.match import posting_listing_fields
from tests.fakes.discover import FakeDiscoverRepo
from tests.fakes.handoff import FakeHandoffRepo
from tests.fakes.interview import FakeInterviewRepo
from tests.fakes.jobsearch import FakeJobSearchRepo
from tests.fakes.prepare import FakePrepareRepo
from tests.fakes.weekly import FakeWeeklyRepo

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
    user_id: str | None = None


@dataclass
class _UserFact:
    user_id: str | None
    profile_id: str
    match_id: str | None
    question: str
    answer: str
    fact: dict[str, Any]
    created_at: datetime


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
    postings: dict[str, tuple[str, dict[str, Any], datetime]] = field(default_factory=dict)
    """hash -> (text, parsed JSON without rawText, created_at). Shared by every account; never deleted with one."""
    profile_facts: dict[str, tuple[str, list[dict[str, Any]]]] = field(default_factory=dict)
    """profile id -> (facts_hash, facts JSON)."""
    user_facts: dict[tuple[str, str], _UserFact] = field(default_factory=dict)
    """(profile id, fact id) -> the fact with the question it answered."""
    matches: dict[str, StoredMatch] = field(default_factory=dict)
    variants: dict[tuple[str, str], StoredVariant] = field(default_factory=dict)
    """(match id, kind) -> variant."""

    def drop_match_data(self, *, user_id: str, profile_ids: set[str]) -> None:
        """What deleting an account removes: its matches (with their variants), the facts it gave, the facts cache
        of its profiles. Postings stay."""
        gone = {mid for mid, m in self.matches.items() if m.user_id == user_id or m.profile_id in profile_ids}
        for mid in gone:
            del self.matches[mid]
        for key in [k for k in self.variants if k[0] in gone]:
            del self.variants[key]
        for key in [
            k for k, f in self.user_facts.items() if f.user_id == user_id or f.profile_id in profile_ids
        ]:
            del self.user_facts[key]
        for pid in profile_ids:
            self.profile_facts.pop(pid, None)

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
            user_id=existing.user_id if existing else None,
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

    async def get_owned_for_chat(self, user_id: str, pid: str) -> tuple[dict[str, Any], StoredContext] | None:
        await _yield()
        record = self._s.profiles.get(pid)
        if record is None or record.user_id != user_id or record.is_sample:
            return None
        self._touch(record)
        return _roundtrip(record.profile), StoredContext.model_validate(_roundtrip(record.context))

    async def get_open_for_chat(self, pid: str) -> OpenProfile | None:
        await _yield()
        record = self._s.profiles.get(pid)
        if record is None or not (record.user_id is None or record.is_sample):
            return None
        self._touch(record)
        return OpenProfile(
            profile=_roundtrip(record.profile),
            context=StoredContext.model_validate(_roundtrip(record.context)),
            is_sample=record.is_sample,
        )

    async def get_access(self, pid: str) -> ProfileAccess | None:
        await _yield()
        record = self._s.profiles.get(pid)
        return None if record is None else ProfileAccess(user_id=record.user_id, is_sample=record.is_sample)

    async def purge_expired(self, cutoff: datetime) -> int:
        await _yield()
        expired = [
            pid
            for pid, p in self._s.profiles.items()
            if not p.is_sample and p.user_id is None and p.last_accessed_at < _utc(cutoff)
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


@dataclass
class _Portfolio:
    profile_id: str
    public_id: str
    doc: dict[str, Any]
    generated: list[dict[str, Any]]
    facts_hash: str | None
    version: int
    published: bool
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    last_viewed_at: datetime | None
    user_id: str | None = None


class FakePortfolioRepo:
    def __init__(self, state: _State) -> None:
        self._s = state
        self.portfolios: dict[str, _Portfolio] = {}

    async def get(self, profile_id: str) -> StoredPortfolio | None:
        await _yield()
        p = self.portfolios.get(profile_id)
        if p is None:
            return None
        return StoredPortfolio(
            profile_id=p.profile_id,
            public_id=p.public_id,
            doc=_roundtrip(p.doc),
            facts_hash=p.facts_hash,
            generated=_roundtrip(p.generated),
            version=p.version,
            published=p.published,
            created_at=p.created_at,
            updated_at=p.updated_at,
            user_id=p.user_id,
        )

    async def get_published(self, public_id: str) -> StoredPortfolio | None:
        await _yield()
        for p in self.portfolios.values():
            if p.public_id == public_id and p.published:
                now = self._s.clock.now()
                if p.last_viewed_at is None or p.last_viewed_at < now - timedelta(minutes=10):
                    p.last_viewed_at = now  # like the SQL repo: at most one write per ten minutes
                return StoredPortfolio(
                    profile_id=p.profile_id,
                    public_id=p.public_id,
                    doc=_roundtrip(p.doc),
                    facts_hash=p.facts_hash,
                    generated=_roundtrip(p.generated),
                    version=p.version,
                    published=p.published,
                    created_at=p.created_at,
                    updated_at=p.updated_at,
                    user_id=p.user_id,
                )
        return None

    async def put(self, rec: StoredPortfolio, *, expected_version: int | None) -> StoredPortfolio:
        await _yield()
        existing = self.portfolios.get(rec.profile_id)
        if expected_version is not None and (existing is None or existing.version != expected_version):
            raise PortfolioConflict(f"portfolio version conflict: expected {expected_version}")
        now = self._s.clock.now()
        # Determine new version: 1 for new row, existing+1 for updates, expected+1 for optimistic
        if existing is None:
            new_version = 1
        elif expected_version is None:
            new_version = existing.version + 1
        else:
            new_version = expected_version + 1
        public_id = existing.public_id if existing else rec.public_id
        created_at = existing.created_at if existing else now
        # unpublished: cleared; published: kept while it stays published, now when this write publishes it
        if not rec.published:
            published_at = None
        elif existing is not None and existing.published:
            published_at = existing.published_at or now
        else:
            published_at = now
        p = _Portfolio(
            profile_id=rec.profile_id,
            public_id=public_id,
            doc=_roundtrip(rec.doc),
            generated=_roundtrip(rec.generated),
            facts_hash=rec.facts_hash,
            version=new_version,
            published=rec.published,
            created_at=created_at,
            updated_at=now,
            published_at=published_at,
            last_viewed_at=existing.last_viewed_at if existing else None,
            # a write that does not name an owner keeps the one the page has
            user_id=rec.user_id if rec.user_id is not None else (existing.user_id if existing else None),
        )
        self.portfolios[rec.profile_id] = p
        return StoredPortfolio(
            profile_id=p.profile_id,
            public_id=p.public_id,
            doc=_roundtrip(p.doc),
            facts_hash=p.facts_hash,
            generated=_roundtrip(p.generated),
            version=p.version,
            published=p.published,
            created_at=p.created_at,
            updated_at=p.updated_at,
            user_id=p.user_id,
        )

    async def delete(self, profile_id: str) -> bool:
        await _yield()
        if profile_id in self.portfolios:
            del self.portfolios[profile_id]
            return True
        return False

    async def purge_orphan_drafts(self, cutoff: datetime) -> int:
        await _yield()
        # Collect profile ids that exist (in _State)
        profile_ids = set(self._s.profiles.keys())
        # Find portfolios to delete: unpublished, updated before cutoff, profile gone
        to_delete = [
            pid
            for pid, p in self.portfolios.items()
            if not p.published and p.updated_at < _utc(cutoff) and p.profile_id not in profile_ids
        ]
        for pid in to_delete:
            del self.portfolios[pid]
        return len(to_delete)


@dataclass
class _User:
    id: str
    email: str | None
    name: str | None
    picture_url: str | None
    google_sub: str | None
    created_at: datetime
    last_login_at: datetime
    locale: str | None
    currency: str | None
    supabase_sub: str | None = None

    def view(self) -> StoredUser:
        return StoredUser(
            id=self.id,
            email=self.email,
            name=self.name,
            picture_url=self.picture_url,
            google_sub=self.google_sub,
            created_at=self.created_at,
            last_login_at=self.last_login_at,
            locale=self.locale,
            currency=self.currency,
            supabase_sub=self.supabase_sub,
        )


class FakeUserRepo:
    def __init__(self, state: _State, portfolios: FakePortfolioRepo) -> None:
        self._s = state
        self._portfolios = portfolios
        self.users: dict[str, _User] = {}
        self.sessions: dict[str, StoredAuthSession] = {}
        self.links: dict[str, StoredMagicLink] = {}

    def _by_email(self, email: str) -> _User | None:
        return next((u for u in self.users.values() if u.email == email), None)

    def _create(self, *, email: str | None, now: datetime, **fields: Any) -> _User:
        user = _User(
            id=secrets.token_hex(16),
            email=email,
            name=fields.get("name"),
            picture_url=fields.get("picture_url"),
            google_sub=fields.get("google_sub"),
            created_at=now,
            last_login_at=now,
            locale=fields.get("locale"),
            currency=None,
            supabase_sub=fields.get("supabase_sub"),
        )
        self.users[user.id] = user
        return user

    async def get(self, user_id: str) -> StoredUser | None:
        await _yield()
        user = self.users.get(user_id)
        return None if user is None else user.view()

    async def get_by_email(self, email: str) -> StoredUser | None:
        await _yield()
        user = self._by_email(email)
        return None if user is None else user.view()

    async def sign_in_google(
        self,
        *,
        sub: str,
        email: str,
        name: str | None,
        picture_url: str | None,
        locale: str | None,
        now: datetime,
    ) -> StoredUser:
        await _yield()
        user = next((u for u in self.users.values() if u.google_sub == sub), None) or self._by_email(email)
        if user is None:
            return self._create(
                email=email, now=now, name=name, picture_url=picture_url, google_sub=sub, locale=locale
            ).view()
        user.google_sub = user.google_sub or sub
        user.last_login_at = now
        user.picture_url = picture_url
        user.name = user.name or name
        user.locale = user.locale or locale
        return user.view()

    async def sign_in_email(self, *, email: str, now: datetime) -> StoredUser:
        await _yield()
        user = self._by_email(email)
        if user is None:
            return self._create(email=email, now=now).view()
        user.last_login_at = now
        return user.view()

    async def sign_in_supabase(
        self, *, sub: str, email: str | None, email_verified: bool, name: str | None, now: datetime
    ) -> StoredUser:
        await _yield()
        verified = email if email_verified and email else None
        user = next((u for u in self.users.values() if u.supabase_sub == sub), None)
        if user is not None:
            user.last_login_at = now
            user.name = user.name or name
            if verified is not None and user.email != verified and self._by_email(verified) is None:
                user.email = verified
            return user.view()
        if verified is not None:
            holder = self._by_email(verified)
            if holder is not None and holder.supabase_sub is None:
                holder.supabase_sub = sub
                holder.last_login_at = now
                holder.name = holder.name or name
                return holder.view()
            if holder is not None:
                raise EmailTaken(holder.id)
        return self._create(email=verified, now=now, name=name, supabase_sub=sub).view()

    async def update(
        self, user_id: str, *, name: str | None = None, currency: str | None = None
    ) -> StoredUser | None:
        await _yield()
        user = self.users.get(user_id)
        if user is None:
            return None
        if name is not None:
            user.name = name
        if currency is not None:
            user.currency = currency
        return user.view()

    async def delete(self, user_id: str) -> bool:
        await _yield()
        user = self.users.pop(user_id, None)
        if user is None:
            return False
        owned = [pid for pid, p in self._s.profiles.items() if p.user_id == user_id]
        self._s.drop_match_data(user_id=user_id, profile_ids=set(owned))
        for pid in owned:
            del self._s.profiles[pid]
            for sid in [sid for sid, sess in self._s.sessions.items() if sess.profile_id == pid]:
                del self._s.sessions[sid]
        for pid in [
            pid for pid, p in self._portfolios.portfolios.items() if p.user_id == user_id or pid in owned
        ]:
            del self._portfolios.portfolios[pid]
        self.sessions = {k: v for k, v in self.sessions.items() if v.user_id != user_id}
        self.links = {k: v for k, v in self.links.items() if user.email is None or v.email != user.email}
        return True

    async def create_session(self, rec: StoredAuthSession) -> None:
        await _yield()
        self.sessions[rec.id] = rec

    async def get_session(self, session_id: str) -> tuple[StoredAuthSession, StoredUser] | None:
        await _yield()
        session = self.sessions.get(session_id)
        user = self.users.get(session.user_id) if session is not None else None
        if session is None or user is None:
            return None
        return session, user.view()

    async def touch_session(self, session_id: str, *, last_seen_at: datetime, expires_at: datetime) -> None:
        await _yield()
        session = self.sessions.get(session_id)
        if session is not None:
            self.sessions[session_id] = dataclasses.replace(
                session, last_seen_at=last_seen_at, expires_at=expires_at
            )

    async def mark_session_verified(self, session_id: str, verified_at: datetime) -> None:
        await _yield()
        session = self.sessions.get(session_id)
        if session is not None:
            self.sessions[session_id] = dataclasses.replace(session, verified_at=verified_at)

    async def delete_session(self, session_id: str) -> bool:
        await _yield()
        return self.sessions.pop(session_id, None) is not None

    async def add_magic_link(self, rec: StoredMagicLink) -> None:
        await _yield()
        if rec.token_hash in self.links:
            raise ValueError("duplicate magic link")
        self.links[rec.token_hash] = rec

    async def consume_magic_link(self, token_hash: str, now: datetime) -> StoredMagicLink | None:
        await _yield()
        link = self.links.get(token_hash)
        if link is None or link.used_at is not None or not _utc(link.expires_at) > _utc(now):
            return None
        spent = dataclasses.replace(link, used_at=now)
        self.links[token_hash] = spent
        return spent

    async def list_profiles(self, user_id: str, limit: int = 20) -> list[OwnedProfile]:
        await _yield()
        mine = sorted(
            ((pid, p) for pid, p in self._s.profiles.items() if p.user_id == user_id),
            key=lambda item: (-_utc(item[1].created_at).timestamp(), item[0]),
        )
        out: list[OwnedProfile] = []
        for pid, p in mine[:limit]:
            person = p.profile.get("person")
            name = person.get("name") if isinstance(person, dict) else None
            out.append(
                OwnedProfile(
                    id=pid,
                    created_at=p.created_at,
                    goal=p.goal,
                    person_name=name if isinstance(name, str) and name.strip() else None,
                )
            )
        return out

    async def claim_profile(self, user_id: str, profile_id: str) -> None:
        await _yield()
        p = self._s.profiles.get(profile_id)
        if p is None or p.is_sample:
            raise ProfileNotClaimable(profile_id)
        if p.user_id is not None and p.user_id != user_id:
            raise ProfileOwnedByAnother(profile_id)
        p.user_id = user_id
        page = self._portfolios.portfolios.get(profile_id)
        if page is not None and page.user_id is None:
            page.user_id = user_id
        for match_id, match in list(self._s.matches.items()):
            if match.profile_id == profile_id and match.user_id is None:
                self._s.matches[match_id] = dataclasses.replace(match, user_id=user_id)
        for fact in self._s.user_facts.values():
            if fact.profile_id == profile_id and fact.user_id is None:
                fact.user_id = user_id

    async def purge_expired(self, now: datetime) -> int:
        await _yield()
        old_sessions = [k for k, v in self.sessions.items() if _utc(v.expires_at) < _utc(now)]
        old_links = [k for k, v in self.links.items() if _utc(v.expires_at) < _utc(now)]
        for k in old_sessions:
            del self.sessions[k]
        for k in old_links:
            del self.links[k]
        return len(old_sessions) + len(old_links)

    # inspection helpers (not part of the port)
    def profile_owner(self, profile_id: str) -> str | None:
        return self._s.profiles[profile_id].user_id

    def portfolio_owner(self, profile_id: str) -> str | None:
        return self._portfolios.portfolios[profile_id].user_id


class FakePostingRepo:
    def __init__(self, state: _State) -> None:
        self._s = state

    async def get(self, posting_hash: str) -> Posting | None:
        await _yield()
        found = self._s.postings.get(posting_hash)
        if found is None:
            return None
        text, parsed, _ = found
        return Posting.model_validate({**_roundtrip(parsed), "rawText": text})

    async def put(self, posting: Posting) -> None:
        await _yield()
        self._s.postings.setdefault(
            posting.hash,
            (
                posting.rawText,
                _roundtrip(posting.model_dump(mode="json", exclude={"rawText"}, exclude_none=True)),
                self._s.clock.now(),
            ),
        )


class FakeProfileFactsRepo:
    def __init__(self, state: _State) -> None:
        self._s = state

    async def get(self, profile_id: str, facts_hash: str) -> list[PortfolioFact] | None:
        await _yield()
        found = self._s.profile_facts.get(profile_id)
        if found is None or found[0] != facts_hash:
            return None
        return [PortfolioFact.model_validate(f) for f in _roundtrip(found[1])]

    async def put(self, profile_id: str, facts_hash: str, facts: list[PortfolioFact]) -> None:
        await _yield()
        self._s.profile_facts[profile_id] = (facts_hash, _roundtrip([f.to_dict() for f in facts]))


class FakeUserFactRepo:
    def __init__(self, state: _State) -> None:
        self._s = state

    async def list_for_profile(self, profile_id: str) -> list[Fact]:
        await _yield()
        mine = sorted(
            (f for (pid, _), f in self._s.user_facts.items() if pid == profile_id),
            key=lambda f: f.created_at,
        )
        return [Fact.model_validate(_roundtrip(f.fact)) for f in mine]

    async def add(
        self,
        user_id: str | None,
        profile_id: str,
        fact: Fact,
        *,
        question: str,
        answer: str,
        match_id: str | None,
    ) -> bool:
        await _yield()
        key = (profile_id, fact.id)
        if key in self._s.user_facts:
            return False
        self._s.user_facts[key] = _UserFact(
            user_id=user_id,
            profile_id=profile_id,
            match_id=match_id,
            question=question,
            answer=answer,
            fact=_roundtrip(fact.model_dump(mode="json", exclude_none=True)),
            created_at=self._s.clock.now(),
        )
        return True

    # inspection helper (not part of the port)
    def rows(self) -> list[_UserFact]:
        return list(self._s.user_facts.values())


class FakeMatchRepo:
    def __init__(self, state: _State) -> None:
        self._s = state

    async def create(
        self,
        *,
        match_id: str,
        user_id: str | None,
        profile_id: str,
        posting_hash: str,
        facts: dict[str, Any],
        result: dict[str, Any],
        band: str,
        blocked: bool,
        version: int,
        now: datetime,
    ) -> StoredMatch:
        await _yield()
        if match_id in self._s.matches:
            raise ValueError("duplicate match id")
        if posting_hash not in self._s.postings:
            raise ValueError("the posting must be stored first")
        record = StoredMatch(
            id=match_id,
            user_id=user_id,
            profile_id=profile_id,
            posting_hash=posting_hash,
            facts=_roundtrip(facts),
            result=_roundtrip(result),
            band=band,
            blocked=blocked,
            version=version,
            created_at=now,
            updated_at=now,
        )
        self._s.matches[match_id] = record
        return record

    async def get(self, match_id: str, user_id: str | None) -> StoredMatch | None:
        await _yield()
        found = self._s.matches.get(match_id)
        if found is None or found.user_id != user_id:
            return None
        return dataclasses.replace(found, facts=_roundtrip(found.facts), result=_roundtrip(found.result))

    async def list_for_user(self, user_id: str, limit: int = 50) -> list[MatchListing]:
        await _yield()
        mine = sorted(
            (m for m in self._s.matches.values() if m.user_id == user_id),
            key=lambda m: (-_utc(m.created_at).timestamp(), m.id),
        )
        out: list[MatchListing] = []
        for m in mine[:limit]:
            title, company = posting_listing_fields(self._s.postings[m.posting_hash][1])
            out.append(MatchListing(m.id, title, company, m.band, m.created_at))
        return out

    async def save_result(
        self,
        match_id: str,
        user_id: str | None,
        *,
        expected_version: int,
        facts: dict[str, Any],
        result: dict[str, Any],
        band: str,
        blocked: bool,
        version: int,
        now: datetime,
    ) -> bool:
        await _yield()
        found = self._s.matches.get(match_id)
        if found is None or found.user_id != user_id or found.version != expected_version:
            return False
        self._s.matches[match_id] = dataclasses.replace(
            found,
            facts=_roundtrip(facts),
            result=_roundtrip(result),
            band=band,
            blocked=blocked,
            version=version,
            updated_at=now,
        )
        return True

    async def save_facts(
        self, match_id: str, user_id: str | None, facts: dict[str, Any], now: datetime
    ) -> bool:
        await _yield()
        found = self._s.matches.get(match_id)
        if found is None or found.user_id != user_id:
            return False
        self._s.matches[match_id] = dataclasses.replace(found, facts=_roundtrip(facts), updated_at=now)
        return True

    async def get_variant(self, match_id: str, kind: VariantKind) -> StoredVariant | None:
        await _yield()
        found = self._s.variants.get((match_id, kind))
        return None if found is None else dataclasses.replace(found, spec=_roundtrip(found.spec))

    async def put_variant(
        self, match_id: str, kind: VariantKind, *, spec: dict[str, Any], status: str, now: datetime
    ) -> StoredVariant:
        await _yield()
        if match_id not in self._s.matches:
            raise ValueError("no such match")
        existing = self._s.variants.get((match_id, kind))
        record = StoredVariant(
            id=existing.id if existing else secrets.token_hex(16),
            match_id=match_id,
            kind=kind,
            spec=_roundtrip(spec),
            status=status,
            version=existing.version + 1 if existing else 1,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._s.variants[(match_id, kind)] = record
        return record


@dataclass
class _Balance:
    credits: int = 0
    pass_until: datetime | None = None


@dataclass(frozen=True)
class _LedgerRow:
    id: str
    user_id: str
    delta: int
    reason: str
    ref: str | None
    created_at: datetime


class FakeBillingRepo:
    def __init__(self) -> None:
        self.orders: dict[str, StoredOrder] = {}
        self.balances: dict[str, _Balance] = {}
        self.ledger: list[_LedgerRow] = []

    # inspection helpers (not part of the port)
    def ledger_of(self, user_id: str) -> list[_LedgerRow]:
        return [row for row in self.ledger if row.user_id == user_id]

    def ledger_sum(self, user_id: str) -> int:
        return sum(row.delta for row in self.ledger_of(user_id))

    def _by_provider(self, provider_order_id: str) -> StoredOrder | None:
        return next((o for o in self.orders.values() if o.provider_order_id == provider_order_id), None)

    def _add(self, user_id: str, delta: int, reason: str, ref: str | None, now: datetime) -> _LedgerRow:
        row = _LedgerRow(secrets.token_hex(16), user_id, delta, reason, ref, _utc(now))
        self.ledger.append(row)
        return row

    def _live_uses(
        self, user_id: str, *, reasons: tuple[str, ...], ref: str | None = None, since: datetime | None = None
    ) -> list[_LedgerRow]:
        refunded = {row.ref for row in self.ledger_of(user_id) if row.reason == "refund"}
        found = [
            row
            for row in self.ledger_of(user_id)
            if row.reason in reasons
            and (ref is None or row.ref == ref)
            and (since is None or row.created_at >= _utc(since))
            and f"refund:{row.id}" not in refunded
        ]
        return sorted(found, key=lambda row: row.created_at, reverse=True)

    # orders
    async def add_order(self, rec: StoredOrder) -> None:
        await _yield()
        if self._by_provider(rec.provider_order_id) is not None or rec.id in self.orders:
            raise ValueError("duplicate order")
        self.orders[rec.id] = rec

    async def get_order(self, order_id: str) -> StoredOrder | None:
        await _yield()
        return self.orders.get(order_id)

    async def get_order_by_provider_id(self, provider_order_id: str) -> StoredOrder | None:
        await _yield()
        return self._by_provider(provider_order_id)

    async def settle_order(
        self,
        provider_order_id: str,
        *,
        now: datetime,
        credits: int,
        pass_days: int,
        raw: dict[str, Any] | None,
        amount_minor: int | None = None,
        currency: str | None = None,
    ) -> SettleResult:
        await _yield()
        order = self._by_provider(provider_order_id)
        if order is None:
            return SettleResult("unknown_order", None)
        if order.status in ("paid", "refunded"):
            return SettleResult("already_paid", order)
        if (amount_minor is not None and amount_minor != order.amount_minor) or (
            currency is not None and currency != order.currency
        ):
            return SettleResult("mismatch", order)
        paid = dataclasses.replace(order, status="paid", paid_at=_utc(now), raw=raw)
        self.orders[order.id] = paid
        if paid.user_id is None:
            return SettleResult("orphaned", paid)
        self._grant(paid.user_id, credits=credits, pass_days=pass_days, ref=paid.id, now=now)
        return SettleResult("granted", paid)

    async def fail_order(self, provider_order_id: str) -> bool:
        await _yield()
        order = self._by_provider(provider_order_id)
        if order is None or order.status != "created":
            return False
        self.orders[order.id] = dataclasses.replace(order, status="failed")
        return True

    # balances
    async def entitlements(self, user_id: str, *, month_start: datetime) -> StoredEntitlements:
        await _yield()
        balance = self.balances.get(user_id, _Balance())
        used = self._live_uses(user_id, reasons=("free_monthly",), since=month_start)
        return StoredEntitlements(user_id, balance.credits, balance.pass_until, len(used))

    def _grant(self, user_id: str, *, credits: int, pass_days: int, ref: str, now: datetime) -> bool:
        balance = self.balances.setdefault(user_id, _Balance())
        if any(r.reason == "purchase" and r.ref == ref for r in self.ledger_of(user_id)):
            return False
        balance.credits += credits
        if pass_days:
            start = max(_utc(now), balance.pass_until) if balance.pass_until is not None else _utc(now)
            balance.pass_until = start + timedelta(days=pass_days)
        self._add(user_id, credits, "purchase", ref, now)
        return True

    async def grant(self, user_id: str, *, credits: int, pass_days: int, ref: str, now: datetime) -> bool:
        await _yield()
        return self._grant(user_id, credits=credits, pass_days=pass_days, ref=ref, now=now)

    async def consume(
        self, user_id: str, ref: str, *, now: datetime, month_start: datetime, free_allowance: int
    ) -> ConsumeOutcome | None:
        await _yield()
        balance = self.balances.setdefault(user_id, _Balance())
        earlier = self._live_uses(user_id, reasons=("match", "free_monthly"), ref=ref)
        if earlier:
            use = earlier[0]
            source: ConsumeSource = (
                "free_monthly" if use.reason == "free_monthly" else ("credit" if use.delta < 0 else "pass")
            )
            return ConsumeOutcome(source, balance.credits, balance.pass_until, replayed=True)
        if balance.pass_until is not None and balance.pass_until > _utc(now):
            self._add(user_id, 0, "match", ref, now)
            return ConsumeOutcome("pass", balance.credits, balance.pass_until)
        if len(self._live_uses(user_id, reasons=("free_monthly",), since=month_start)) < free_allowance:
            self._add(user_id, 0, "free_monthly", ref, now)
            return ConsumeOutcome("free_monthly", balance.credits, balance.pass_until)
        if balance.credits > 0:
            balance.credits -= 1
            self._add(user_id, -1, "match", ref, now)
            return ConsumeOutcome("credit", balance.credits, balance.pass_until)
        return None

    async def release(self, user_id: str, ref: str, *, now: datetime) -> bool:
        await _yield()
        balance = self.balances.setdefault(user_id, _Balance())
        earlier = self._live_uses(user_id, reasons=("match", "free_monthly"), ref=ref)
        if not earlier:
            return False
        use = earlier[0]
        balance.credits -= use.delta
        self._add(user_id, -use.delta, "refund", f"refund:{use.id}", now)
        return True


class FakeRepositories:
    """The fakes over one shared state; ``bundle`` is the `Repositories` services are constructed with."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._state = _State(clock=clock or SystemClock())
        self.profiles = FakeProfileRepo(self._state)
        self.sessions = FakeSessionRepo(self._state)
        self.llm_calls = FakeLlmCallRepo(self._state)
        self.decisions = FakeDecisionLogRepo(self._state)
        self.limits = FakeLimitRepo(self._state)
        self.kv = FakeKvRepo(self._state)
        self.portfolios = FakePortfolioRepo(self._state)
        self.users = FakeUserRepo(self._state, self.portfolios)
        self.billing = FakeBillingRepo()
        self.postings = FakePostingRepo(self._state)
        self.profile_facts = FakeProfileFactsRepo(self._state)
        self.user_facts = FakeUserFactRepo(self._state)
        self.matches = FakeMatchRepo(self._state)
        self.discover = FakeDiscoverRepo()
        self.jobsearch = FakeJobSearchRepo()
        self.interview = FakeInterviewRepo()
        self.prepare = FakePrepareRepo()
        self.handoff = FakeHandoffRepo()
        self.weekly = FakeWeeklyRepo()
        self.bundle = Repositories(
            profiles=self.profiles,
            sessions=self.sessions,
            llm_calls=self.llm_calls,
            decisions=self.decisions,
            limits=self.limits,
            kv=self.kv,
            portfolios=self.portfolios,
            users=self.users,
            billing=self.billing,
            postings=self.postings,
            profile_facts=self.profile_facts,
            user_facts=self.user_facts,
            matches=self.matches,
            discover=self.discover,
            jobsearch=self.jobsearch,
            interview=self.interview,
            prepare=self.prepare,
            handoff=self.handoff,
            weekly=self.weekly,
        )


__all__ = [
    "FakeBillingRepo",
    "FakeDecisionLogRepo",
    "FakeKvRepo",
    "FakeLimitRepo",
    "FakeLlmCallRepo",
    "FakeMatchRepo",
    "FakePortfolioRepo",
    "FakePostingRepo",
    "FakeProfileFactsRepo",
    "FakeProfileRepo",
    "FakeRepositories",
    "FakeSessionRepo",
    "FakeUserFactRepo",
    "FakeUserRepo",
    "window_start",
]
