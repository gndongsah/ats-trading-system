"""Add queryable point-in-time clocks to source documents.

Revision ID: 20261026_09
Revises: 20261026_08
"""

import sqlalchemy as sa
from alembic import op

revision = "20261026_09"
down_revision = "20261026_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ats_source_documents",
        sa.Column("source_type", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ats_source_documents",
        sa.Column("subject", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "ats_source_documents",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ats_source_documents",
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ats_source_documents",
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        """
        UPDATE ats_source_documents
        SET source_type = source_data ->> 'source_type',
            subject = source_data ->> 'subject',
            published_at = (source_data ->> 'published_at')::timestamptz,
            available_at = COALESCE(
                source_data ->> 'available_at',
                source_data ->> 'observed_at'
            )::timestamptz,
            ingested_at = COALESCE(
                source_data ->> 'ingested_at',
                source_data ->> 'available_at',
                source_data ->> 'observed_at'
            )::timestamptz
        """
    )

    for column in (
        "source_type",
        "subject",
        "published_at",
        "available_at",
        "ingested_at",
    ):
        op.alter_column("ats_source_documents", column, nullable=False)

    op.create_check_constraint(
        "ck_ats_source_available_clock",
        "ats_source_documents",
        "available_at >= published_at",
    )
    op.create_check_constraint(
        "ck_ats_source_ingested_clock",
        "ats_source_documents",
        "ingested_at >= available_at",
    )
    op.create_index(
        "ix_ats_source_replay",
        "ats_source_documents",
        [
            "source_type",
            "subject",
            "published_at",
            "available_at",
            "ingested_at",
            "source_id",
            "version",
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_ats_source_replay", table_name="ats_source_documents")
    op.drop_constraint(
        "ck_ats_source_ingested_clock",
        "ats_source_documents",
        type_="check",
    )
    op.drop_constraint(
        "ck_ats_source_available_clock",
        "ats_source_documents",
        type_="check",
    )
    for column in (
        "ingested_at",
        "available_at",
        "published_at",
        "subject",
        "source_type",
    ):
        op.drop_column("ats_source_documents", column)
