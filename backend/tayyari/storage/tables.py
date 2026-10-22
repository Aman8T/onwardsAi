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
    UniqueConstraint,
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
EMAIL_LEN = 320
"""The longest possible email address (RFC 5321: 64 + 1 + 255)."""
USER_AGENT_LEN = 200
"""Sign-in records keep at most this much of the User-Agent."""
PROVIDER_ID_LEN = 128
"""Provider order ids and ledger references (Razorpay ``order_`` ids are 20 characters, Stripe session ids ~66)."""


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
    user_id: Mapped[str | None] = mapped_column(String(ID_LEN), ForeignKey("users.id"))
    """The account that owns this profile (set by `UserRepo.claim_profile`); None while it is anonymous."""

    __table_args__ = (
        Index("ix_profiles_last_accessed_at", "last_accessed_at"),
        Index("ix_profiles_owner_hash_created_at", "owner_hash", "created_at"),
        Index("ix_profiles_user_id_created_at", "user_id", "created_at"),
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


# ───────────────────────────── portfolios ─────────────────────────────


class PortfolioRow(Base):
    """One portfolio document: the Portfolio JSON with metadata, facts_hash for extraction caching,
    and generated slots for "reset to generated wording". No foreign key: a portfolio survives profile
    deletion (60-day purge)."""

    __tablename__ = "portfolios"

    profile_id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    doc: Mapped[dict[str, Any]] = mapped_column(JSONType)
    generated: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)
    facts_hash: Mapped[str | None] = mapped_column(String(HASH_LEN))
    version: Mapped[int] = mapped_column(Integer)
    published: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_viewed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    user_id: Mapped[str | None] = mapped_column(String(ID_LEN))
    """The owning account, copied from the profile when it is claimed (no foreign key, like ``profile_id``)."""

    __table_args__ = (
        Index("ix_portfolios_public_id", "public_id"),
        Index("ix_portfolios_updated_at", "updated_at"),
        Index("ix_portfolios_user_id", "user_id"),
    )


# ───────────────────────────── accounts ─────────────────────────────


class UserRow(Base):
    """A person with an account. ``email`` is stored lower-cased, so its unique constraint is case-insensitive."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(EMAIL_LEN))
    """NULL for an account made from a phone-only Leafout sign-in (the app never stores the phone)."""
    name: Mapped[str | None] = mapped_column(Text)
    picture_url: Mapped[str | None] = mapped_column(Text)
    google_sub: Mapped[str | None] = mapped_column(String(255))
    supabase_sub: Mapped[str | None] = mapped_column(String(36))
    """The Leafout (Supabase) user id this account is linked to; unique."""
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_login_at: Mapped[datetime] = mapped_column(UtcDateTime)
    locale: Mapped[str | None] = mapped_column(String(35))
    currency: Mapped[str | None] = mapped_column(String(3))

    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        UniqueConstraint("google_sub", name="uq_users_google_sub"),
        Index("uq_users_supabase_sub", "supabase_sub", unique=True),
    )


class AuthSessionRow(Base):
    """One sign-in. ``id`` is the sha256 of the random cookie token, so a database leak cannot be replayed."""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(HASH_LEN), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ip_hash: Mapped[str | None] = mapped_column(String(HASH_LEN))
    user_agent: Mapped[str | None] = mapped_column(String(USER_AGENT_LEN))
    source: Mapped[str | None] = mapped_column(String(16))
    """``supabase`` for a session started from the Leafout site's cookie; NULL for the app's own sign-ins."""
    supabase_session_id: Mapped[str | None] = mapped_column(String(36))
    """The token's ``session_id`` claim (kept for later revocation; never logged)."""
    verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    """When the site's cookie last vouched for this session."""

    __table_args__ = (
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )


class MagicLinkRow(Base):
    """An emailed sign-in link, keyed by the sha256 of its token. Single use: ``used_at`` is set when it is spent."""

    __tablename__ = "magic_links"

    token_hash: Mapped[str] = mapped_column(String(HASH_LEN), primary_key=True)
    email: Mapped[str] = mapped_column(String(EMAIL_LEN))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    ip_hash: Mapped[str | None] = mapped_column(String(HASH_LEN))

    __table_args__ = (Index("ix_magic_links_expires_at", "expires_at"),)


# ───────────────────────────── payments and entitlements ─────────────────────────────


class OrderRow(Base):
    """One purchase attempt. ``provider_order_id`` is the provider's own id (Razorpay ``order_...``, Stripe Checkout
    Session ``cs_...``); the webhook and the browser hand-off both find the row by it, and it is unique, so a
    replayed event can only ever settle the order once.

    ``user_id`` is cleared (not the row deleted) when the account is deleted: the payment record outlives the
    account, and nothing is granted to an account that is gone. ``raw`` keeps a few provider ids, never an email
    address or a card detail.
    """

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(ID_LEN), ForeignKey("users.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(16))
    provider_order_id: Mapped[str] = mapped_column(String(PROVIDER_ID_LEN))
    product: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    paid_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    __table_args__ = (
        UniqueConstraint("provider_order_id", name="uq_orders_provider_order_id"),
        Index("ix_orders_user_id", "user_id"),
    )


class EntitlementRow(Base):
    """What an account may use right now: posting credits and the end of its pass. The row is also the lock that
    serialises every change to one account's balance (``UPDATE`` first, then read and write)."""

    __tablename__ = "entitlements"

    user_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    credits: Mapped[int] = mapped_column(Integer)
    pass_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


class CreditLedgerRow(Base):
    """Append-only: every change to ``entitlements.credits`` is a row with its ``delta`` (so the balance is always
    the sum of the deltas), and the free monthly postings and pass uses are rows with a delta of 0.

    ``reason``: ``purchase`` (``ref`` is the order id), ``match`` (``ref`` is the match id; -1 for a credit, 0 for a
    pass), ``free_monthly`` (0), ``refund`` (``ref`` is ``refund:<id of the row it reverses>``), ``admin``.
    """

    __tablename__ = "credit_ledger"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("users.id", ondelete="CASCADE"))
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(16))
    ref: Mapped[str | None] = mapped_column(String(PROVIDER_ID_LEN))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)

    __table_args__ = (Index("ix_credit_ledger_user_id_created_at", "user_id", "created_at"),)


# ───────────────────────────── posting match ─────────────────────────────

BAND_LEN = 16
"""``strong`` / ``good`` / ``stretch`` / ``weak`` (and room to grow)."""


class PostingRow(Base):
    """A job posting as read, shared by everyone who pasted the same text: ``hash`` is the sha256 of its normalised
    text (`match.text.posting_hash`). The reading holds no one's data (the reader never sees a person), so rows are
    not deleted with an account. ``parsed`` is the `Posting` JSON without ``rawText``, which lives in ``text``."""

    __tablename__ = "postings"

    hash: Mapped[str] = mapped_column(String(HASH_LEN), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class MatchRow(Base):
    """One posting matched against one profile. ``facts`` is the person's `FactBook` as it stood (the facts the
    result's ``factIds`` cite, and the structure the resume is composed from); ``result`` is the `MatchResult`;
    ``band`` and ``blocked`` repeat two of its fields for listing. Follows its account and its profile.
    ``user_id`` is NULL for a match made without an account (anonymous matching, migration 0006); claiming its profile
    gives it the account's id."""

    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(ID_LEN), ForeignKey("users.id", ondelete="CASCADE"))
    profile_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("profiles.id", ondelete="CASCADE"))
    posting_hash: Mapped[str] = mapped_column(String(HASH_LEN), ForeignKey("postings.hash"))
    facts: Mapped[dict[str, Any]] = mapped_column(JSONType)
    result: Mapped[dict[str, Any]] = mapped_column(JSONType)
    band: Mapped[str] = mapped_column(String(BAND_LEN))
    blocked: Mapped[bool] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)

    __table_args__ = (
        Index("ix_matches_user_id_created_at", "user_id", "created_at"),
        Index("ix_matches_profile_id", "profile_id"),
    )


class UserFactRow(Base):
    """Something a person said in their own words (an answer to a question, a figure for a resume line): the `Fact`
    JSON with the question it answered. Facts belong to the profile, so a later match can use them; ``id`` is a hash
    of ``(profile_id, fact id)``, so saying the same thing twice is one row. ``match_id`` is where it was said.
    ``user_id`` is NULL for something said without an account (migration 0006); claiming the profile gives it the
    account's id."""

    __tablename__ = "user_facts"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(ID_LEN), ForeignKey("users.id", ondelete="CASCADE"))
    profile_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("profiles.id", ondelete="CASCADE"))
    match_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("matches.id", ondelete="SET NULL")
    )
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    fact: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)

    __table_args__ = (
        Index("ix_user_facts_profile_id_created_at", "profile_id", "created_at"),
        Index("ix_user_facts_user_id", "user_id"),
    )


class VariantRow(Base):
    """The resume or the cover note built for a match (one of each: ``kind``). ``spec`` is the `ResumeVariant` or
    `CoverNote` JSON; ``status`` is ``needs_user`` while a resume has open questions, else ``ready``."""

    __tablename__ = "variants"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    match_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("matches.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(8))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)

    __table_args__ = (UniqueConstraint("match_id", "kind", name="uq_variants_match_id_kind"),)


class ProfileFactsRow(Base):
    """The portfolio fact extractor's output for a profile, kept so a second match does not read the sources again.
    ``facts_hash`` is `portfolio.facts.facts_hash` of the sources it was read from; a profile whose sources changed
    simply gets a new row in place of this one."""

    __tablename__ = "profile_facts"

    profile_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    facts_hash: Mapped[str] = mapped_column(String(HASH_LEN))
    facts: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


# ───────────────────────────── discover ─────────────────────────────


class DiscoverListingRow(Base):
    """One job on an employer's own board (Greenhouse or Lever) that is in India. ``id`` is a stable hash of
    ``(source, board, external id)`` (`discover.normalise.listing_id`), so a listing keeps its id across refreshes.

    ``status`` is ``open`` while the board lists it and ``closed`` once a refresh that read the board did not; a closed
    row is deleted 30 days after ``closed_at`` (`storage.maintenance`). ``lat``/``lng`` are approximate centroids of the
    locality (``precision`` ``locality``) or of the city (``city``); they exist so a distance can be worked out, never to
    show a pin. ``years_min`` is the years of experience the posting asks for, when it says; ``entry_level`` marks an
    entry-level title or a posting asking for at most two years. No row holds anything about a person: the person's area
    is chosen in the browser and sent with each request.
    """

    __tablename__ = "discover_listings"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    employer_id: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(PROVIDER_ID_LEN))
    title: Mapped[str] = mapped_column(String(300))
    title_norm: Mapped[str] = mapped_column(String(300))
    work_mode: Mapped[str] = mapped_column(String(8))
    city: Mapped[str | None] = mapped_column(String(40))
    locality: Mapped[str | None] = mapped_column(String(48))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    precision: Mapped[str] = mapped_column(String(8))
    remote_india: Mapped[bool] = mapped_column(Boolean)
    location_text: Mapped[str] = mapped_column(String(300))
    apply_url: Mapped[str] = mapped_column(Text)
    apply_host: Mapped[str] = mapped_column(String(255))
    posted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(8))
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    salary_text: Mapped[str | None] = mapped_column(String(120))
    years_min: Mapped[int | None] = mapped_column(Integer)
    entry_level: Mapped[bool] = mapped_column(Boolean)
    content_hash: Mapped[str] = mapped_column(String(HASH_LEN))
    posting_text: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        Index("ix_discover_listings_status", "status"),
        Index("ix_discover_listings_city", "city"),
        Index("ix_discover_listings_employer_id", "employer_id"),
    )


class DiscoverRunRow(Base):
    """One refresh of the employers' boards. ``status``: ``running`` while it works, ``ok`` when at least one board was
    read, ``failed`` when none was (or it crashed). A ``running`` row older than 20 minutes is a dead run (single flight
    ignores it). ``note`` says what went wrong in a few words, never a response body."""

    __tablename__ = "discover_runs"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(8))
    employers_ok: Mapped[int] = mapped_column(Integer)
    employers_failed: Mapped[int] = mapped_column(Integer)
    listings_open: Mapped[int] = mapped_column(Integer)
    listings_closed: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (Index("ix_discover_runs_started_at", "started_at"),)


class JobSearchRunRow(Base):
    """One finished search with AI. ``result`` is what the person saw (the `JobSearchRun` fields: roles, funnel, trail,
    note); ``postings`` holds, server-side only, the text of each shown posting by job id (``title``, ``employer``,
    ``applyUrl``, ``text``), so the prep page, the referral note and "See how you fit" read what the search read.
    Purged after 30 days (`storage.maintenance`). No foreign key: like every profile-scoped row here."""

    __tablename__ = "jobsearch_runs"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(ID_LEN))
    city: Mapped[str | None] = mapped_column(String(40))
    remote: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    result: Mapped[dict[str, Any]] = mapped_column(JSONType)
    postings: Mapped[dict[str, Any]] = mapped_column(JSONType)

    __table_args__ = (
        Index("ix_jobsearch_runs_profile_id", "profile_id", "created_at"),
        Index("ix_jobsearch_runs_created_at", "created_at"),
    )


class JobSearchDocRow(Base):
    """A role's prep page (``kind`` ``prep``) or referral note (``referral``), once written: one row per profile, job and
    kind (``id`` is a hash of the three), replaced by a rewrite. Purged after 30 days."""

    __tablename__ = "jobsearch_docs"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(ID_LEN))
    job_id: Mapped[str] = mapped_column(String(ID_LEN))
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    body: Mapped[dict[str, Any]] = mapped_column(JSONType)

    __table_args__ = (Index("ix_jobsearch_docs_created_at", "created_at"),)


class InterviewSessionRow(Base):
    """One practice interview (I1). ``state`` is everything the service needs to carry on (the plan, the phrased lines
    and hidden answer notes, the turns, the per-slot judgements, the work sample and the assistant log); ``debrief`` the
    `Debrief` once the interview is done, else NULL. ``version`` starts at 1 and goes up by one on every save, and a
    save only lands on the version before it (`InterviewRepo.save`), so two tabs answering at once cannot overwrite
    each other. Purged ``settings.interview.retention_days`` (30) after ``created_at`` (`storage.maintenance`). No
    foreign key: like every profile-scoped row here, it goes with the retention purge, not with the profile."""

    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(ID_LEN))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
    version: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict[str, Any]] = mapped_column(JSONType)
    debrief: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    __table_args__ = (
        Index("ix_interview_sessions_profile_id", "profile_id", "created_at"),
        Index("ix_interview_sessions_created_at", "created_at"),
    )


# ───────────────────────────── prepare ─────────────────────────────

PLAN_ID_LEN = 32
"""A plan's id: ``token_hex(16)``."""
ROLE_ID_LEN = 120
"""`contract.prepare.ROLE_ID_MAX_CHARS`: a `Role.id` of the graph, or a requirement's name."""


class PreparePlanRow(Base):
    """One plan for a role of a profile's graph (P1, "How do I prepare for this role?"). ``content`` is the `PreparePlan`
    JSON without ``state`` and ``version`` (the gaps, the evidence, the moves and the headline, written once);
    ``state`` is what the person changes (hours a week, the move shown, done tasks, links) and ``version`` goes up by one
    on every state save, which lands only on the version before it (`PrepareRepo.save_state`). ``requirement`` is NULL
    for a plan over the whole role and the requirement's name for a plan over one gap. The newest row for a
    ``(profile, role, requirement)`` is the one returned again; ``fresh`` writes a new one. The foreign key follows the
    profile: a plan goes when its profile is purged or its account deleted, so there is no retention step of its own."""

    __tablename__ = "prepare_plans"

    id: Mapped[str] = mapped_column(String(PLAN_ID_LEN), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("profiles.id", ondelete="CASCADE"))
    role_id: Mapped[str] = mapped_column(String(ROLE_ID_LEN))
    requirement: Mapped[str | None] = mapped_column(String(ROLE_ID_LEN))
    content: Mapped[dict[str, Any]] = mapped_column(JSONType)
    state: Mapped[dict[str, Any]] = mapped_column(JSONType)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)

    __table_args__ = (Index("ix_prepare_plans_profile_id_role_id", "profile_id", "role_id", "requirement"),)


# ───────────────────────────── claim marks (I2) ─────────────────────────────

CLAIM_MARK_ID_LEN = 32
"""A mark's id: ``token_hex(16)``. It is also the id of the mark's Fix step (`FixTask.id`)."""
CLAIM_KEY_LEN = 400
"""`contract.interview.CLAIM_KEY_MAX_CHARS`: the folded text of the evidence line."""
CLAIM_TEXT_LEN = 400
CLAIM_SOURCE_LEN = 120
CLAIM_QUESTION_LEN = 200
CLAIM_QUOTE_LEN = 300
FIX_TEXT_LEN = 200
"""`interview.debrief.FIX_MAX_CHARS`."""


class ClaimMarkRow(Base):
    """One judged claim of a finished practice session (I2): the verdict an evidence line got in an ``ask``, ``claim`` or
    ``story`` slot, the rung that decided it, the question as asked and the verdict's quote. Progress and re-drill read
    these, so a claim keeps its verdicts after its session's transcript is purged (``interview_sessions`` go at 30 days;
    a mark has no foreign key to its session). ``(profile_id, session_id, slot_id)`` is unique: a session's marks are
    written once, and writing them again adds nothing.

    The ``fix_*`` columns are the debrief's Fix step for that slot (NULL when the claim was Defended) and where the person
    put it: ``fix_plan_id`` (a Prepare plan of the same profile; a plan that is gone reads as Practice) or ``fix_kept``
    (kept in Practice), when (``fix_added_at``), and when it was ticked (``fix_done_at``). Ticking never changes a verdict.
    The row goes with its profile (ON DELETE CASCADE), so with the account too."""

    __tablename__ = "claim_marks"

    id: Mapped[str] = mapped_column(String(CLAIM_MARK_ID_LEN), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("profiles.id", ondelete="CASCADE"))
    claim_key: Mapped[str] = mapped_column(String(CLAIM_KEY_LEN))
    claim_text: Mapped[str] = mapped_column(String(CLAIM_TEXT_LEN))
    source: Mapped[str] = mapped_column(String(CLAIM_SOURCE_LEN))
    session_id: Mapped[str] = mapped_column(String(ID_LEN))
    slot_id: Mapped[str] = mapped_column(String(16))
    session_kind: Mapped[str] = mapped_column(String(16))
    slot_kind: Mapped[str] = mapped_column(String(16))
    verdict: Mapped[str] = mapped_column(String(16))
    rung: Mapped[str] = mapped_column(String(8))
    question: Mapped[str] = mapped_column(String(CLAIM_QUESTION_LEN))
    quote: Mapped[str | None] = mapped_column(String(CLAIM_QUOTE_LEN))
    story: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    marked_on: Mapped[date] = mapped_column(Date)
    fix_text: Mapped[str | None] = mapped_column(String(FIX_TEXT_LEN))
    fix_minutes: Mapped[int | None] = mapped_column(Integer)
    fix_plan_id: Mapped[str | None] = mapped_column(String(PLAN_ID_LEN))
    fix_kept: Mapped[bool] = mapped_column(Boolean)
    fix_added_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    fix_done_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)

    __table_args__ = (
        UniqueConstraint(
            "profile_id", "session_id", "slot_id", name="uq_claim_marks_profile_id_session_id_slot_id"
        ),
        Index("ix_claim_marks_profile_id_claim_key", "profile_id", "claim_key"),
        Index("ix_claim_marks_fix_plan_id", "fix_plan_id"),
    )


HANDOFF_ID_LEN = 64
"""A hand-off's id: ``secrets.token_urlsafe(24)`` (32 characters)."""


class HandoffRow(Base):
    """A resume, links and a goal posted by the marketing site for the app to take once (``POST /api/handoff``). The id is
    the visitor's only way back to it (a 192-bit random token carried in the redirect); the row is deleted when it is taken
    and, once ``expires_at`` has passed, by the next hand-off. No foreign keys: it belongs to no profile or account."""

    __tablename__ = "handoffs"

    id: Mapped[str] = mapped_column(String(HANDOFF_ID_LEN), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    links: Mapped[list[str]] = mapped_column(JSONType)
    goal: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    resume_name: Mapped[str | None] = mapped_column(String(255))
    resume_type: Mapped[str | None] = mapped_column(String(100))
    resume_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)

    __table_args__ = (Index("ix_handoffs_expires_at", "expires_at"),)


# ───────────────────────────── the Leafout Weekly ─────────────────────────────

WEEKLY_TOKEN_LEN = 64
"""An unsubscribe token: ``secrets.token_urlsafe(32)`` (43 characters)."""


class WeeklySubscriptionRow(Base):
    """One account's subscription to The Leafout Weekly (``status`` ``active``, ``paused`` or ``off``). ``email`` is where the
    paper goes (the account's own unless the reader gave another; NULL for a phone-only account, which gets the in-app paper
    only), ``day`` is ``mon`` or ``fri``, ``sections`` the parts the reader wants (a JSON list). ``unsubscribe_token`` is the
    secret in the one-click link of every email; it is fresh each time the status goes from ``off`` to ``active``. The row goes
    with its account (ON DELETE CASCADE)."""

    __tablename__ = "weekly_subscriptions"

    user_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    email: Mapped[str | None] = mapped_column(String(EMAIL_LEN))
    day: Mapped[str] = mapped_column(String(3))
    sections: Mapped[list[str]] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(8))
    paused_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    unsubscribe_token: Mapped[str] = mapped_column(String(WEEKLY_TOKEN_LEN))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)

    __table_args__ = (
        UniqueConstraint("unsubscribe_token", name="uq_weekly_subscriptions_unsubscribe_token"),
    )


class WeeklyEditionRow(Base):
    """One edition of The Leafout Weekly for one reader and one week. ``payload`` is the `WeeklyEdition` JSON without ``id``,
    ``createdAt``, ``emailedAt`` and ``readAt`` (those are the columns); ``week_of`` is the Monday (or Friday) of the week in
    India, and ``(user_id, week_of)`` is unique, so a week is never built twice for one reader. ``emailed_at`` is set when the
    email went out, ``read_at`` when the reader opened it in the app. Goes with the account (ON DELETE CASCADE)."""

    __tablename__ = "weekly_editions"

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("users.id", ondelete="CASCADE"))
    issue: Mapped[int] = mapped_column(Integer)
    week_of: Mapped[date] = mapped_column(Date)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    emailed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    read_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    __table_args__ = (
        UniqueConstraint("user_id", "week_of", name="uq_weekly_editions_user_id_week_of"),
        Index("ix_weekly_editions_week_of", "week_of"),
    )


__all__ = [
    "BAND_LEN",
    "CLAIM_KEY_LEN",
    "CLAIM_MARK_ID_LEN",
    "CLAIM_QUESTION_LEN",
    "CLAIM_QUOTE_LEN",
    "CLAIM_SOURCE_LEN",
    "CLAIM_TEXT_LEN",
    "EMAIL_LEN",
    "FIX_TEXT_LEN",
    "HANDOFF_ID_LEN",
    "HASH_LEN",
    "ID_LEN",
    "NAMING_CONVENTION",
    "PLAN_ID_LEN",
    "PROVIDER_ID_LEN",
    "ROLE_ID_LEN",
    "USER_AGENT_LEN",
    "WEEKLY_TOKEN_LEN",
    "AuthSessionRow",
    "Base",
    "ChatMessageRow",
    "ChatSessionRow",
    "ClaimMarkRow",
    "CreditLedgerRow",
    "DecisionLogRow",
    "DecisionStateRow",
    "DiscoverListingRow",
    "DiscoverRunRow",
    "EntitlementRow",
    "HandoffRow",
    "InterviewSessionRow",
    "JSONType",
    "JevCallRow",
    "JobSearchDocRow",
    "JobSearchRunRow",
    "KvRow",
    "LlmCallRow",
    "MagicLinkRow",
    "MatchRow",
    "OrderRow",
    "PortfolioRow",
    "PostingRow",
    "PreparePlanRow",
    "ProfileContextRow",
    "ProfileFactsRow",
    "ProfileRow",
    "RateLimitHitRow",
    "SpendDailyRow",
    "UserFactRow",
    "UserRow",
    "UtcDateTime",
    "VariantRow",
    "WeeklyEditionRow",
    "WeeklySubscriptionRow",
    "as_utc",
    "metadata",
]
