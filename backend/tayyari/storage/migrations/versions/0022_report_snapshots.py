"""Where can I go next, the shareable report (N1): report_snapshots.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-17

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: one new table and no
change to an existing one, so the previous release keeps working against this schema.

``report_snapshots`` is one row per account: the owner's choices and the public content of their report, drawn from their
newest profile and already redacted by those choices. ``slug`` is the public link, NULL while the report is not shared, and
unique. The row goes with its account and with its profile (ON DELETE CASCADE).

Note: branch ``feat/inbound-email`` planned its own migration as 0022; if it merges after this one it becomes 0023 with
``down_revision`` 0022.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "report_snapshots",
        sa.Column("user_id", sa.String(96), nullable=False),
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("slug", sa.String(12), nullable=True),
        sa.Column("shared", sa.Boolean(), nullable=False),
        sa.Column("choices", _json(), nullable=False),
        sa.Column("content", _json(), nullable=False),
        sa.Column("version", sa.String(8), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_report_snapshots_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name="fk_report_snapshots_profile_id_profiles",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_report_snapshots"),
        sa.UniqueConstraint("slug", name="uq_report_snapshots_slug"),
    )


def downgrade() -> None:
    op.drop_table("report_snapshots")
