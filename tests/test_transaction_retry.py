import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from src.persistence.transaction_retry import (
    TransactionRetryExhausted,
    run_in_transaction,
)


class _Orig(Exception):
    def __init__(self, sqlstate):
        super().__init__("synthetic")
        self.sqlstate = sqlstate


def _error(sqlstate, cls=DBAPIError):
    return cls("stmt", {}, _Orig(sqlstate))


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("create table t (k text primary key)"))
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def _count(factory):
    with factory() as session:
        return session.execute(text("select count(*) from t")).scalar_one()


@pytest.mark.parametrize("state", ["40001", "40P01", "55P03"])
def test_transient_error_is_retried_with_same_key_and_no_partial_rows(factory, state):
    keys = []

    def work(session):
        keys.append("idem-1")
        session.execute(text("insert into t values ('idem-1')"))
        if len(keys) < 3:
            raise _error(state)
        return "ok"

    assert run_in_transaction(factory, work) == "ok"
    assert keys == ["idem-1"] * 3
    assert _count(factory) == 1


def test_exhaustion_fails_closed_and_rolls_back_every_attempt(factory):
    calls = []

    def work(session):
        calls.append(1)
        session.execute(text("insert into t values ('x')"))
        raise _error("40001")

    with pytest.raises(TransactionRetryExhausted) as info:
        run_in_transaction(factory, work, max_attempts=3)
    assert len(calls) == 3
    assert info.value.sqlstate == "40001"
    assert _count(factory) == 0


def test_non_transient_error_is_not_retried(factory):
    calls = []

    def work(session):
        calls.append(1)
        raise _error("23505", IntegrityError)

    with pytest.raises(IntegrityError):
        run_in_transaction(factory, work)
    assert len(calls) == 1


def test_unknown_sqlstate_is_not_retried(factory):
    calls = []

    def work(session):
        calls.append(1)
        raise DBAPIError("stmt", {}, Exception("no state"))

    with pytest.raises(DBAPIError):
        run_in_transaction(factory, work)
    assert len(calls) == 1


@pytest.mark.parametrize("attempts", [0, 6, -1])
def test_attempt_bound_is_enforced(factory, attempts):
    with pytest.raises(ValueError):
        run_in_transaction(factory, lambda s: None, max_attempts=attempts)
