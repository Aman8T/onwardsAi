"""The frozen copy of the resume an application was prepared with: applications.resume_copy.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-11

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: one nullable column on an
existing table, so the previous release keeps working against this schema.

``resume_copy`` is the default resume as it stood when the application's values were prepared: its id, title, finalised
date, the sha256 of the file in the approval digest and the finalised variant. NULL when the application was prepared with
the match's tailored resume or the saved upload, which is how every application made before this revision reads.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.add_column("applications", sa.Column("resume_copy", _json(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "resume_copy")
