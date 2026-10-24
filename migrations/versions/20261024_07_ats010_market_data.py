"""Append-only point-in-time market data revisions.

Revision ID: 20261024_07
Revises: 20261020_06
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261024_07"
down_revision = "20261020_06"
branch_labels = None
depends_on = None


def _names():
    connection = op.get_bind()
    schema = connection.scalar(sa.text("SELECT current_schema()"))
    role = os.environ.get("ATS_RUNTIME_ROLE")
    if not schema or not role:
        raise RuntimeError(
            "market migration requires current schema and ATS_RUNTIME_ROLE"
        )
    quote = connection.dialect.identifier_preparer.quote
    return quote(schema), quote(role)


def upgrade():
    schema, role = _names()
    op.create_table(
        "ats_market_data",
        sa.Column("source", sa.String(128), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("stream", sa.String(32), primary_key=True),
        sa.Column("event_time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_data", JSONB(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_ats_market_revision"),
        sa.CheckConstraint("ingested_at >= available_at", name="ck_ats_market_clock"),
    )
    op.create_index(
        "ix_ats_market_replay",
        "ats_market_data",
        ["source", "symbol", "stream", "event_time", "available_at", "ingested_at"],
    )
    for suffix, clause, granularity in (
        ("append_only", "UPDATE OR DELETE", "ROW"),
        ("truncate_append_only", "TRUNCATE", "STATEMENT"),
    ):
        op.execute(
            f"CREATE TRIGGER ats_market_data_{suffix} BEFORE {clause} "
            f"ON {schema}.ats_market_data FOR EACH {granularity} "
            f"EXECUTE FUNCTION {schema}.ats_reject_append_only_mutation()"
        )
    op.execute(f"REVOKE ALL PRIVILEGES ON {schema}.ats_market_data FROM PUBLIC")
    op.execute(f"REVOKE ALL PRIVILEGES ON {schema}.ats_market_data FROM {role}")
    op.execute(f"GRANT SELECT, INSERT ON {schema}.ats_market_data TO {role}")


def downgrade():
    op.drop_index("ix_ats_market_replay", table_name="ats_market_data")
    op.drop_table("ats_market_data")
