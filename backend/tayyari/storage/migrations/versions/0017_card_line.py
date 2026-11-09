"""The radar card's one line (dashboard ideas, 2026-11-10): card_snapshots.line.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-10

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: one nullable column on an
existing table, so the previous release keeps working against this schema.

``line`` is the sentence the owner chose to put under the shape (one of their read's sentences, or their own words, at most
200 characters); NULL for no line, which is how every card made before this revision reads.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("card_snapshots", sa.Column("line", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("card_snapshots", "line")
