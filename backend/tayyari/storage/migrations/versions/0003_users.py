"""Accounts: users, auth_sessions, magic_links, and the owner columns on profiles and portfolios.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: three new
tables and two nullable columns, so the previous release keeps working against this schema.

``profiles.user_id`` carries a foreign key (no ON DELETE action: deleting an account removes its profiles first, in
`SqlUserRepo.delete`). Postgres adds it with ALTER TABLE; SQLite cannot add a constraint to an existing table, but
it does accept a column-level REFERENCES in ADD COLUMN, so that path runs raw SQL (a batch rebuild of ``profiles``
would be wrong here: with foreign keys on, dropping the old table cascades into the contexts and chats that point
at it). SQLite reports such a column-level constraint without its ON DELETE clause, which is why none is declared.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("picture_url", sa.Text(), nullable=True),
        sa.Column("google_sub", sa.String(255), nullable=True),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("last_login_at", _ts(), nullable=False),
        sa.Column("locale", sa.String(35), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("google_sub", name="uq_users_google_sub"),
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(96), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("expires_at", _ts(), nullable=False),
        sa.Column("last_seen_at", _ts(), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(200), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_auth_sessions_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    op.create_table(
        "magic_links",
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("expires_at", _ts(), nullable=False),
        sa.Column("used_at", _ts(), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("token_hash", name="pk_magic_links"),
    )
    op.create_index("ix_magic_links_expires_at", "magic_links", ["expires_at"])

    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "ALTER TABLE profiles ADD COLUMN user_id VARCHAR(96) "
            "CONSTRAINT fk_profiles_user_id_users REFERENCES users (id)"
        )
    else:
        op.add_column("profiles", sa.Column("user_id", sa.String(96), nullable=True))
        op.create_foreign_key("fk_profiles_user_id_users", "profiles", "users", ["user_id"], ["id"])
    op.create_index("ix_profiles_user_id_created_at", "profiles", ["user_id", "created_at"])

    op.add_column("portfolios", sa.Column("user_id", sa.String(96), nullable=True))
    op.create_index("ix_portfolios_user_id", "portfolios", ["user_id"])


def downgrade() -> None:
    # Best effort, for a development database: the rows in these tables are the only copy of the accounts.
    op.drop_index("ix_portfolios_user_id", table_name="portfolios")
    op.drop_column("portfolios", "user_id")
    op.drop_index("ix_profiles_user_id_created_at", table_name="profiles")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_profiles_user_id_users", "profiles", type_="foreignkey")
    op.drop_column("profiles", "user_id")
    op.drop_table("magic_links")
    op.drop_table("auth_sessions")
    op.drop_table("users")
