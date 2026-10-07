"""Initial schema: profiles, chat, usage and decision logs, rate limits, spend ledger, kv.

Revision ID: 0001
Revises:
Create Date: 2026-10-07

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Every name is
spelled out (constraints and indexes follow the naming convention of the metadata), so this file keeps
working unchanged if the convention ever changes.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("last_accessed_at", _ts(), nullable=False),
        sa.Column("owner_hash", sa.String(64), nullable=True),
        sa.Column("is_sample", sa.Boolean(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("profile", _json(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_profiles"),
    )
    op.create_index("ix_profiles_last_accessed_at", "profiles", ["last_accessed_at"])
    op.create_index("ix_profiles_owner_hash_created_at", "profiles", ["owner_hash", "created_at"])

    op.create_table(
        "profile_contexts",
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("context", _json(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name="fk_profile_contexts_profile_id_profiles",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("profile_id", name="pk_profile_contexts"),
    )

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("origin", _json(), nullable=True),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name="fk_chat_sessions_profile_id_profiles",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chat_sessions"),
    )
    op.create_index("ix_chat_sessions_profile_id", "chat_sessions", ["profile_id"])
    op.create_index("ix_chat_sessions_updated_at", "chat_sessions", ["updated_at"])

    op.create_table(
        "chat_messages",
        sa.Column("session_id", sa.String(96), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tools", _json(), nullable=True),
        sa.Column("llm_call_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_chat_messages_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "seq", name="pk_chat_messages"),
    )

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("web_searches", sa.Integer(), nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("ttft_ms", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("stop_reason", sa.String(32), nullable=True),
        sa.Column("error_kind", sa.String(64), nullable=True),
        sa.Column("profile_id", sa.String(96), nullable=True),
        sa.Column("session_id", sa.String(96), nullable=True),
        sa.Column("analysis_id", sa.Uuid(), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_llm_calls"),
    )
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])
    op.create_index("ix_llm_calls_profile_id", "llm_calls", ["profile_id"])
    op.create_index("ix_llm_calls_purpose_created_at", "llm_calls", ["purpose", "created_at"])

    op.create_table(
        "jev_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("model_requested", sa.String(64), nullable=False),
        sa.Column("model_served", sa.String(64), nullable=True),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("questions", _json(), nullable=False),
        sa.Column("response", _json(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_kind", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.String(96), nullable=True),
        sa.Column("analysis_id", sa.Uuid(), nullable=True),
        sa.Column("is_sample", sa.Boolean(), nullable=False),
        sa.Column("payload_purged_at", _ts(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_jev_calls"),
    )
    op.create_index("ix_jev_calls_pack_created_at", "jev_calls", ["pack", "created_at"])
    op.create_index("ix_jev_calls_state_hash", "jev_calls", ["state_hash"])
    op.create_index("ix_jev_calls_created_at", "jev_calls", ["created_at"])

    op.create_table(
        "decision_states",
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=True),
        sa.Column("raw_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("last_ref_at", _ts(), nullable=False),
        sa.Column("is_sample", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("hash", name="pk_decision_states"),
    )

    op.create_table(
        "decision_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("spec_id", sa.String(96), nullable=False),
        sa.Column("spec_version", sa.String(32), nullable=False),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
        sa.Column("source_used", sa.String(16), nullable=False),
        sa.Column("escalated", sa.Boolean(), nullable=False),
        sa.Column("escalation_reason", sa.String(32), nullable=True),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("claude_call_id", sa.Uuid(), nullable=True),
        sa.Column("subject_ref", sa.String(200), nullable=True),
        sa.Column("jev_answer", _json(), nullable=True),
        sa.Column("jev_confidence", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("claude_answer", _json(), nullable=True),
        sa.Column("incumbent", _json(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("profile_id", sa.String(96), nullable=True),
        sa.Column("analysis_id", sa.Uuid(), nullable=True),
        sa.Column("is_sample", sa.Boolean(), nullable=False),
        sa.Column("extra", _json(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_decision_log"),
    )
    op.create_index("ix_decision_log_spec_id_created_at", "decision_log", ["spec_id", "created_at"])
    op.create_index("ix_decision_log_profile_id", "decision_log", ["profile_id"])
    op.create_index("ix_decision_log_call_id", "decision_log", ["call_id"])
    op.create_index("ix_decision_log_created_at", "decision_log", ["created_at"])

    op.create_table(
        "rate_limit_hits",
        sa.Column("bucket", sa.String(200), nullable=False),
        sa.Column("window_start", _ts(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("bucket", "window_start", name="pk_rate_limit_hits"),
    )
    op.create_index("ix_rate_limit_hits_window_start", "rate_limit_hits", ["window_start"])

    op.create_table(
        "spend_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("day", "scope", name="pk_spend_daily"),
    )

    op.create_table(
        "kv",
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("value", _json(), nullable=True),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_kv"),
    )


def downgrade() -> None:
    for table in (
        "kv",
        "spend_daily",
        "rate_limit_hits",
        "decision_log",
        "decision_states",
        "jev_calls",
        "llm_calls",
        "chat_messages",
        "chat_sessions",
        "profile_contexts",
        "profiles",
    ):
        op.drop_table(table)
