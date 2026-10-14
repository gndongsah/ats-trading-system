from collections.abc import Mapping


def migration_database_url(environment: Mapping[str, str]) -> str:
    url = environment.get("ATS_MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "ATS_MIGRATION_DATABASE_URL is required for Alembic migrations"
        )
    return url
