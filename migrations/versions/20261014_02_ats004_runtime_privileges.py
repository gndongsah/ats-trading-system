"""Restrict ATS-004 journal privileges to a separate runtime role.

Revision ID: 20261014_02
Revises: 20261014_01
"""

import os

import sqlalchemy as sa
from alembic import op

revision = "20261014_02"
down_revision = "20261014_01"
branch_labels = None
depends_on = None

TABLES = (
    "ats_trades",
    "ats_score_snapshots",
    "ats_trade_plans",
    "ats_order_intents",
    "ats_fills",
    "ats_position_snapshots",
    "ats_trade_results",
    "ats_journal_entries",
)


def _schema_and_role() -> tuple[str, str]:
    connection = op.get_bind()
    schema_name = connection.scalar(sa.text("SELECT current_schema()"))
    if schema_name is None:
        raise RuntimeError("ATS-004 migration requires a current PostgreSQL schema")
    runtime_role = os.environ.get("ATS_RUNTIME_ROLE")
    if not runtime_role:
        raise RuntimeError(
            "ATS_RUNTIME_ROLE is required to keep application and migration roles separate"
        )
    return (
        connection.dialect.identifier_preparer.quote(schema_name),
        connection.dialect.identifier_preparer.quote(runtime_role),
    )


def upgrade() -> None:
    connection = op.get_bind()
    schema_name, quoted_role = _schema_and_role()
    role_name = os.environ["ATS_RUNTIME_ROLE"]
    migration_role = connection.dialect.identifier_preparer.quote(
        connection.scalar(sa.text("SELECT current_user"))
    )
    table_list = ", ".join(f"{schema_name}.{table}" for table in TABLES)

    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_list} FROM PUBLIC")
    op.execute(f"REVOKE CREATE ON SCHEMA {schema_name} FROM PUBLIC")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {migration_role} "
        f"IN SCHEMA {schema_name} REVOKE ALL ON TABLES FROM PUBLIC"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {migration_role} "
        f"IN SCHEMA {schema_name} REVOKE ALL ON FUNCTIONS FROM PUBLIC"
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION "
        f"{schema_name}.ats_reject_append_only_mutation() FROM PUBLIC"
    )

    role_is_superuser = connection.scalar(
        sa.text("SELECT rolsuper FROM pg_roles WHERE rolname = :role"),
        {"role": role_name},
    )
    if role_is_superuser is None:
        raise RuntimeError(
            f"ATS_RUNTIME_ROLE {role_name!r} must exist before migration"
        )
    if role_is_superuser:
        raise RuntimeError("ATS_RUNTIME_ROLE must not be a PostgreSQL superuser")
    if role_name == connection.scalar(sa.text("SELECT current_user")):
        raise RuntimeError("ATS_RUNTIME_ROLE must differ from the migration role")
    schema_name_for_check = connection.scalar(sa.text("SELECT current_schema()"))
    schema_owner = connection.scalar(
        sa.text(
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = :schema"
        ),
        {"schema": schema_name_for_check},
    )
    runtime_owns_table = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relname = ANY(:tables) "
            "AND c.relkind IN ('r', 'p') AND c.relowner = "
            "(SELECT oid FROM pg_roles WHERE rolname = :role))"
        ),
        {
            "schema": schema_name_for_check,
            "tables": list(TABLES),
            "role": role_name,
        },
    )
    if schema_owner == role_name or runtime_owns_table:
        raise RuntimeError("ATS_RUNTIME_ROLE must not own the ATS schema or tables")
    owner_roles = connection.scalars(
        sa.text(
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace "
            "WHERE nspname = :schema "
            "UNION SELECT DISTINCT pg_get_userbyid(c.relowner) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relname = ANY(:tables) "
            "AND c.relkind IN ('r', 'p')"
        ),
        {"schema": schema_name_for_check, "tables": list(TABLES)},
    ).all()
    if any(
        connection.scalar(
            sa.text("SELECT pg_has_role(:member, :owner, 'MEMBER')"),
            {"member": role_name, "owner": owner_role},
        )
        for owner_role in owner_roles
    ):
        raise RuntimeError(
            "ATS_RUNTIME_ROLE must not inherit or assume an ATS schema/table owner role"
        )

    op.execute(
        f"REVOKE ALL PRIVILEGES ON TABLE {table_list} FROM {quoted_role} CASCADE"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA {schema_name} FROM {quoted_role}")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION "
        f"{schema_name}.ats_reject_append_only_mutation() FROM {quoted_role}"
    )
    op.execute(f"GRANT USAGE ON SCHEMA {schema_name} TO {quoted_role}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE {table_list} TO {quoted_role}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {migration_role} "
        f"IN SCHEMA {schema_name} GRANT SELECT, INSERT ON TABLES TO {quoted_role}"
    )


def downgrade() -> None:
    quoted_schema, quoted_role = _schema_and_role()
    connection = op.get_bind()
    table_list = ", ".join(f"{quoted_schema}.{table}" for table in TABLES)
    op.execute(f"REVOKE SELECT, INSERT ON TABLE {table_list} FROM {quoted_role}")
    op.execute(f"REVOKE USAGE ON SCHEMA {quoted_schema} FROM {quoted_role}")
    migration_role = connection.dialect.identifier_preparer.quote(
        connection.scalar(sa.text("SELECT current_user"))
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {migration_role} "
        f"IN SCHEMA {quoted_schema} REVOKE SELECT, INSERT ON TABLES FROM {quoted_role}"
    )
