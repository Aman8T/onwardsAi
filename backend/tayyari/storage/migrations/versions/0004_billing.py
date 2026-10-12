"""Payments and entitlements: orders, entitlements, credit_ledger.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: three new
tables, so the previous release keeps working against this schema.

``orders.user_id`` is nullable and set to NULL when the account is deleted (the payment record outlives the account);
``entitlements`` and ``credit_ledger`` follow their account (ON DELETE CASCADE).
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("user_id", sa.String(96), nullable=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_order_id", sa.String(128), nullable=False),
        sa.Column("product", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("paid_at", _ts(), nullable=True),
        sa.Column("raw", _json(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_orders_user_id_users", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.UniqueConstraint("provider_order_id", name="uq_orders_provider_order_id"),
    )
    op.create_index("ix_orders_user_id", "orders", ["user_id"])

    op.create_table(
        "entitlements",
        sa.Column("user_id", sa.String(96), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("pass_until", _ts(), nullable=True),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_entitlements_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_entitlements"),
    )

    op.create_table(
        "credit_ledger",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("user_id", sa.String(96), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("ref", sa.String(128), nullable=True),
        sa.Column("created_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_credit_ledger_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_credit_ledger"),
    )
    op.create_index("ix_credit_ledger_user_id_created_at", "credit_ledger", ["user_id", "created_at"])


def downgrade() -> None:
    # Best effort, for a development database: these tables are the only record of what was bought.
    op.drop_index("ix_credit_ledger_user_id_created_at", table_name="credit_ledger")
    op.drop_table("credit_ledger")
    op.drop_table("entitlements")
    op.drop_index("ix_orders_user_id", table_name="orders")
    op.drop_table("orders")
