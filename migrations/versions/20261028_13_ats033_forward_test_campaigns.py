"""Add ATS-033 forward-test campaign persistence and run binding.

Revision ID: 20261028_13
Revises: 20261027_12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261028_13"
down_revision = "20261027_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ats_forward_test_campaigns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_type", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("playbook_version", sa.String(length=64), nullable=False),
        sa.Column("playbook_digest", sa.String(length=64), nullable=False),
        sa.Column("operational_policy_version", sa.String(length=64), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("manifest_data", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "run_type = 'PAPER'",
            name="ck_ats_forward_test_campaign_run_type",
        ),
        sa.CheckConstraint(
            "end_at > start_at",
            name="ck_ats_forward_test_campaign_window",
        ),
        sa.UniqueConstraint(
            "manifest_digest",
            name="uq_ats_forward_test_campaign_manifest_digest",
        ),
    )
    op.create_index(
        "ix_ats_forward_test_campaign_window",
        "ats_forward_test_campaigns",
        ["start_at", "end_at"],
    )

    op.add_column(
        "ats_forward_test_runs",
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "ats_forward_test_runs",
        sa.Column("campaign_digest", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_ats_forward_test_run_campaign",
        "ats_forward_test_runs",
        "ats_forward_test_campaigns",
        ["campaign_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_ats_forward_test_campaign_binding",
        "ats_forward_test_runs",
        "(campaign_id IS NULL AND campaign_digest IS NULL) OR "
        "(campaign_id IS NOT NULL AND campaign_digest IS NOT NULL)",
    )
    op.create_index(
        "ix_ats_forward_test_campaign_as_of",
        "ats_forward_test_runs",
        ["campaign_id", "as_of"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ats_forward_test_campaign_as_of",
        table_name="ats_forward_test_runs",
    )
    op.drop_constraint(
        "ck_ats_forward_test_campaign_binding",
        "ats_forward_test_runs",
        type_="check",
    )
    op.drop_constraint(
        "fk_ats_forward_test_run_campaign",
        "ats_forward_test_runs",
        type_="foreignkey",
    )
    op.drop_column("ats_forward_test_runs", "campaign_digest")
    op.drop_column("ats_forward_test_runs", "campaign_id")

    op.drop_index(
        "ix_ats_forward_test_campaign_window",
        table_name="ats_forward_test_campaigns",
    )
    op.drop_table("ats_forward_test_campaigns")
