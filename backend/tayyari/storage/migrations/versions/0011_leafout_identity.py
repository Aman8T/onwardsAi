"""Sign in with the Leafout account: ``users.supabase_sub``, ``auth_sessions.source`` / ``supabase_session_id`` /
``verified_at``, and ``users.email`` may be NULL.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-19

Additive, with one approved relaxation. New nullable columns: ``users.supabase_sub`` (the Leafout / Supabase user id, with a
unique index ``uq_users_supabase_sub``; a NULL never collides), and on ``auth_sessions`` ``source`` (``supabase`` for a session
started from the site's cookie, NULL for the app's own sign-ins), ``supabase_session_id`` (the token's ``session_id`` claim)
and ``verified_at`` (when the site's cookie last vouched for it). The previous release never reads or writes them and keeps
working against this schema.

The relaxation: ``users.email`` drops ``NOT NULL``, so a person who signs in to Leafout with a phone number only can have an
account (the app never stores the phone). The previous release always writes an email, so it is unaffected; a rollback is
safe while no row has a NULL email. The unique constraint on ``email`` stays (NULLs do not collide).

Why the DDL is spelled out and not ``op.alter_column`` (the same trap 0003 and 0006 describe): Postgres has
``ALTER COLUMN ... DROP NOT NULL``, but SQLite cannot drop a constraint in place, and Alembic's batch mode rebuilds the table.
A rebuild drops ``users`` while ``profiles``, ``auth_sessions``, ``orders`` and others point at it with ON DELETE CASCADE and
the connection has foreign keys on, which would delete everything an account owns. SQLite documents a safe way (edit the
table's SQL in ``sqlite_master`` and bump ``schema_version``); nothing on disk changes and the result is exactly the table a
fresh database gets from `tayyari.storage.tables`.

The downgrade is best effort, for a development database: it drops the index and the new columns and (on Postgres) puts
``NOT NULL`` back after removing the accounts that have no email. SQLite cannot add the constraint in place, so there ``email``
stays nullable, which the older code never notices.
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOT_NULL_EMAIL = re.compile(r"(\bemail\s+VARCHAR\(320\))\s+NOT\s+NULL", re.IGNORECASE)


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _is_nullable(table: str, column: str) -> bool:
    columns = sa.inspect(op.get_bind()).get_columns(table)
    return next(c for c in columns if c["name"] == column)["nullable"]


def _sqlite_drop_not_null_email() -> None:
    """Remove ``NOT NULL`` from ``users.email`` by rewriting the table's SQL (the documented in-place procedure)."""
    if _is_nullable("users", "email"):
        return
    bind = op.get_bind()
    sql = bind.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).scalar()
    relaxed, changed = _NOT_NULL_EMAIL.subn(r"\1", str(sql), count=1)
    if not changed:
        raise RuntimeError(f"cannot find NOT NULL on users.email in: {sql}")
    version = bind.exec_driver_sql("PRAGMA schema_version").scalar_one()
    bind.exec_driver_sql("PRAGMA writable_schema = ON")
    bind.exec_driver_sql(
        "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = 'users'", (relaxed,)
    )
    bind.exec_driver_sql(f"PRAGMA schema_version = {int(version) + 1}")
    bind.exec_driver_sql("PRAGMA writable_schema = OFF")
    if not _is_nullable("users", "email"):
        raise RuntimeError("users.email is still NOT NULL after rewriting its SQL")


def upgrade() -> None:
    op.add_column("users", sa.Column("supabase_sub", sa.String(36), nullable=True))
    op.create_index("uq_users_supabase_sub", "users", ["supabase_sub"], unique=True)
    op.add_column("auth_sessions", sa.Column("source", sa.String(16), nullable=True))
    op.add_column("auth_sessions", sa.Column("supabase_session_id", sa.String(36), nullable=True))
    op.add_column("auth_sessions", sa.Column("verified_at", _ts(), nullable=True))
    if op.get_bind().dialect.name == "sqlite":
        _sqlite_drop_not_null_email()
    else:
        op.execute("ALTER TABLE users ALTER COLUMN email DROP NOT NULL")


def downgrade() -> None:
    op.drop_index("uq_users_supabase_sub", table_name="users")
    if op.get_bind().dialect.name != "sqlite":
        op.execute("DELETE FROM users WHERE email IS NULL")
        op.execute("ALTER TABLE users ALTER COLUMN email SET NOT NULL")
    # SQLite (3.35 and later) drops a column in place when no index or constraint uses it, without rebuilding the table.
    for table, column in (
        ("auth_sessions", "verified_at"),
        ("auth_sessions", "supabase_session_id"),
        ("auth_sessions", "source"),
        ("users", "supabase_sub"),
    ):
        op.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
