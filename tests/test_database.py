from sqlalchemy import text
from sqlalchemy.orm import Session

from src.database import create_database_engine


def test_database_engine_supports_sqlite_sessions():
    engine = create_database_engine("sqlite://")
    try:
        with Session(engine) as session:
            assert session.scalar(text("SELECT 1")) == 1
    finally:
        engine.dispose()
