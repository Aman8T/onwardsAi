"""Hand-offs from the marketing site: handoffs.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-20

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: one new table and no
change to an existing one, so the previous release keeps working against this schema.

``handoffs`` holds what the marketing site posted (a resume file, links, a goal, notes) until the app takes it, once, with
``GET /api/handoff/{id}``. The id is a random token and the only way back to the row. ``expires_at`` bounds how long it waits;
the next hand-off deletes the expired rows. There are no foreign keys: a hand-off belongs to no profile or account.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "handoffs",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("expires_at", _ts(), nullable=False),
        sa.Column("links", _json(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("resume_name", sa.String(255), nullable=True),
        sa.Column("resume_type", sa.String(100), nullable=True),
        sa.Column("resume_bytes", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_handoffs"),
    )
    op.create_index("ix_handoffs_expires_at", "handoffs", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_handoffs_expires_at", table_name="handoffs")
    op.drop_table("handoffs")
