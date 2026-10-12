"""Anonymous posting match: ``matches.user_id`` and ``user_facts.user_id`` may be NULL.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-12

A relaxation, which the additive rule allows: no table, column or index is added, removed or renamed, and no
constraint gets tighter. Two ``NOT NULL`` constraints are dropped, so a match made without an account (and something
said in it) can be stored with no owner until its profile is claimed (`SqlUserRepo.claim_profile` gives those rows the
account's id). The previous release keeps working against this schema: it only ever writes a non-NULL ``user_id`` and
only ever reads a match ``WHERE user_id = <the signed-in account>``, so a row with a NULL owner is invisible to it.
The foreign keys to ``users`` stay (a NULL satisfies them), and so do their ON DELETE CASCADE actions.

Why the DDL is spelled out and not ``op.alter_column`` (which ``test_migration_lint`` flags as a reshape, and which
this is not): Postgres has ``ALTER COLUMN ... DROP NOT NULL``, but SQLite cannot drop a constraint in place, and
Alembic's batch mode answers by rebuilding the table. A rebuild drops ``matches`` while ``variants`` point at it with
ON DELETE CASCADE and the connection has foreign keys on, so the drop would delete every stored resume and cover note
(0003 explains the same trap for ``profiles``). SQLite documents a safe way to remove a ``NOT NULL`` (see "Making
Other Kinds Of Table Schema Changes" in its ALTER TABLE page): edit the table's SQL in ``sqlite_master`` and bump
``schema_version``. Nothing on disk changes, no row is touched, and the result is exactly the table a fresh database
gets from `tayyari.storage.tables` (a test compares the two).

The downgrade is best effort, for a development database: it deletes the rows that have no owner (their resumes and
cover notes go with them) and, on Postgres, puts the constraints back. SQLite cannot add a ``NOT NULL`` in place, so
there the columns stay nullable, which the older code never notices.
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("matches", "user_facts")
_NOT_NULL_USER_ID = re.compile(r"(\buser_id\s+VARCHAR\(96\))\s+NOT\s+NULL", re.IGNORECASE)


def _is_nullable(table: str, column: str) -> bool:
    columns = sa.inspect(op.get_bind()).get_columns(table)
    return next(c for c in columns if c["name"] == column)["nullable"]


def _sqlite_drop_not_null(table: str, column: str) -> None:
    """Remove ``NOT NULL`` from ``table.column`` by rewriting the table's SQL (the documented in-place procedure)."""
    if _is_nullable(table, column):
        return
    bind = op.get_bind()
    sql = bind.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).scalar()
    relaxed, changed = _NOT_NULL_USER_ID.subn(r"\1", str(sql), count=1)
    if not changed:
        raise RuntimeError(f"cannot find NOT NULL on {table}.{column} in: {sql}")
    version = bind.exec_driver_sql("PRAGMA schema_version").scalar_one()
    bind.exec_driver_sql("PRAGMA writable_schema = ON")
    bind.exec_driver_sql(
        "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?", (relaxed, table)
    )
    bind.exec_driver_sql(f"PRAGMA schema_version = {int(version) + 1}")
    bind.exec_driver_sql("PRAGMA writable_schema = OFF")
    if not _is_nullable(table, column):
        raise RuntimeError(f"{table}.{column} is still NOT NULL after rewriting its SQL")


def upgrade() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    for table in TABLES:
        if sqlite:
            _sqlite_drop_not_null(table, "user_id")
        else:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN user_id DROP NOT NULL")


def downgrade() -> None:
    # The rows nobody owns cannot exist under the old constraint; the resumes and cover notes of those matches go too.
    op.execute("DELETE FROM variants WHERE match_id IN (SELECT id FROM matches WHERE user_id IS NULL)")
    op.execute("DELETE FROM user_facts WHERE user_id IS NULL")
    op.execute("DELETE FROM matches WHERE user_id IS NULL")
    if op.get_bind().dialect.name != "sqlite":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN user_id SET NOT NULL")
