"""A second resume file merges into the same blocks, and every original is kept: resume_files.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-11

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: one new table and no
column on an existing table, so the previous release keeps working against this schema.

``resume_files`` keeps each resume file of a profile with its original bytes: ``origin`` is ``analysis`` (the file the
profile was analysed from; no merge) or ``upload`` (added later in /resume). ``facts`` are the facts as extracted, ``merge``
the report of how they merged into the person's blocks, ``merged_book`` the blocks as they stood after the file. The foreign
keys follow the account and the profile.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "resume_files",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("user_id", sa.String(96), nullable=True),
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("origin", sa.String(8), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime", sa.String(100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("facts", _json(), nullable=False),
        sa.Column("merge", _json(), nullable=True),
        sa.Column("merged_book", _json(), nullable=True),
        sa.Column("created_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_resume_files_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["profiles.id"], name="fk_resume_files_profile_id_profiles", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resume_files"),
    )
    op.create_index("ix_resume_files_profile_id", "resume_files", ["profile_id"])


def downgrade() -> None:
    # Best effort, for a development database: this table is the only copy of the files.
    op.drop_index("ix_resume_files_profile_id", table_name="resume_files")
    op.drop_table("resume_files")
