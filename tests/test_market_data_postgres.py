from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from src.market_data import MarketDataStore
from src.persistence.models import MarketDataRecord
from tests import test_postgres_persistence as pg
from tests.test_market_data import EVENTS, T, requirements

migrated_postgres_database = pg.migrated_postgres_database
migrated_postgres_engine = pg.migrated_postgres_engine


def test_postgres_yesterday_unchanged_after_today_correction(migrated_postgres_engine):
    factory = sessionmaker(bind=migrated_postgres_engine)
    with factory.begin() as session:
        store = MarketDataStore(session, clock=lambda: T)
        for event in EVENTS[:2]:
            store.ingest(event)
        before = store.replay(as_of=T, requirements=requirements())
    with factory.begin() as session:
        MarketDataStore(session, clock=lambda: T + timedelta(days=1)).ingest(EVENTS[2])
    with factory() as session:
        after = MarketDataStore(session).replay(as_of=T, requirements=requirements())
        assert before == after and before.digest == after.digest


def test_concurrent_duplicate_delivery_keeps_one_revision(migrated_postgres_engine):
    factory = sessionmaker(bind=migrated_postgres_engine)

    def deliver(_):
        with factory.begin() as session:
            return MarketDataStore(session, clock=lambda: T).ingest(EVENTS[1])

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(deliver, range(3)))
    assert results[0] == results[1] == results[2]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(MarketDataRecord)) == 1


@pytest.mark.parametrize(
    "action",
    [
        "UPDATE ats_market_data SET revision = 9",
        "DELETE FROM ats_market_data",
        "TRUNCATE ats_market_data",
    ],
)
def test_append_only_guards_and_runtime_privileges(migrated_postgres_database, action):
    engine, role = migrated_postgres_database
    with engine.begin() as con:
        from sqlalchemy.orm import Session

        with Session(bind=con) as session, session.begin():
            MarketDataStore(session, clock=lambda: T).ingest(EVENTS[1])
    # Owner is stopped by triggers; runtime is limited to SELECT/INSERT.
    for runtime in (False, True):
        with engine.connect() as con, con.begin():
            if runtime:
                con.execute(text(f'SET LOCAL ROLE "{role}"'))
            with pytest.raises(DBAPIError):
                con.execute(text(action))
            con.rollback()
