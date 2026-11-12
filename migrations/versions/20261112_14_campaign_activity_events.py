"""ATS-036E campaign activity event ledger.

Revision ID: 20261112_14
Revises: 20261028_13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261112_14"
down_revision = "20261028_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ats_campaign_activity_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_digest", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column(
            "event_data",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_ats_campaign_activity_sequence"),
        sa.CheckConstraint("attempt >= 0", name="ck_ats_campaign_activity_attempt"),
        sa.CheckConstraint(
            "status IN ('SCHEDULED','RUNNING','COMPLETED','HOLD','FAILED','MISSED','RETRY_EXHAUSTED')",
            name="ck_ats_campaign_activity_status",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["ats_forward_test_campaigns.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "campaign_id",
            "job_id",
            "sequence",
            name="uq_ats_campaign_activity_job_sequence",
        ),
    )
    op.create_index(
        "ix_ats_campaign_activity_campaign_schedule",
        "ats_campaign_activity_events",
        ["campaign_id", "scheduled_at", "job_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ats_campaign_activity_campaign_schedule",
        table_name="ats_campaign_activity_events",
    )
    op.drop_table("ats_campaign_activity_events")
