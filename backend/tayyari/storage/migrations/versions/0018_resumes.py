"""/resume: one set of blocks, many resumes (R2): resumes.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-10

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: one new table and no
column on an existing table, so the previous release keeps working against this schema.

``resumes`` keeps each resume of a profile: ``kind`` is ``upload`` (the resume as the person wrote it, ``role_key`` empty)
or ``path`` (arranged for one next role, ``role_key`` that role's id), and a profile has at most one of each. ``draft`` is
the `ResumeVariant` being edited, ``final`` the one last finalised with its lint (NULL until then: the only version that
is downloaded), ``facts`` the person's fact book as it stood. Exactly one resume of a profile is the default. ``version``
goes up by one on every write of the draft and a write only lands on the version before it. ``user_id`` is NULL for a
profile with no account, as in ``matches``. The foreign keys follow the account and the profile: a resume goes with its
profile's purge or its account's deletion.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "resumes",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("user_id", sa.String(96), nullable=True),
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("role_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("draft", _json(), nullable=False),
        sa.Column("final", _json(), nullable=True),
        sa.Column("facts", _json(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.Column("finalised_at", _ts(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_resumes_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["profiles.id"], name="fk_resumes_profile_id_profiles", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resumes"),
        sa.UniqueConstraint("profile_id", "kind", "role_key", name="uq_resumes_profile_id_kind_role_key"),
    )
    op.create_index("ix_resumes_profile_id", "resumes", ["profile_id"])


def downgrade() -> None:
    # Best effort, for a development database: this table is the only copy of the resumes.
    op.drop_index("ix_resumes_profile_id", table_name="resumes")
    op.drop_table("resumes")
