"""Add append-only portfolio snapshots and risk reservation events.

Revision ID: 20261016_04
Revises: 20261014_03
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261016_04"
down_revision = "20261014_03"
branch_labels = None
depends_on = None

TABLES = ("ats_portfolio_snapshots", "ats_risk_reservation_events")


def _schema_and_role() -> tuple[str, str]:
    connection = op.get_bind()
    schema = connection.scalar(sa.text("SELECT current_schema()"))
    role = os.environ.get("ATS_RUNTIME_ROLE")
    if not schema:
        raise RuntimeError("ATS-010 migration requires a current PostgreSQL schema")
    if not role:
        raise RuntimeError("ATS_RUNTIME_ROLE is required for ATS-010 migration")
    preparer = connection.dialect.identifier_preparer
    return preparer.quote(schema), preparer.quote(role)


def upgrade() -> None:
    quoted_schema, quoted_role = _schema_and_role()
    op.create_table(
        "ats_portfolio_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "snapshot_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.UniqueConstraint(
            "account_id", "version", name="uq_ats_portfolio_snapshot_account_version"
        ),
        sa.CheckConstraint("version >= 1", name="ck_ats_portfolio_snapshot_version"),
    )
    op.create_table(
        "ats_risk_reservation_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("snapshot_version", sa.Integer(), nullable=False),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ats_trade_plans.id"),
            nullable=False,
        ),
        sa.Column(
            "event_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "reservation_id", "sequence", name="uq_ats_risk_reservation_sequence"
        ),
        sa.UniqueConstraint(
            "account_id",
            "idempotency_key",
            name="uq_ats_risk_reservation_idempotency",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_ats_risk_reservation_sequence"),
        sa.CheckConstraint(
            "event_type IN ('reserved', 'fill', 'stop_confirmed', 'release')",
            name="ck_ats_risk_reservation_event_type",
        ),
    )
    op.create_index(
        "ix_ats_risk_reservation_account",
        "ats_risk_reservation_events",
        ["account_id", "reservation_id", "sequence"],
    )

    for table_name in TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_append_only
            BEFORE UPDATE OR DELETE ON {quoted_schema}.{table_name}
            FOR EACH ROW EXECUTE FUNCTION
                {quoted_schema}.ats_reject_append_only_mutation()
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_truncate_append_only
            BEFORE TRUNCATE ON {quoted_schema}.{table_name}
            FOR EACH STATEMENT EXECUTE FUNCTION
                {quoted_schema}.ats_reject_append_only_mutation()
            """
        )

    table_list = ", ".join(f"{quoted_schema}.{table}" for table in TABLES)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_list} FROM PUBLIC")
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_list} FROM {quoted_role}")
    op.execute(f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE {table_list} TO {quoted_role}")


def downgrade() -> None:
    quoted_schema, quoted_role = _schema_and_role()
    table_list = ", ".join(f"{quoted_schema}.{table}" for table in TABLES)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_list} FROM {quoted_role}")
    for table_name in TABLES:
        op.execute(
            f"DROP TRIGGER IF EXISTS {table_name}_append_only "
            f"ON {quoted_schema}.{table_name}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS {table_name}_truncate_append_only "
            f"ON {quoted_schema}.{table_name}"
        )
    op.drop_index(
        "ix_ats_risk_reservation_account", table_name="ats_risk_reservation_events"
    )
    op.drop_table("ats_risk_reservation_events")
    op.drop_table("ats_portfolio_snapshots")
