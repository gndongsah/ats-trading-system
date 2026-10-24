import os
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from src.database import create_database_engine


def test_sqlite_engine_is_unaffected_by_utc_session_hook():
    engine = create_database_engine("sqlite://")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
    engine.dispose()


def test_postgres_sessions_are_utc_even_when_the_server_zone_is_not():
    database_url = os.getenv("ATS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set ATS_TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    if make_url(database_url).database != "ats_test":
        raise RuntimeError(
            "ATS_TEST_DATABASE_URL must target the dedicated ats_test database"
        )

    # Simulate a server configured for a non-UTC zone (e.g. a local install in
    # US Central time). Without the UTC session hook, timestamptz values come
    # back with a -05:00/-06:00 offset and the domain models reject them.
    non_utc_url = make_url(database_url).update_query_dict(
        {"options": "-ctimezone=America/Chicago"}
    )
    engine = create_database_engine(non_utc_url.render_as_string(hide_password=False))
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SHOW TIME ZONE")).scalar_one() == "UTC"
            value = connection.execute(
                text("SELECT TIMESTAMPTZ '2026-10-23 15:30:00-05'")
            ).scalar_one()
        assert value.utcoffset() == timedelta(0)
        assert (value.hour, value.minute) == (20, 30)
    finally:
        engine.dispose()
