"""Add persisted ATS-021 agent evaluation snapshots.

Revision ID: 20261027_11
Revises: 20261027_10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261027_11"
down_revision = "20261027_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ats_agent_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("arm", sa.String(length=16), nullable=False),
        sa.Column("positive_label", sa.String(length=128), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observations", sa.Integer(), nullable=False),
        sa.Column("summary_data", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "arm IN ('llm_only', 'llm_jev')",
            name="ck_ats_agent_evaluation_arm",
        ),
    )
    op.create_index(
        "ix_ats_agent_evaluation_lookup",
        "ats_agent_evaluations",
        ["experiment_id", "arm", "positive_label", "as_of"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ats_agent_evaluation_lookup",
        table_name="ats_agent_evaluations",
    )
    op.drop_table("ats_agent_evaluations")
