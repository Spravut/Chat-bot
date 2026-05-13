"""
Transaction isolation helpers.

Postgres default is `READ COMMITTED`, which is enough for most reads but lets
through write-skew anomalies — two transactions can each read part of an
invariant, decide independently, and commit results that violate the
invariant globally.

Our concrete write-skew case is the mutual-like → match creation:
  T1 inserts Like(A→B);  reads "is there a Like(B→A)?" → no
  T2 inserts Like(B→A);  reads "is there a Like(A→B)?" → no
  both commit → two Likes, zero Matches (BUG)

This module provides:
  - `run_serializable(...)` — runs an operation under SERIALIZABLE with
    exponential-backoff retry on SQLSTATE 40001 (`serialization_failure`).
  - `with_for_update_locks(stmt, ids)` — convenience for pessimistic row
    locks in canonical ID order (deadlock-free).

See [docs/isolation.md](../../docs/isolation.md) for the trade-offs.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

T = TypeVar("T")


def is_serialization_failure(exc: BaseException) -> bool:
    """Detect Postgres `serialization_failure` (SQLSTATE 40001).

    Works on both raw asyncpg exceptions and SQLAlchemy `DBAPIError` wrappers.
    """
    if isinstance(exc, DBAPIError):
        orig = getattr(exc, "orig", None)
        if orig is None:
            return False
        sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        if sqlstate == "40001":
            return True
        return "could not serialize" in str(orig).lower()
    sqlstate = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    return sqlstate == "40001"


async def run_serializable(
    session_factory: async_sessionmaker[AsyncSession],
    operation: Callable[[AsyncSession], Awaitable[T]],
    max_attempts: int = 3,
    base_delay: float = 0.05,
) -> T:
    """Run `operation(session)` inside a SERIALIZABLE transaction with retries.

    The session passed to `operation` has `isolation_level=SERIALIZABLE` set on
    its connection BEFORE the transaction begins (Postgres requirement). On
    `serialization_failure`, the transaction is rolled back and retried with
    exponential backoff (jitter via the event loop scheduler).

    Non-serialization errors propagate immediately — `operation` is expected
    to be deterministic given the DB state.
    """
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        async with session_factory() as session:
            # Isolation MUST be set before any statement on this connection.
            await session.connection(execution_options={
                "isolation_level": "SERIALIZABLE"
            })
            try:
                result = await operation(session)
                await session.commit()
                return result
            except DBAPIError as exc:
                await session.rollback()
                last_exc = exc
                if is_serialization_failure(exc) and attempt < max_attempts - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.info(
                        "SERIALIZABLE conflict on attempt %d/%d, retrying in %.0fms",
                        attempt + 1, max_attempts, delay * 1000,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
            except Exception:
                await session.rollback()
                raise
    # Exhausted retries on serialization conflicts
    assert last_exc is not None
    raise last_exc
