"""Add explicit run type to persisted trade results.

Revision ID: 20261026_08
Revises: 20261024_07
"""

import sqlalchemy as sa
from alembic import op

revision = "20261026_08"
down_revision = "20261024_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ats_trade_results",
        sa.Column(
            "run_type",
            sa.String(length=16),
            nullable=False,
            server_default="PAPER",
        ),
    )
    op.create_check_constraint(
        "ck_ats_trade_results_run_type",
        "ats_trade_results",
        "run_type IN ('LIVE', 'PAPER', 'REPLAY')",
    )
    op.create_index(
        "ix_ats_trade_results_run_type",
        "ats_trade_results",
        ["run_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_ats_trade_results_run_type", table_name="ats_trade_results")
    op.drop_constraint(
        "ck_ats_trade_results_run_type",
        "ats_trade_results",
        type_="check",
    )
    op.drop_column("ats_trade_results", "run_type")
