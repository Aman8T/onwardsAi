"""The plans (B2): plan_usage and plan_interest.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-17

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: two new tables and no
change to an existing one, so the previous release keeps working against this schema.

``plan_usage`` is one row per counted use of a plan feature (a new analysis, a search with AI, a practice session, a coach
question), unique on ``(user_id, feature, ref)`` so a use is counted once; Just Looking counts the rows since the month
began. ``plan_interest`` is one row per closed plan an account asked to be told about. Both go with their account
(ON DELETE CASCADE).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "plan_usage",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("user_id", sa.String(96), nullable=False),
        sa.Column("feature", sa.String(16), nullable=False),
        sa.Column("ref", sa.String(64), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_plan_usage_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_plan_usage"),
        sa.UniqueConstraint("user_id", "feature", "ref", name="uq_plan_usage_user_id_feature_ref"),
    )
    op.create_index(
        "ix_plan_usage_user_id_feature_created_at", "plan_usage", ["user_id", "feature", "created_at"]
    )
    op.create_table(
        "plan_interest",
        sa.Column("user_id", sa.String(96), nullable=False),
        sa.Column("plan", sa.String(16), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_plan_interest_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "plan", name="pk_plan_interest"),
    )


def downgrade() -> None:
    op.drop_table("plan_interest")
    op.drop_index("ix_plan_usage_user_id_feature_created_at", table_name="plan_usage")
    op.drop_table("plan_usage")
