"""Add append-only ATS-009 loss-state evidence and events.

Revision ID: 20261020_05
Revises: 20261016_04
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261020_05"
down_revision = "20261016_04"
branch_labels = None
depends_on = None

TABLES = (
    "ats_loss_valuation_observations",
    "ats_loss_external_flows",
    "ats_loss_period_openings",
    "ats_loss_state_events",
)


def _schema_and_role() -> tuple[str, str]:
    connection = op.get_bind()
    schema = connection.scalar(sa.text("SELECT current_schema()"))
    role = os.environ.get("ATS_RUNTIME_ROLE")
    if not schema:
        raise RuntimeError("ATS-009 migration requires a current PostgreSQL schema")
    if not role:
        raise RuntimeError("ATS_RUNTIME_ROLE is required for ATS-009 migration")
    preparer = connection.dialect.identifier_preparer
    return preparer.quote(schema), preparer.quote(role)


def _append_only(table_name: str, quoted_schema: str) -> None:
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


def upgrade() -> None:
    quoted_schema, quoted_role = _schema_and_role()
    uuid = postgresql.UUID(as_uuid=True)
    numeric = sa.Numeric()
    timestamp = sa.DateTime(timezone=True)

    op.create_table(
        "ats_loss_valuation_observations",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("account_id", uuid, nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("source_epoch", sa.String(128), nullable=False),
        sa.Column("source_sequence", sa.Integer(), nullable=False),
        sa.Column("as_of", timestamp, nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("net_liquidation", numeric, nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("reconciled", sa.Boolean(), nullable=False),
        sa.Column("provenance_ref", sa.String(256), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column(
            "correction_of_id",
            uuid,
            sa.ForeignKey("ats_loss_valuation_observations.id"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "account_id",
            "source_id",
            "source_epoch",
            "source_sequence",
            name="uq_ats_loss_valuation_source_sequence",
        ),
        sa.CheckConstraint(
            "source_sequence > 0", name="ck_ats_loss_valuation_sequence"
        ),
    )
    op.create_index(
        "ix_ats_loss_valuation_account_as_of",
        "ats_loss_valuation_observations",
        ["account_id", "as_of", "source_sequence"],
    )

    op.create_table(
        "ats_loss_external_flows",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("account_id", uuid, nullable=False),
        sa.Column("flow_id", sa.String(128), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("source_epoch", sa.String(128), nullable=False),
        sa.Column("source_sequence", sa.Integer(), nullable=False),
        sa.Column("occurred_at", timestamp, nullable=False),
        sa.Column("flow_kind", sa.String(16), nullable=False),
        sa.Column("signed_amount", numeric, nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("provenance_ref", sa.String(256), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column(
            "correction_of_id",
            uuid,
            sa.ForeignKey("ats_loss_external_flows.id"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "account_id",
            "source_id",
            "source_epoch",
            "source_sequence",
            name="uq_ats_loss_flow_source_sequence",
        ),
        sa.UniqueConstraint(
            "account_id",
            "source_id",
            "source_epoch",
            "flow_id",
            name="uq_ats_loss_flow_identity",
        ),
        sa.CheckConstraint("source_sequence > 0", name="ck_ats_loss_flow_sequence"),
        sa.CheckConstraint(
            "flow_kind IN ('CONTRIBUTION', 'WITHDRAWAL')",
            name="ck_ats_loss_flow_kind",
        ),
        sa.CheckConstraint(
            "(flow_kind = 'CONTRIBUTION' AND signed_amount > 0) OR "
            "(flow_kind = 'WITHDRAWAL' AND signed_amount < 0)",
            name="ck_ats_loss_flow_sign",
        ),
    )
    op.create_index(
        "ix_ats_loss_flow_account_time",
        "ats_loss_external_flows",
        ["account_id", "occurred_at", "source_sequence"],
    )

    op.create_table(
        "ats_loss_period_openings",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("account_id", uuid, nullable=False),
        sa.Column("period_kind", sa.String(8), nullable=False),
        sa.Column("period_id", sa.String(128), nullable=False),
        sa.Column("calendar_version", sa.String(128), nullable=False),
        sa.Column("opened_at", timestamp, nullable=False),
        sa.Column("opening_equity", numeric, nullable=False),
        sa.Column("opening_nlv", numeric, nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("source_epoch", sa.String(128), nullable=False),
        sa.Column("source_sequence", sa.Integer(), nullable=False),
        sa.Column("immutable_version", sa.Integer(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("reconciled", sa.Boolean(), nullable=False),
        sa.Column("provenance_ref", sa.String(256), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "account_id",
            "period_kind",
            "period_id",
            name="uq_ats_loss_opening_period",
        ),
        sa.CheckConstraint(
            "period_kind IN ('DAILY', 'WEEKLY')", name="ck_ats_loss_opening_kind"
        ),
        sa.CheckConstraint(
            "immutable_version >= 1", name="ck_ats_loss_opening_version"
        ),
        sa.CheckConstraint("source_sequence > 0", name="ck_ats_loss_opening_sequence"),
        sa.CheckConstraint("opening_equity > 0", name="ck_ats_loss_opening_equity"),
    )
    op.create_index(
        "ix_ats_loss_opening_account_period",
        "ats_loss_period_openings",
        ["account_id", "period_kind", "period_id"],
    )

    op.create_table(
        "ats_loss_state_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("account_id", uuid, nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("event_key", sa.String(256), nullable=False),
        sa.Column("daily_period_id", sa.String(128), nullable=False),
        sa.Column("weekly_period_id", sa.String(128), nullable=False),
        sa.Column("daily_locked", sa.Boolean(), nullable=False),
        sa.Column("weekly_locked", sa.Boolean(), nullable=False),
        sa.Column(
            "valuation_observation_id",
            uuid,
            sa.ForeignKey("ats_loss_valuation_observations.id"),
            nullable=False,
        ),
        sa.Column(
            "daily_opening_id",
            uuid,
            sa.ForeignKey("ats_loss_period_openings.id"),
            nullable=False,
        ),
        sa.Column(
            "weekly_opening_id",
            uuid,
            sa.ForeignKey("ats_loss_period_openings.id"),
            nullable=False,
        ),
        sa.Column("daily_pnl_numerator", sa.String(4096), nullable=False),
        sa.Column("daily_pnl_denominator", sa.String(4096), nullable=False),
        sa.Column("weekly_pnl_numerator", sa.String(4096), nullable=False),
        sa.Column("weekly_pnl_denominator", sa.String(4096), nullable=False),
        sa.Column("daily_r_numerator", sa.String(4096), nullable=False),
        sa.Column("daily_r_denominator", sa.String(4096), nullable=False),
        sa.Column("weekly_r_numerator", sa.String(4096), nullable=False),
        sa.Column("weekly_r_denominator", sa.String(4096), nullable=False),
        sa.Column(
            "flow_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("correction_of_state_version", sa.Integer(), nullable=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.UniqueConstraint(
            "account_id",
            "state_version",
            name="uq_ats_loss_state_account_version",
        ),
        sa.UniqueConstraint(
            "account_id",
            "event_key",
            name="uq_ats_loss_state_event_key",
        ),
        sa.CheckConstraint("state_version >= 1", name="ck_ats_loss_state_version"),
        sa.CheckConstraint(
            "event_type IN ('OBSERVED', 'LOCKED', 'CORRECTION')",
            name="ck_ats_loss_state_event_type",
        ),
    )
    op.create_index(
        "ix_ats_loss_state_account_version",
        "ats_loss_state_events",
        ["account_id", "state_version"],
    )

    for table_name in TABLES:
        _append_only(table_name, quoted_schema)

    table_list = ", ".join(f"{quoted_schema}.{table}" for table in TABLES)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_list} FROM PUBLIC")
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_list} FROM {quoted_role}")
    op.execute(f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE {table_list} TO {quoted_role}")


def downgrade() -> None:
    quoted_schema, quoted_role = _schema_and_role()
    table_list = ", ".join(f"{quoted_schema}.{table}" for table in TABLES)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_list} FROM {quoted_role}")
    for table_name in reversed(TABLES):
        op.execute(
            f"DROP TRIGGER IF EXISTS {table_name}_append_only "
            f"ON {quoted_schema}.{table_name}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS {table_name}_truncate_append_only "
            f"ON {quoted_schema}.{table_name}"
        )
        op.drop_table(table_name)
