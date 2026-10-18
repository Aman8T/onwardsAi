"""Prepare (help me prepare, as blocks): prepare_plans.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-18

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: one new table and no
column on an existing table, so the previous release keeps working against this schema.

``prepare_plans`` keeps each plan for a role of a profile's graph: ``content`` is the plan as written once (the gaps, the
evidence, the moves and the headline), ``state`` what the person changes (hours a week, the move shown, done tasks, links).
``version`` goes up by one on every state save and a save only lands on the version before it, so two tabs cannot overwrite
each other. The foreign key follows the profile: a plan goes with its profile's purge or its account's deletion.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "prepare_plans",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("role_id", sa.String(120), nullable=False),
        sa.Column("requirement", sa.String(120), nullable=True),
        sa.Column("content", _json(), nullable=False),
        sa.Column("state", _json(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["profiles.id"], name="fk_prepare_plans_profile_id_profiles", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_prepare_plans"),
    )
    op.create_index(
        "ix_prepare_plans_profile_id_role_id", "prepare_plans", ["profile_id", "role_id", "requirement"]
    )


def downgrade() -> None:
    op.drop_index("ix_prepare_plans_profile_id_role_id", table_name="prepare_plans")
    op.drop_table("prepare_plans")
