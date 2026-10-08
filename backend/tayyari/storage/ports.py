"""Repository Protocols and the small value types they exchange.

Services depend on these, never on SQLAlchemy, so every service is unit-tested against the dict-backed
fakes in ``tests/fakes/repos.py``. The SQLAlchemy implementations live in `repos/`.

Profiles travel as JSON documents (``dict``): storage does not import the Profile model, so a change to
the model never needs a storage change (``schema_version`` records which shape a document has).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from tayyari.contract.records import DecisionRecord, JevCallRecord, LlmCallRecord, StateBlob
from tayyari.contract.stored import StoredContext


class SessionConflict(RuntimeError):
    """`SessionRepo.append_turn` lost a ``(session_id, seq)`` race twice in a row."""


class SessionNotFound(LookupError):
    """`SessionRepo.append_turn` was asked to append to a session that does not exist."""


@dataclass(frozen=True, slots=True)
class ToolEvent:
    """One tool use shown as a chip under an assistant message (``WebSearch`` or ``WebFetch``)."""

    name: str
    detail: str


@dataclass(frozen=True, slots=True)
class ChatMessage:
    seq: int
    role: str  # "user" | "assistant"
    content: str
    tools: list[ToolEvent] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ChatSession:
    id: str
    profile_id: str
    origin: dict[str, Any] | None
    turn_count: int
    messages: list[ChatMessage] = field(default_factory=list)
    """Oldest first; at most the last ``limit`` messages."""


class ProfileRepo(Protocol):
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
        """Insert or replace a profile and its context in one transaction (``created_at`` is kept)."""
        ...

    async def get_profile(self, pid: str) -> dict[str, Any] | None:
        """The profile document, or None. Touches ``last_accessed_at`` at most once an hour."""
        ...

    async def get_for_chat(self, pid: str) -> tuple[dict[str, Any], StoredContext] | None:
        """Profile and context in one round trip (also touches ``last_accessed_at``), or None."""
        ...

    async def purge_expired(self, cutoff: datetime) -> int:
        """Delete non-sample profiles last accessed before ``cutoff`` (their chats go too); returns the count."""
        ...


class SessionRepo(Protocol):
    async def load(self, sid: str, profile_id: str, limit: int = 40) -> ChatSession | None:
        """The session with its last ``limit`` messages (oldest first).

        None when ``sid`` is unknown *or belongs to another profile*: callers treat both as a fresh start.
        """
        ...

    async def create(
        self, profile_id: str, origin: dict[str, Any] | None, ip_hash: str | None
    ) -> ChatSession:
        """A new empty session with a fresh 128-bit id. The profile must exist (the SQL repository enforces it)."""
        ...

    async def append_turn(
        self,
        sid: str,
        *,
        user: str,
        assistant: str | None,
        tools: list[ToolEvent],
        llm_call_id: UUID | None,
    ) -> None:
        """Append the user message and the assistant reply with the next ``seq`` numbers.

        ``assistant=None`` stores only the user message (a turn with no visible text). ``tools`` and
        ``llm_call_id`` belong to the assistant message (to the user message when there is none).
        Optimistic: on a ``(session_id, seq)`` conflict it reloads and retries once, then raises
        `SessionConflict`. Raises `SessionNotFound` for an unknown session. Increments ``turn_count``.
        """
        ...


class LlmCallRepo(Protocol):
    async def record(self, rec: LlmCallRecord) -> None:
        """Insert the call and add its cost to ``spend_daily`` (``total`` and ``anthropic``), atomically.

        Idempotent per ``rec.id``: recording the same call again changes nothing.
        """
        ...


class DecisionLogRepo(Protocol):
    async def record_batch(
        self,
        states: list[StateBlob],
        calls: list[JevCallRecord],
        rows: list[DecisionRecord],
    ) -> None:
        """Write one flush of the decision log in one transaction.

        States are inserted or touched (``last_ref_at``), calls and rows are inserted (idempotent per id),
        and the cost of newly inserted Jev calls is added to ``spend_daily`` (``total`` and ``jev``).
        """
        ...

    def export(
        self, since: datetime, until: datetime | None = None, limit: int | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Decision rows in ``[since, until)``, oldest first, joined with their Jev call and decompressed state.

        Each item is JSON-ready: the decision columns at the top level, ``"call"`` (the Jev call, or None),
        and ``"state"`` (the decoded state, or None once its payload has been purged).
        """
        ...


class LimitRepo(Protocol):
    async def hit(self, bucket: str, window_s: int, now: datetime) -> int:
        """Count one hit in the fixed window containing ``now`` and return the new count (atomic).

        A counter is keyed by ``(bucket, window start)``: put the window size in the bucket name
        (``"analyze:min:<ip_hash>"``), or two sizes starting at the same instant would share a counter.
        """
        ...

    async def spent_today(self, day: date) -> int:
        """Micro-USD recorded for ``day`` in scope ``total`` (0 when there is none)."""
        ...

    async def purge_hits(self, before: datetime) -> int:
        """Delete counters whose window started before ``before``; returns the count."""
        ...


class KvRepo(Protocol):
    async def get(self, key: str) -> Any | None:
        """The stored JSON value, or None when the key is missing (or holds null)."""
        ...

    async def set(self, key: str, value: Any) -> None:
        """Insert or replace a value."""
        ...

    async def get_or_create_secret(self, key: str, nbytes: int = 32) -> bytes:
        """The secret stored under ``key``, creating ``nbytes`` of randomness on first use (race-safe).

        Concurrent callers, on this or another replica, all get the same bytes. An existing secret keeps
        its length whatever ``nbytes`` says.
        """
        ...


class PortfolioConflict(RuntimeError):
    """A portfolio save lost an optimistic-concurrency race (someone saved a newer version first)."""


@dataclass(frozen=True, slots=True)
class StoredPortfolio:
    """One portfolio row. ``doc`` is the `Portfolio` JSON (camelCase, tayyari.contract.portfolio shape)."""

    profile_id: str
    public_id: str
    doc: dict[str, Any]
    facts_hash: str | None
    """sha256 of the source texts the facts were extracted from (reuse the facts when unchanged)."""
    generated: list[dict[str, Any]]
    """The copy slots as last generated (model or fallback wording), so "reset to generated wording" can
    restore a slot the owner edited. Kept out of the `Portfolio` document the client sees."""
    version: int
    published: bool
    created_at: datetime
    updated_at: datetime


class PortfolioRepo(Protocol):
    """Portfolios live apart from profiles: no foreign key, so the 60-day profile purge never deletes a page."""

    async def get(self, profile_id: str) -> StoredPortfolio | None:
        """The owner's portfolio (one per profile), or None."""
        ...

    async def get_published(self, public_id: str) -> StoredPortfolio | None:
        """A published portfolio by its public id (None when unknown or unpublished); records the view time."""
        ...

    async def put(self, rec: StoredPortfolio, *, expected_version: int | None) -> StoredPortfolio:
        """Insert or update; returns the stored row with its new ``version`` and ``updated_at``.

        ``expected_version=None`` creates the row, or replaces it outright when one exists (a regenerate).
        Otherwise the update applies only when the stored version equals ``expected_version`` and raises
        `PortfolioConflict` when it does not. ``public_id`` and ``created_at`` of an existing row are kept.
        """
        ...

    async def delete(self, profile_id: str) -> bool:
        """Remove the owner's portfolio; True when a row was deleted."""
        ...

    async def purge_orphan_drafts(self, cutoff: datetime) -> int:
        """Delete unpublished portfolios last updated before ``cutoff`` whose profile no longer exists."""
        ...


__all__ = [
    "ChatMessage",
    "ChatSession",
    "DecisionLogRepo",
    "KvRepo",
    "LimitRepo",
    "LlmCallRepo",
    "PortfolioConflict",
    "PortfolioRepo",
    "ProfileRepo",
    "SessionConflict",
    "SessionNotFound",
    "SessionRepo",
    "StoredPortfolio",
    "ToolEvent",
]
