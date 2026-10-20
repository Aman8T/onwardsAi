"""The Leafout Weekly: weekly_subscriptions and weekly_editions.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-21

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: two new tables and no
change to an existing one, so the previous release keeps working against this schema.

``weekly_subscriptions`` is one row per account that ever subscribed: where the paper goes, which day, which parts, the status
and the secret of the one-click unsubscribe link. ``weekly_editions`` is the paper itself, one row per reader and week
(``week_of`` is the Monday or Friday in India); ``(user_id, week_of)`` is unique, so a week is never built twice for a reader.
Both follow their account (ON DELETE CASCADE).
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "weekly_subscriptions",
        sa.Column("user_id", sa.String(96), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("day", sa.String(3), nullable=False),
        sa.Column("sections", _json(), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("paused_until", _ts(), nullable=True),
        sa.Column("unsubscribe_token", sa.String(64), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_weekly_subscriptions_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_weekly_subscriptions"),
        sa.UniqueConstraint("unsubscribe_token", name="uq_weekly_subscriptions_unsubscribe_token"),
    )
    op.create_table(
        "weekly_editions",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("user_id", sa.String(96), nullable=False),
        sa.Column("issue", sa.Integer(), nullable=False),
        sa.Column("week_of", sa.Date(), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("emailed_at", _ts(), nullable=True),
        sa.Column("read_at", _ts(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_weekly_editions_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_weekly_editions"),
        sa.UniqueConstraint("user_id", "week_of", name="uq_weekly_editions_user_id_week_of"),
    )
    op.create_index("ix_weekly_editions_week_of", "weekly_editions", ["week_of"])


def downgrade() -> None:
    op.drop_index("ix_weekly_editions_week_of", table_name="weekly_editions")
    op.drop_table("weekly_editions")
    op.drop_table("weekly_subscriptions")
