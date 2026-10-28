"""Add persisted ATS-031 forward-test run evidence.

Revision ID: 20261027_12
Revises: 20261027_11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261027_12"
down_revision = "20261027_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ats_forward_test_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("run_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("playbook_version", sa.String(length=64), nullable=False),
        sa.Column("playbook_digest", sa.String(length=64), nullable=False),
        sa.Column("run_data", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "run_type = 'PAPER'",
            name="ck_ats_forward_test_run_type",
        ),
        sa.CheckConstraint(
            "status IN ('BLOCKED', 'ACCEPTED', 'FILLED')",
            name="ck_ats_forward_test_status",
        ),
    )
    op.create_index(
        "ix_ats_forward_test_as_of",
        "ats_forward_test_runs",
        ["as_of", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ats_forward_test_as_of",
        table_name="ats_forward_test_runs",
    )
    op.drop_table("ats_forward_test_runs")
