"""Search with AI: jobsearch_runs, jobsearch_docs.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-16

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: two new tables with
no foreign keys and no column on an existing table, so the previous release keeps working against this schema.

``jobsearch_runs`` keeps each finished search with AI: what the person saw (the roles, the funnel, the trail) and,
server-side only, the text of each shown posting, so the prep page, the referral note and "See how you fit" read the
posting the search read. ``jobsearch_docs`` keeps a role's prep page and referral note once written. Both are purged
after 30 days.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "jobsearch_runs",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("city", sa.String(40), nullable=True),
        sa.Column("remote", sa.Boolean(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("result", _json(), nullable=False),
        sa.Column("postings", _json(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_jobsearch_runs"),
    )
    op.create_index("ix_jobsearch_runs_profile_id", "jobsearch_runs", ["profile_id", "created_at"])
    op.create_index("ix_jobsearch_runs_created_at", "jobsearch_runs", ["created_at"])

    op.create_table(
        "jobsearch_docs",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("job_id", sa.String(96), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("body", _json(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_jobsearch_docs"),
    )
    op.create_index("ix_jobsearch_docs_created_at", "jobsearch_docs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_jobsearch_docs_created_at", table_name="jobsearch_docs")
    op.drop_table("jobsearch_docs")
    op.drop_index("ix_jobsearch_runs_created_at", table_name="jobsearch_runs")
    op.drop_index("ix_jobsearch_runs_profile_id", table_name="jobsearch_runs")
    op.drop_table("jobsearch_runs")
