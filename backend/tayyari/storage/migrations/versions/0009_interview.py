"""Practice the interview: interview_sessions.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-17

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: one new table with
no foreign key and no column on an existing table, so the previous release keeps working against this schema.

``interview_sessions`` keeps each practice interview: ``state`` is everything the service needs to carry on (the plan,
the phrased lines, the turns, the per-slot judgements, the work sample and the assistant log), ``debrief`` the debrief
once the interview is done (NULL while it is active). ``version`` goes up by one on every save and a save only lands on
the version before it, so two tabs answering at once cannot overwrite each other. Rows are purged after 30 days.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", _json(), nullable=False),
        sa.Column("debrief", _json(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_interview_sessions"),
    )
    op.create_index("ix_interview_sessions_profile_id", "interview_sessions", ["profile_id", "created_at"])
    op.create_index("ix_interview_sessions_created_at", "interview_sessions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_interview_sessions_created_at", table_name="interview_sessions")
    op.drop_index("ix_interview_sessions_profile_id", table_name="interview_sessions")
    op.drop_table("interview_sessions")
