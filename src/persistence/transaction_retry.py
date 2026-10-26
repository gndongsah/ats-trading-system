"""Bounded whole-transaction retry for transient PostgreSQL failures.

Only database work may run inside ``work``. Remote broker calls must never be
placed in a retried scope. Each attempt uses a fresh session and transaction,
and exhaustion fails closed after every attempt has been rolled back.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

# serialization_failure, deadlock_detected, lock_not_available
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01", "55P03"})
MAX_ATTEMPTS_CEILING = 5


class TransactionRetryExhausted(RuntimeError):
    """Every bounded attempt failed with a transient error; nothing was committed."""

    def __init__(self, attempts: int, sqlstate: str) -> None:
        super().__init__(
            f"transaction failed after {attempts} attempts (SQLSTATE {sqlstate})"
        )
        self.attempts = attempts
        self.sqlstate = sqlstate


def _sqlstate(error: DBAPIError) -> str | None:
    original = error.orig
    for name in ("sqlstate", "pgcode"):
        value = getattr(original, name, None)
        if isinstance(value, str):
            return value
    return None


def run_in_transaction[T](
    session_factory: Callable[[], Session],
    work: Callable[[Session], T],
    *,
    max_attempts: int = 3,
) -> T:
    if not 1 <= max_attempts <= MAX_ATTEMPTS_CEILING:
        raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS_CEILING}")
    last_state = "UNKNOWN"
    for _ in range(max_attempts):
        session = session_factory()
        try:
            with session.begin():
                return work(session)
        except DBAPIError as error:
            state = _sqlstate(error)
            if state not in RETRYABLE_SQLSTATES:
                raise
            last_state = state
        finally:
            session.close()
    raise TransactionRetryExhausted(max_attempts, last_state)
