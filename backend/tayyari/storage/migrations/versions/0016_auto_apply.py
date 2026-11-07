"""Auto-apply: applications, application_events, ats_playbooks, run_metrics, apply_profiles.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-05

Written by hand to match `tayyari.storage.tables` exactly (a test compares the two). Additive only: five new tables and no
change to an existing one, so the previous release keeps working against this schema.

``applications`` is one job application made or tracked through auto-apply (its stage, the review of what will be sent, the
approval that alone allows a submit, and the worker's lease); ``application_events`` its history; ``ats_playbooks`` the
replayable per-ATS (or per-site) playbooks, built in or recorded from successful AI runs; ``run_metrics`` one row per engine
run for the learning loop (playbook vs AI, outcome, cost); ``apply_profiles`` a person's saved details, kept answers and
resume file. Applications and profiles go with their account (ON DELETE CASCADE); events go with their application.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine[Any]:
    """JSON on SQLite, JSONB on Postgres; a Python None is stored as SQL NULL."""
    return sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("owner_key", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(96), nullable=True),
        sa.Column("profile_id", sa.String(96), nullable=True),
        sa.Column("match_id", sa.String(96), nullable=True),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("job_url", sa.Text(), nullable=False),
        sa.Column("apply_url", sa.Text(), nullable=True),
        sa.Column("manual_url", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("ats", sa.String(64), nullable=False),
        sa.Column("company", sa.String(300), nullable=True),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column("location", sa.String(300), nullable=True),
        sa.Column("job", _json(), nullable=True),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("stage_reason", sa.Text(), nullable=True),
        sa.Column("needs", _json(), nullable=True),
        sa.Column("review", _json(), nullable=True),
        sa.Column("review_digest", sa.String(64), nullable=True),
        sa.Column("review_screenshot", sa.LargeBinary(), nullable=True),
        sa.Column("approved_at", _ts(), nullable=True),
        sa.Column("approved_digest", sa.String(64), nullable=True),
        sa.Column("submitted_at", _ts(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_until", _ts(), nullable=True),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_applications_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_applications"),
    )
    op.create_index("ix_applications_owner_key_created_at", "applications", ["owner_key", "created_at"])
    op.create_index("ix_applications_stage_updated_at", "applications", ["stage", "updated_at"])
    op.create_index("ix_applications_domain", "applications", ["domain"])
    op.create_index("ix_applications_user_id", "applications", ["user_id"])

    op.create_table(
        "application_events",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("application_id", sa.String(96), nullable=False),
        sa.Column("at", _ts(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("from_stage", sa.String(24), nullable=True),
        sa.Column("to_stage", sa.String(24), nullable=True),
        sa.Column("detail", _json(), nullable=True),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name="fk_application_events_application_id_applications",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_events"),
    )
    op.create_index("ix_application_events_application_id_at", "application_events", ["application_id", "at"])

    op.create_table(
        "ats_playbooks",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("ats", sa.String(64), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("origin", sa.String(12), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spec", _json(), nullable=False),
        sa.Column("successes", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.Column("promoted_at", _ts(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ats_playbooks"),
    )
    op.create_index("ix_ats_playbooks_ats_status", "ats_playbooks", ["ats", "status"])
    op.create_index("ix_ats_playbooks_domain", "ats_playbooks", ["domain"])

    op.create_table(
        "run_metrics",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("application_id", sa.String(96), nullable=True),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("run_kind", sa.String(12), nullable=False),
        sa.Column("ats", sa.String(64), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("playbook_id", sa.String(96), nullable=True),
        sa.Column("playbook_status", sa.String(12), nullable=True),
        sa.Column("agent", sa.String(32), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("failure_reason", sa.String(64), nullable=True),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("fields_total", sa.Integer(), nullable=False),
        sa.Column("fields_filled", sa.Integer(), nullable=False),
        sa.Column("fields_model", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_run_metrics"),
    )
    op.create_index("ix_run_metrics_created_at", "run_metrics", ["created_at"])
    op.create_index("ix_run_metrics_ats_created_at", "run_metrics", ["ats", "created_at"])
    op.create_index("ix_run_metrics_domain_created_at", "run_metrics", ["domain", "created_at"])

    op.create_table(
        "apply_profiles",
        sa.Column("owner_key", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(96), nullable=True),
        sa.Column("details", _json(), nullable=False),
        sa.Column("answers", _json(), nullable=False),
        sa.Column("resume_name", sa.String(255), nullable=True),
        sa.Column("resume_type", sa.String(100), nullable=True),
        sa.Column("resume_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_apply_profiles_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("owner_key", name="pk_apply_profiles"),
    )
    op.create_index("ix_apply_profiles_user_id", "apply_profiles", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_apply_profiles_user_id", table_name="apply_profiles")
    op.drop_table("apply_profiles")
    op.drop_index("ix_run_metrics_domain_created_at", table_name="run_metrics")
    op.drop_index("ix_run_metrics_ats_created_at", table_name="run_metrics")
    op.drop_index("ix_run_metrics_created_at", table_name="run_metrics")
    op.drop_table("run_metrics")
    op.drop_index("ix_ats_playbooks_domain", table_name="ats_playbooks")
    op.drop_index("ix_ats_playbooks_ats_status", table_name="ats_playbooks")
    op.drop_table("ats_playbooks")
    op.drop_index("ix_application_events_application_id_at", table_name="application_events")
    op.drop_table("application_events")
    op.drop_index("ix_applications_user_id", table_name="applications")
    op.drop_index("ix_applications_domain", table_name="applications")
    op.drop_index("ix_applications_stage_updated_at", table_name="applications")
    op.drop_index("ix_applications_owner_key_created_at", table_name="applications")
    op.drop_table("applications")
