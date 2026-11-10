"""The application inbox: inbox_addresses, inbound_emails, verification_codes.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-10

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: three new tables and no
change to an existing one, so the previous release keeps working against this schema.

``inbox_addresses`` is one tracking address per account (unique ``user_id``), made only on opt-in, never reused (unique
``local_part``; the row outlives its account with ``user_id`` NULL). ``inbound_emails`` is one row per email Resend received
(metadata only, never the body), unique on Resend's id so a repeated webhook does nothing. ``verification_codes`` holds a
code or ATS link for 15 minutes for the auto-apply worker.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "inbox_addresses",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("user_id", sa.String(96), nullable=True),
        sa.Column("local_part", sa.String(32), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("copies", sa.Boolean(), nullable=False),
        sa.Column("consent_at", _ts(), nullable=False),
        sa.Column("consent_version", sa.String(16), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_inbox_addresses_user_id_users", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inbox_addresses"),
        sa.UniqueConstraint("user_id", name="uq_inbox_addresses_user_id"),
        sa.UniqueConstraint("local_part", name="uq_inbox_addresses_local_part"),
    )

    op.create_table(
        "inbound_emails",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("provider_email_id", sa.String(128), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("address_id", sa.String(96), nullable=True),
        sa.Column("user_id", sa.String(96), nullable=True),
        sa.Column("recipient", sa.String(64), nullable=True),
        sa.Column("sender", sa.String(320), nullable=False),
        sa.Column("sender_domain", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("received_at", _ts(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("stage", sa.String(16), nullable=True),
        sa.Column("application_id", sa.String(96), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("copy_status", sa.String(8), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claimed_until", _ts(), nullable=True),
        sa.Column("processed_at", _ts(), nullable=True),
        sa.Column("created_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["address_id"],
            ["inbox_addresses.id"],
            name="fk_inbound_emails_address_id_inbox_addresses",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_inbound_emails_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inbound_emails"),
        sa.UniqueConstraint("provider_email_id", name="uq_inbound_emails_provider_email_id"),
    )
    op.create_index("ix_inbound_emails_user_id_received_at", "inbound_emails", ["user_id", "received_at"])
    op.create_index(
        "ix_inbound_emails_address_id_received_at", "inbound_emails", ["address_id", "received_at"]
    )
    op.create_index("ix_inbound_emails_status_received_at", "inbound_emails", ["status", "received_at"])

    op.create_table(
        "verification_codes",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("user_id", sa.String(96), nullable=False),
        sa.Column("sender_domain", sa.String(255), nullable=False),
        sa.Column("code", sa.String(16), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("received_at", _ts(), nullable=False),
        sa.Column("expires_at", _ts(), nullable=False),
        sa.Column("used_at", _ts(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_verification_codes_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_verification_codes"),
    )
    op.create_index(
        "ix_verification_codes_user_id_received_at", "verification_codes", ["user_id", "received_at"]
    )
    op.create_index("ix_verification_codes_expires_at", "verification_codes", ["expires_at"])


def downgrade() -> None:
    op.drop_table("verification_codes")
    op.drop_table("inbound_emails")
    op.drop_table("inbox_addresses")
