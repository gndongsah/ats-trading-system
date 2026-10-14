"""Add versioned, append-only ATS-005 evidence records.

Revision ID: 20261014_03
Revises: 20261014_02
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261014_03"
down_revision = "20261014_02"
branch_labels = None
depends_on = None

TABLES = ("ats_source_documents", "ats_evidence_items")


def _schema_and_runtime_role() -> tuple[str, str]:
    connection = op.get_bind()
    schema_name = connection.scalar(sa.text("SELECT current_schema()"))
    runtime_role = os.environ.get("ATS_RUNTIME_ROLE")
    if schema_name is None:
        raise RuntimeError("ATS-005 migration requires a current PostgreSQL schema")
    if not runtime_role:
        raise RuntimeError("ATS_RUNTIME_ROLE is required for ATS-005 migration")
    role = connection.execute(
        sa.text("SELECT rolsuper FROM pg_roles WHERE rolname = :role"),
        {"role": runtime_role},
    ).scalar_one_or_none()
    if role is None or role:
        raise RuntimeError("ATS_RUNTIME_ROLE must exist and must not be a superuser")
    if runtime_role == connection.scalar(sa.text("SELECT current_user")):
        raise RuntimeError("ATS_RUNTIME_ROLE must differ from the migration role")
    migration_owner = connection.scalar(sa.text("SELECT current_user"))
    if connection.scalar(
        sa.text("SELECT pg_has_role(:member, :owner, 'MEMBER')"),
        {"member": runtime_role, "owner": migration_owner},
    ):
        raise RuntimeError(
            "ATS_RUNTIME_ROLE must not inherit or assume the migration-owner role"
        )
    schema_owner = connection.scalar(
        sa.text(
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = :schema"
        ),
        {"schema": schema_name},
    )
    if schema_owner == runtime_role:
        raise RuntimeError("ATS_RUNTIME_ROLE must not own the ATS schema")
    is_schema_owner_member = connection.scalar(
        sa.text("SELECT pg_has_role(:member, :owner, 'MEMBER')"),
        {"member": runtime_role, "owner": schema_owner},
    )
    if is_schema_owner_member:
        raise RuntimeError(
            "ATS_RUNTIME_ROLE must not inherit or assume the schema-owner role"
        )
    preparer = connection.dialect.identifier_preparer
    return preparer.quote(schema_name), preparer.quote(runtime_role)


def upgrade() -> None:
    quoted_schema, quoted_role = _schema_and_runtime_role()
    op.create_table(
        "ats_source_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "source_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.UniqueConstraint(
            "source_id", "version", name="uq_ats_source_document_version"
        ),
        sa.CheckConstraint(
            "version >= 1", name="ck_ats_source_document_version_positive"
        ),
    )
    op.create_table(
        "ats_evidence_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column(
            "source_document_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "evidence_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["source_document_version_id"],
            [f"{quoted_schema}.ats_source_documents.id"],
            name="fk_ats_evidence_source_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_ats_evidence_version_positive"),
    )
    op.execute(
        f"""
        CREATE FUNCTION {quoted_schema}.ats_reject_evidence_mutation()
        RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
                'ATS evidence records are append-only: % on % is forbidden',
                TG_OP, TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table_name in TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_append_only
            BEFORE UPDATE OR DELETE ON {quoted_schema}.{table_name}
            FOR EACH ROW EXECUTE FUNCTION
                {quoted_schema}.ats_reject_evidence_mutation()
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_truncate_append_only
            BEFORE TRUNCATE ON {quoted_schema}.{table_name}
            FOR EACH STATEMENT EXECUTE FUNCTION
                {quoted_schema}.ats_reject_evidence_mutation()
            """
        )
    table_list = ", ".join(f"{quoted_schema}.{table}" for table in TABLES)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_list} FROM PUBLIC")
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_list} FROM {quoted_role}")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION "
        f"{quoted_schema}.ats_reject_evidence_mutation() FROM PUBLIC"
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION "
        f"{quoted_schema}.ats_reject_evidence_mutation() FROM {quoted_role}"
    )
    op.execute(f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE {table_list} TO {quoted_role}")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION "
        f"{quoted_schema}.ats_reject_append_only_mutation() FROM {quoted_role}"
    )


def downgrade() -> None:
    quoted_schema, quoted_role = _schema_and_runtime_role()
    tables = ", ".join(f"{quoted_schema}.{table}" for table in TABLES)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {tables} FROM {quoted_role}")
    for table_name in reversed(TABLES):
        op.execute(
            f"DROP TRIGGER IF EXISTS {table_name}_append_only "
            f"ON {quoted_schema}.{table_name}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS {table_name}_truncate_append_only "
            f"ON {quoted_schema}.{table_name}"
        )
    op.execute(
        f"DROP FUNCTION IF EXISTS {quoted_schema}.ats_reject_evidence_mutation()"
    )
    op.drop_table("ats_evidence_items")
    op.drop_table("ats_source_documents")
