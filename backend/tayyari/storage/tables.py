"""SQLAlchemy 2 table definitions: the one description of the schema.

`migrations/versions/0001_initial.py` is hand-written to match this module exactly, and a test compares
the two with Alembic's autogenerate diff, so a change here needs a new migration (additive only: see the
lint test in tests/unit/storage).

Conventions:

- Timestamps are UTC-aware everywhere (`UtcDateTime`: ``timestamptz`` on Postgres, a naive UTC value on
  SQLite that is made aware again on the way out). Naive datetimes handed in are assumed to be UTC.
- JSON columns are ``JSONB`` on Postgres and plain JSON on SQLite. A Python ``None`` is stored as SQL NULL
  (never the JSON value ``null``), so nullable JSON columns are queryable with ``IS NULL``.
- Money is integer micro-USD in ``BigInteger`` columns, so sums never drift.
- Log tables (``llm_calls``, ``jev_calls``, ``decision_log``) deliberately carry no foreign keys: retention
  deletes them on their own schedule and a dangling reference must never reject a log write.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Dialect,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ID_LEN = 96
"""Profile and session ids (stored ids are at most 81 characters: the legacy shape; new ones are 32 or 39)."""
HASH_LEN = 64
"""IP hashes (16 hex characters today) and sha256 state hashes (64)."""


def as_utc(value: datetime) -> datetime:
    """``value`` as an aware UTC datetime (a naive value is taken to be UTC already)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """``DateTime(timezone=True)`` that always returns aware UTC values, also on SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        utc = as_utc(value)
        # SQLite has no time zones: store the naive UTC wall time (the type puts the zone back on read).
        return utc.replace(tzinfo=None) if dialect.name == "sqlite" else utc

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else as_utc(value)


JSONType = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


metadata = Base.metadata


# ───────────────────────────── profiles and chat ─────────────────────────────


class ProfileRow(Base):
    """One analysed person: the Profile document the graph renders (the context lives next door)."""

    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_accessed_at: Mapped[datetime] = mapped_column(UtcDateTime)
    owner_hash: Mapped[str | None] = mapped_column(String(HASH_LEN))
    is_sample: Mapped[bool] = mapped_column(Boolean)
    schema_version: Mapped[int] = mapped_column(Integer)
    goal: Mapped[str | None] = mapped_column(Text)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONType)

    __table_args__ = (
        Index("ix_profiles_last_accessed_at", "last_accessed_at"),
        Index("ix_profiles_owner_hash_created_at", "owner_hash", "created_at"),
    )


class ProfileContextRow(Base):
    """Source text and reader outputs, split off so reading a profile never loads them."""

    __tablename__ = "profile_contexts"

    profile_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    context: Mapped[dict[str, Any]] = mapped_column(JSONType)
    bytes: Mapped[int] = mapped_column(Integer)


class ChatSessionRow(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("profiles.id", ondelete="CASCADE"))
    origin: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
    turn_count: Mapped[int] = mapped_column(Integer)
    ip_hash: Mapped[str | None] = mapped_column(String(HASH_LEN))

    __table_args__ = (
        Index("ix_chat_sessions_profile_id", "profile_id"),
        Index("ix_chat_sessions_updated_at", "updated_at"),
    )


class ChatMessageRow(Base):
    """One message. The composite primary key is the optimistic-append guard of `SessionRepo.append_turn`."""

    __tablename__ = "chat_messages"

    session_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("chat_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    tools: Mapped[list[dict[str, str]] | None] = mapped_column(JSONType)
    llm_call_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


# ───────────────────────────── usage and decision logs ─────────────────────────────


class LlmCallRow(Base):
    """One Anthropic API request."""

    __tablename__ = "llm_calls"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    purpose: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cache_write_tokens: Mapped[int] = mapped_column(Integer)
    cache_read_tokens: Mapped[int] = mapped_column(Integer)
    web_searches: Mapped[int] = mapped_column(Integer)
    cost_micro_usd: Mapped[int] = mapped_column(BigInteger)
    latency_ms: Mapped[int] = mapped_column(Integer)
    ttft_ms: Mapped[int | None] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(128))
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    error_kind: Mapped[str | None] = mapped_column(String(64))
    profile_id: Mapped[str | None] = mapped_column(String(ID_LEN))
    session_id: Mapped[str | None] = mapped_column(String(ID_LEN))
    analysis_id: Mapped[UUID | None] = mapped_column(Uuid)
    ip_hash: Mapped[str | None] = mapped_column(String(HASH_LEN))

    __table_args__ = (
        Index("ix_llm_calls_created_at", "created_at"),
        Index("ix_llm_calls_profile_id", "profile_id"),
        Index("ix_llm_calls_purpose_created_at", "purpose", "created_at"),
    )


class JevCallRow(Base):
    """One Jev request. ``questions`` plus the `decision_states` row make a complete replay request."""

    __tablename__ = "jev_calls"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    pack: Mapped[str] = mapped_column(String(64))
    model_requested: Mapped[str] = mapped_column(String(64))
    model_served: Mapped[str | None] = mapped_column(String(64))
    state_hash: Mapped[str] = mapped_column(String(HASH_LEN))
    questions: Mapped[dict[str, Any]] = mapped_column(JSONType)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(16))
    error_kind: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_micro_usd: Mapped[int] = mapped_column(BigInteger)
    latency_ms: Mapped[int] = mapped_column(Integer)
    profile_id: Mapped[str | None] = mapped_column(String(ID_LEN))
    analysis_id: Mapped[UUID | None] = mapped_column(Uuid)
    is_sample: Mapped[bool] = mapped_column(Boolean)
    payload_purged_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    __table_args__ = (
        Index("ix_jev_calls_pack_created_at", "pack", "created_at"),
        Index("ix_jev_calls_state_hash", "state_hash"),
        Index("ix_jev_calls_created_at", "created_at"),
    )


class DecisionStateRow(Base):
    """A Jev request state, stored once per distinct content (``hash`` = sha256 of its canonical JSON)."""

    __tablename__ = "decision_states"

    hash: Mapped[str] = mapped_column(String(HASH_LEN), primary_key=True)
    payload: Mapped[bytes | None] = mapped_column(LargeBinary)
    raw_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_ref_at: Mapped[datetime] = mapped_column(UtcDateTime)
    is_sample: Mapped[bool] = mapped_column(Boolean)


class DecisionLogRow(Base):
    """One decision outcome, linked (without foreign keys) to its Jev call and any Claude call."""

    __tablename__ = "decision_log"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    spec_id: Mapped[str] = mapped_column(String(96))
    spec_version: Mapped[str] = mapped_column(String(32))
    pack: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16))
    used: Mapped[bool] = mapped_column(Boolean)
    source_used: Mapped[str] = mapped_column(String(16))
    escalated: Mapped[bool] = mapped_column(Boolean)
    escalation_reason: Mapped[str | None] = mapped_column(String(32))
    call_id: Mapped[UUID | None] = mapped_column(Uuid)
    claude_call_id: Mapped[UUID | None] = mapped_column(Uuid)
    subject_ref: Mapped[str | None] = mapped_column(String(200))
    jev_answer: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    jev_confidence: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    claude_answer: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    incumbent: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    latency_ms: Mapped[int] = mapped_column(Integer)
    cost_micro_usd: Mapped[int] = mapped_column(BigInteger)
    profile_id: Mapped[str | None] = mapped_column(String(ID_LEN))
    analysis_id: Mapped[UUID | None] = mapped_column(Uuid)
    is_sample: Mapped[bool] = mapped_column(Boolean)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    __table_args__ = (
        Index("ix_decision_log_spec_id_created_at", "spec_id", "created_at"),
        Index("ix_decision_log_profile_id", "profile_id"),
        Index("ix_decision_log_call_id", "call_id"),
        Index("ix_decision_log_created_at", "created_at"),
    )


# ───────────────────────────── guard and bookkeeping ─────────────────────────────


class RateLimitHitRow(Base):
    """A fixed-window hit counter: one row per bucket and window start."""

    __tablename__ = "rate_limit_hits"

    bucket: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(UtcDateTime, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)

    __table_args__ = (Index("ix_rate_limit_hits_window_start", "window_start"),)


class SpendDailyRow(Base):
    """The spend ledger: micro-USD per UTC day and scope (``total``, ``anthropic``, ``jev``)."""

    __tablename__ = "spend_daily"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    micro_usd: Mapped[int] = mapped_column(BigInteger)
    calls: Mapped[int] = mapped_column(Integer)


class KvRow(Base):
    """Small bookkeeping values: the last maintenance run, the generated IP-hash key."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value: Mapped[Any | None] = mapped_column(JSONType)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


__all__ = [
    "HASH_LEN",
    "ID_LEN",
    "NAMING_CONVENTION",
    "Base",
    "ChatMessageRow",
    "ChatSessionRow",
    "DecisionLogRow",
    "DecisionStateRow",
    "JSONType",
    "JevCallRow",
    "KvRow",
    "LlmCallRow",
    "ProfileContextRow",
    "ProfileRow",
    "RateLimitHitRow",
    "SpendDailyRow",
    "UtcDateTime",
    "as_utc",
    "metadata",
]
