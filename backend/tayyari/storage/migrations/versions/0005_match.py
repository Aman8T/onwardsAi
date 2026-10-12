"""Posting match: postings, matches, user_facts, variants, profile_facts.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-11

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: five new tables,
so the previous release keeps working against this schema.

``matches``, ``user_facts`` and ``profile_facts`` follow their account and profile (ON DELETE CASCADE), ``variants``
follow their match, and a user fact outlives the match it was said in (ON DELETE SET NULL). ``postings`` are shared
by everyone who pasted the same text and belong to no account, so ``matches.posting_hash`` has no ON DELETE action.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "postings",
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("parsed", _json(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.PrimaryKeyConstraint("hash", name="pk_postings"),
    )

    op.create_table(
        "matches",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("user_id", sa.String(96), nullable=False),
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("posting_hash", sa.String(64), nullable=False),
        sa.Column("facts", _json(), nullable=False),
        sa.Column("result", _json(), nullable=False),
        sa.Column("band", sa.String(16), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_matches_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["profiles.id"], name="fk_matches_profile_id_profiles", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["posting_hash"], ["postings.hash"], name="fk_matches_posting_hash_postings"),
        sa.PrimaryKeyConstraint("id", name="pk_matches"),
    )
    op.create_index("ix_matches_user_id_created_at", "matches", ["user_id", "created_at"])
    op.create_index("ix_matches_profile_id", "matches", ["profile_id"])

    op.create_table(
        "user_facts",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("user_id", sa.String(96), nullable=False),
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("match_id", sa.String(96), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("fact", _json(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_facts_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["profiles.id"], name="fk_user_facts_profile_id_profiles", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["match_id"], ["matches.id"], name="fk_user_facts_match_id_matches", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_facts"),
    )
    op.create_index("ix_user_facts_profile_id_created_at", "user_facts", ["profile_id", "created_at"])
    op.create_index("ix_user_facts_user_id", "user_facts", ["user_id"])

    op.create_table(
        "variants",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("match_id", sa.String(96), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("spec", _json(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["match_id"], ["matches.id"], name="fk_variants_match_id_matches", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_variants"),
        sa.UniqueConstraint("match_id", "kind", name="uq_variants_match_id_kind"),
    )

    op.create_table(
        "profile_facts",
        sa.Column("profile_id", sa.String(96), nullable=False),
        sa.Column("facts_hash", sa.String(64), nullable=False),
        sa.Column("facts", _json(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["profiles.id"], name="fk_profile_facts_profile_id_profiles", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("profile_id", name="pk_profile_facts"),
    )


def downgrade() -> None:
    # Best effort, for a development database: these tables are the only copy of the matches.
    op.drop_table("profile_facts")
    op.drop_table("variants")
    op.drop_index("ix_user_facts_user_id", table_name="user_facts")
    op.drop_index("ix_user_facts_profile_id_created_at", table_name="user_facts")
    op.drop_table("user_facts")
    op.drop_index("ix_matches_profile_id", table_name="matches")
    op.drop_index("ix_matches_user_id_created_at", table_name="matches")
    op.drop_table("matches")
    op.drop_table("postings")
