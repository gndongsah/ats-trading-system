"""Create append-only ATS-004 trade journal storage.

Revision ID: 20261014_01
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261014_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("ats_trades", sa.Column("trade_id", postgresql.UUID(as_uuid=True), primary_key=True))
    op.create_table(
        "ats_score_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_table(
        "ats_trade_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ats_trades.trade_id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_plan_id", postgresql.UUID(as_uuid=True)),
        sa.Column("score_snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ats_score_snapshots.id"), nullable=False),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.UniqueConstraint("trade_id", "version", name="uq_ats_plan_trade_version"),
        sa.CheckConstraint("version >= 1", name="ck_ats_plan_version_positive"),
        sa.CheckConstraint(
            "(version = 1 AND supersedes_plan_id IS NULL) OR "
            "(version > 1 AND supersedes_plan_id IS NOT NULL)",
            name="ck_ats_plan_supersession_shape",
        ),
    )
    op.create_table(
        "ats_order_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ats_trades.trade_id"), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ats_trade_plans.id"), nullable=False),
        sa.Column("order_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_table(
        "ats_fills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ats_trades.trade_id"), nullable=False),
        sa.Column("order_intent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ats_order_intents.id"), nullable=False),
        sa.Column("fill_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_table(
        "ats_position_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ats_trades.trade_id"), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ats_trade_plans.id"), nullable=False),
        sa.Column("snapshot_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_table(
        "ats_trade_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ats_trades.trade_id"), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ats_trade_plans.id"), nullable=False),
        sa.Column("result_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.UniqueConstraint("plan_id", name="uq_ats_result_plan"),
    )
    op.create_table(
        "ats_journal_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ats_trades.trade_id"), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ats_trade_plans.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan_followed", sa.Boolean()),
        sa.Column("mood", sa.String(length=32)),
        sa.Column("lesson", sa.String()),
        sa.Column("thesis", sa.String()),
        sa.Column("entry_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.UniqueConstraint("trade_id", "revision", name="uq_ats_journal_trade_revision"),
        sa.CheckConstraint("revision >= 1", name="ck_ats_journal_revision_positive"),
    )

    op.execute(
        """
        CREATE FUNCTION ats_reject_append_only_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'ATS journal records are append-only: % on % is forbidden',
                TG_OP, TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table_name in (
        "ats_trades",
        "ats_score_snapshots",
        "ats_trade_plans",
        "ats_order_intents",
        "ats_fills",
        "ats_position_snapshots",
        "ats_trade_results",
        "ats_journal_entries",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION ats_reject_append_only_mutation()
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_truncate_append_only
            BEFORE TRUNCATE ON {table_name}
            FOR EACH STATEMENT EXECUTE FUNCTION ats_reject_append_only_mutation()
            """
        )


def downgrade() -> None:
    for table_name in (
        "ats_journal_entries",
        "ats_trade_results",
        "ats_position_snapshots",
        "ats_fills",
        "ats_order_intents",
        "ats_trade_plans",
        "ats_score_snapshots",
        "ats_trades",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_append_only ON {table_name}")
        op.execute(
            f"DROP TRIGGER IF EXISTS {table_name}_truncate_append_only ON {table_name}"
        )
    op.execute("DROP FUNCTION IF EXISTS ats_reject_append_only_mutation()")
    for table_name in (
        "ats_journal_entries",
        "ats_trade_results",
        "ats_position_snapshots",
        "ats_fills",
        "ats_order_intents",
        "ats_trade_plans",
        "ats_score_snapshots",
        "ats_trades",
    ):
        op.drop_table(table_name)
