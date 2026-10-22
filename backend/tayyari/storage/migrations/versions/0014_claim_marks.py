"""Claim marks (I2): claim_marks.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-22

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: one new table and no
change to an existing one, so the previous release keeps working against this schema.

``claim_marks`` is one row per judged claim of a finished practice session: the verdict, the rung that decided it, the
question and the verdict's quote, and the slot's Fix step with where the person put it (a Prepare plan, or kept in Practice)
and when it was ticked. It has no foreign key to its session, so a claim keeps its verdicts after the session's transcript
is purged at 30 days; it follows its profile (ON DELETE CASCADE). ``(profile_id, session_id, slot_id)`` is unique, so a
session's marks are written once.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "claim_marks",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("claim_key", sa.String(400), nullable=False),
        sa.Column("claim_text", sa.String(400), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("session_id", sa.String(96), nullable=False),
        sa.Column("slot_id", sa.String(16), nullable=False),
        sa.Column("session_kind", sa.String(16), nullable=False),
        sa.Column("slot_kind", sa.String(16), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("rung", sa.String(8), nullable=False),
        sa.Column("question", sa.String(200), nullable=False),
        sa.Column("quote", sa.String(300), nullable=True),
        sa.Column("story", _json(), nullable=True),
        sa.Column("marked_on", sa.Date(), nullable=False),
        sa.Column("fix_text", sa.String(200), nullable=True),
        sa.Column("fix_minutes", sa.Integer(), nullable=True),
        sa.Column("fix_plan_id", sa.String(32), nullable=True),
        sa.Column("fix_kept", sa.Boolean(), nullable=False),
        sa.Column("fix_added_at", _ts(), nullable=True),
        sa.Column("fix_done_at", _ts(), nullable=True),
        sa.Column("created_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["profiles.id"], name="fk_claim_marks_profile_id_profiles", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_claim_marks"),
        sa.UniqueConstraint(
            "profile_id", "session_id", "slot_id", name="uq_claim_marks_profile_id_session_id_slot_id"
        ),
    )
    op.create_index("ix_claim_marks_profile_id_claim_key", "claim_marks", ["profile_id", "claim_key"])
    op.create_index("ix_claim_marks_fix_plan_id", "claim_marks", ["fix_plan_id"])


def downgrade() -> None:
    op.drop_index("ix_claim_marks_fix_plan_id", table_name="claim_marks")
    op.drop_index("ix_claim_marks_profile_id_claim_key", table_name="claim_marks")
    op.drop_table("claim_marks")
