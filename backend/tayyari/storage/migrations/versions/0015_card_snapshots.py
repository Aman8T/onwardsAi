"""The radar card (R1): card_snapshots.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-23

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: one new table and no
change to an existing one, so the previous release keeps working against this schema.

``card_snapshots`` is one row per account: what its radar card draws (the areas in the graph's order, up to three skills, and
an optional role), the profile it was drawn from, the theme, and whether it is shared. ``slug`` is the public link, NULL while
the card is not shared, and unique. The row goes with its account and with its profile (ON DELETE CASCADE).
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "card_snapshots",
        sa.Column("user_id", sa.String(96), nullable=False),
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("slug", sa.String(12), nullable=True),
        sa.Column("shared", sa.Boolean(), nullable=False),
        sa.Column("theme", sa.String(12), nullable=False),
        sa.Column("areas", _json(), nullable=False),
        sa.Column("skills", _json(), nullable=False),
        sa.Column("role_id", sa.String(120), nullable=True),
        sa.Column("role_title", sa.String(200), nullable=True),
        sa.Column("version", sa.String(8), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_card_snapshots_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["profiles.id"], name="fk_card_snapshots_profile_id_profiles", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_card_snapshots"),
        sa.UniqueConstraint("slug", name="uq_card_snapshots_slug"),
    )


def downgrade() -> None:
    op.drop_table("card_snapshots")
