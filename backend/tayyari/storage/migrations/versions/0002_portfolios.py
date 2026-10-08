"""Add portfolios table for portfolio storage.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Every name is
spelled out (constraints and indexes follow the naming convention of the metadata), so this file keeps
working unchanged if the convention ever changes.
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "portfolios",
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("doc", _json(), nullable=False),
        sa.Column("generated", _json(), nullable=False),
        sa.Column("facts_hash", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.Column("published_at", _ts(), nullable=True),
        sa.Column("last_viewed_at", _ts(), nullable=True),
        sa.PrimaryKeyConstraint("profile_id", name="pk_portfolios"),
        sa.UniqueConstraint("public_id", name="uq_portfolios_public_id"),
    )
    op.create_index("ix_portfolios_public_id", "portfolios", ["public_id"])
    op.create_index("ix_portfolios_updated_at", "portfolios", ["updated_at"])


def downgrade() -> None:
    op.drop_table("portfolios")
