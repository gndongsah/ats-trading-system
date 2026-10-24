import os
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def create_database_engine(database_url: str) -> Engine:
    engine_options = (
        {"connect_args": {"check_same_thread": False}}
        if database_url.startswith("sqlite")
        else {}
    )
    engine = create_engine(database_url, pool_pre_ping=True, **engine_options)
    if database_url.startswith("sqlite"):
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    install_utc_session_timezone(engine)
    return engine


def install_utc_session_timezone(engine: Engine) -> Engine:
    """Pin every PostgreSQL session on ``engine`` to UTC.

    Domain models reject timestamps that are not UTC. PostgreSQL returns
    ``timestamptz`` values in the session time zone, which otherwise defaults to
    the server's configured zone, so correctness would depend on server setup.
    Non-PostgreSQL engines are returned unchanged.
    """
    if engine.dialect.name == "postgresql" and not event.contains(
        engine, "connect", _set_utc_session_timezone
    ):
        event.listen(engine, "connect", _set_utc_session_timezone)
    return engine


def _set_utc_session_timezone(connection, _record) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("SET TIME ZONE 'UTC'")
    finally:
        cursor.close()
    if not connection.autocommit:
        connection.commit()


def _enable_sqlite_foreign_keys(connection, _record) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ats.db")
engine = create_database_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
