"""Bind loss-state audit events to the approved ATS-009 policy version.

Revision ID: 20261020_06
Revises: 20261020_05
"""

import sqlalchemy as sa
from alembic import op

revision = "20261020_06"
down_revision = "20261020_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ats_loss_state_events",
        sa.Column(
            "policy_version",
            sa.String(64),
            nullable=False,
            server_default="ATS-009-v0.1",
        ),
    )
    op.alter_column("ats_loss_state_events", "policy_version", server_default=None)


def downgrade() -> None:
    op.drop_column("ats_loss_state_events", "policy_version")
