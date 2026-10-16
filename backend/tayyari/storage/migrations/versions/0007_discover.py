"""Discover: discover_listings, discover_runs.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-16

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: two new tables with
no foreign keys and no column on an existing table, so the previous release keeps working against this schema.

``discover_listings`` holds the jobs read from employers' own public boards (Greenhouse, Lever) that are in India, with
the plain text of the posting; ``discover_runs`` is the log of the refreshes that read them. Neither holds anything
about a person.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "discover_listings",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("employer_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("title_norm", sa.String(300), nullable=False),
        sa.Column("work_mode", sa.String(8), nullable=False),
        sa.Column("city", sa.String(40), nullable=True),
        sa.Column("locality", sa.String(48), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("precision", sa.String(8), nullable=False),
        sa.Column("remote_india", sa.Boolean(), nullable=False),
        sa.Column("location_text", sa.String(300), nullable=False),
        sa.Column("apply_url", sa.Text(), nullable=False),
        sa.Column("apply_host", sa.String(255), nullable=False),
        sa.Column("posted_at", _ts(), nullable=True),
        sa.Column("first_seen_at", _ts(), nullable=False),
        sa.Column("last_seen_at", _ts(), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("closed_at", _ts(), nullable=True),
        sa.Column("salary_text", sa.String(120), nullable=True),
        sa.Column("years_min", sa.Integer(), nullable=True),
        sa.Column("entry_level", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("posting_text", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_discover_listings"),
    )
    op.create_index("ix_discover_listings_status", "discover_listings", ["status"])
    op.create_index("ix_discover_listings_city", "discover_listings", ["city"])
    op.create_index("ix_discover_listings_employer_id", "discover_listings", ["employer_id"])

    op.create_table(
        "discover_runs",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("started_at", _ts(), nullable=False),
        sa.Column("finished_at", _ts(), nullable=True),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("employers_ok", sa.Integer(), nullable=False),
        sa.Column("employers_failed", sa.Integer(), nullable=False),
        sa.Column("listings_open", sa.Integer(), nullable=False),
        sa.Column("listings_closed", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_discover_runs"),
    )
    op.create_index("ix_discover_runs_started_at", "discover_runs", ["started_at"])


def downgrade() -> None:
    # Best effort, for a development database: the listings are re-read by the next refresh.
    op.drop_index("ix_discover_runs_started_at", table_name="discover_runs")
    op.drop_table("discover_runs")
    op.drop_index("ix_discover_listings_employer_id", table_name="discover_listings")
    op.drop_index("ix_discover_listings_city", table_name="discover_listings")
    op.drop_index("ix_discover_listings_status", table_name="discover_listings")
    op.drop_table("discover_listings")
