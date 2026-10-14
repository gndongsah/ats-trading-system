import pytest

from migrations.configuration import migration_database_url


def test_migration_database_url_requires_owner_connection():
    with pytest.raises(RuntimeError, match="ATS_MIGRATION_DATABASE_URL is required"):
        migration_database_url(
            {
                "DATABASE_URL": "postgresql+psycopg://runtime@localhost/ats",
            }
        )


def test_migration_database_url_uses_explicit_migration_connection():
    url = "postgresql+psycopg://owner@localhost/ats"
    assert (
        migration_database_url(
            {
                "ATS_MIGRATION_DATABASE_URL": url,
                "DATABASE_URL": "postgresql+psycopg://runtime@localhost/ats",
            }
        )
        == url
    )
