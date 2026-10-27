"""Add persisted ATS-020 triage experiment observations.

Revision ID: 20261027_10
Revises: 20261026_09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261027_10"
down_revision = "20261026_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ats_triage_experiments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("arm", sa.String(length=16), nullable=False),
        sa.Column("predicted_label", sa.String(length=128), nullable=False),
        sa.Column("probability", sa.Numeric(8, 7), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome_label", sa.String(length=128), nullable=True),
        sa.Column("outcome_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prediction_data", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_id",
            "case_id",
            "arm",
            name="uq_ats_triage_experiment_case_arm",
        ),
        sa.CheckConstraint(
            "arm IN ('llm_only', 'llm_jev')",
            name="ck_ats_triage_experiment_arm",
        ),
        sa.CheckConstraint(
            "probability >= 0 AND probability <= 1",
            name="ck_ats_triage_probability",
        ),
    )
    op.create_index(
        "ix_ats_triage_experiment_lookup",
        "ats_triage_experiments",
        ["experiment_id", "case_id", "arm", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ats_triage_experiment_lookup",
        table_name="ats_triage_experiments",
    )
    op.drop_table("ats_triage_experiments")
