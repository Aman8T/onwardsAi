"""The dashboard's notes follow the account: desk_notes.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-11

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: one new table and no
column on an existing table, so the previous release keeps working against this schema.

``desk_notes`` keeps the slips (a note to self or a pinned line) of an account's profile. The note's own id is made by
the browser, so the key is ``(user_id, profile_id, note_id)``. ``at`` is the browser's timestamp in milliseconds. The
foreign keys follow the account and the profile: a note goes with its profile's purge or its account's deletion.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "desk_notes",
        sa.Column("user_id", sa.String(96), nullable=False),
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("note_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("label", sa.String(60), nullable=True),
        sa.Column("by", sa.String(120), nullable=True),
        sa.Column("at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_desk_notes_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["profiles.id"], name="fk_desk_notes_profile_id_profiles", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "profile_id", "note_id", name="pk_desk_notes"),
    )


def downgrade() -> None:
    # Best effort, for a development database: this table is the only copy of the notes kept on the account.
    op.drop_table("desk_notes")
