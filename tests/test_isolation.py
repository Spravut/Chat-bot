"""
Integration tests that demonstrate transaction isolation behaviour against a
real PostgreSQL instance.

Why a separate file and not in the SQLite-backed unit tests:
  - The whole point is to show Postgres-specific isolation behaviour
    (SERIALIZABLE, SQLSTATE 40001). SQLite has only one writer and serializes
    everything implicitly — these tests would not exercise anything there.
  - Skip-gated by env var `INTEGRATION_PG_URL`. Default unit-test run on
    SQLite skips these.

How to run:
  # 1. Bring up the docker stack (or any local Postgres)
  docker-compose up -d postgres
  # 2. Point pytest at it
  $env:INTEGRATION_PG_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/dating_bot"
  python -m pytest tests/test_isolation.py -v

What each test proves:
  - `test_mutual_like_race_under_read_committed` — the classic write-skew
    anomaly: two concurrent "I like you" transactions under READ COMMITTED
    each miss the other's Like and neither creates a Match.
  - `test_mutual_like_serializable_creates_match` — under SERIALIZABLE the
    same race is detected and one transaction retries, producing exactly
    one Match.
"""
from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

PG_URL = os.environ.get("INTEGRATION_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="Set INTEGRATION_PG_URL to a Postgres async URL to run isolation tests",
)


# Models are imported lazily inside fixtures so that this module can be
# collected even when conftest.py applied SQLite shims that would conflict.
@pytest_asyncio.fixture
async def pg_engine():
    engine = create_async_engine(PG_URL, future=True, pool_pre_ping=True)
    from bot.db.models import Base
    async with engine.begin() as conn:
        # Tests run against the existing schema — this is a no-op if migrations
        # already created tables. The shim in conftest.py rewrites JSONB→JSON
        # on the model, but the on-disk schema is whatever migrations made.
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_factory(pg_engine):
    return async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def two_users(pg_factory):
    """Create two clean users for the race; clean up afterwards.

    Telegram IDs are chosen in a "reserved" range (-9000…) so they don't
    collide with real users in a shared dev database.
    """
    from bot.db.models import Like, Match, RatingEvent, User

    tg_a, tg_b = -9001, -9002
    async with pg_factory() as s:
        # Cleanup any leftovers from prior runs
        existing = (await s.scalars(
            select(User).where(User.telegram_id.in_([tg_a, tg_b]))
        )).all()
        for u in existing:
            await s.execute(delete(Match).where(
                (Match.user_a_id == u.id) | (Match.user_b_id == u.id)
            ))
            await s.execute(delete(Like).where(
                (Like.from_user_id == u.id) | (Like.to_user_id == u.id)
            ))
            await s.execute(delete(RatingEvent).where(RatingEvent.user_id == u.id))
            await s.delete(u)
        await s.commit()

        a = User(telegram_id=tg_a, username="alice_test")
        b = User(telegram_id=tg_b, username="bob_test")
        s.add_all([a, b])
        await s.commit()
        a_id, b_id = a.id, b.id

    yield a_id, b_id

    async with pg_factory() as s:
        await s.execute(delete(Match).where(
            (Match.user_a_id.in_([a_id, b_id])) | (Match.user_b_id.in_([a_id, b_id]))
        ))
        await s.execute(delete(Like).where(
            (Like.from_user_id.in_([a_id, b_id])) | (Like.to_user_id.in_([a_id, b_id]))
        ))
        await s.execute(delete(RatingEvent).where(RatingEvent.user_id.in_([a_id, b_id])))
        await s.execute(delete(User).where(User.id.in_([a_id, b_id])))
        await s.commit()


# ── Demo 1: race UNDER READ COMMITTED produces inconsistent state ──────────────

async def test_mutual_like_race_under_read_committed(pg_factory, two_users):
    """Two concurrent transactions each insert their Like and neither sees
    the other's — both commit without creating a Match. This is the write-
    skew anomaly that Postgres' default READ COMMITTED level permits.
    """
    from bot.db.models import Like, Match
    a_id, b_id = two_users
    barrier = asyncio.Barrier(2)

    async def like_under_read_committed(actor_id: int, target_id: int) -> None:
        async with pg_factory() as session:
            # READ COMMITTED is the Postgres default — explicit for clarity.
            await session.connection(execution_options={
                "isolation_level": "READ COMMITTED"
            })
            session.add(Like(from_user_id=actor_id, to_user_id=target_id))
            await session.flush()
            # Both transactions reach here before either commits.
            await barrier.wait()
            mutual = await session.scalar(
                select(Like).where(
                    Like.from_user_id == target_id,
                    Like.to_user_id == actor_id,
                )
            )
            assert mutual is None, (
                "Under READ COMMITTED, neither transaction should see "
                "the other's uncommitted Like — this is the precondition "
                "for the race."
            )
            if mutual:
                # Dead branch under RC — included to show the buggy logic
                # WOULD create a match if it could see the other.
                a, b = sorted([actor_id, target_id])
                session.add(Match(user_a_id=a, user_b_id=b))
                await session.flush()
            await session.commit()

    await asyncio.gather(
        like_under_read_committed(a_id, b_id),
        like_under_read_committed(b_id, a_id),
    )

    async with pg_factory() as s:
        likes = (await s.scalars(select(Like).where(
            Like.from_user_id.in_([a_id, b_id])
        ))).all()
        matches = (await s.scalars(select(Match).where(
            (Match.user_a_id.in_([a_id, b_id])) | (Match.user_b_id.in_([a_id, b_id]))
        ))).all()

    assert len(likes) == 2, "both Likes were committed"
    assert len(matches) == 0, (
        "BUG MANIFESTED: two mutual likes exist but no Match was created — "
        "this is exactly the write-skew anomaly READ COMMITTED permits."
    )


# ── Demo 2: SERIALIZABLE + retry produces correct state ────────────────────────

async def test_mutual_like_serializable_creates_match(pg_factory, two_users):
    """Same concurrent likes, but each transaction runs under SERIALIZABLE.
    Postgres detects the read-write conflict, aborts one with SQLSTATE 40001,
    our `run_serializable` helper retries; the retry sees the other's Like
    and creates the Match. Exactly ONE Match exists at the end.
    """
    from bot.db.models import Like, Match, RatingEvent
    from bot.services.isolation import run_serializable

    a_id, b_id = two_users
    barrier = asyncio.Barrier(2)

    async def operation(actor_id: int, target_id: int):
        async def op(session: AsyncSession) -> tuple[bool, bool]:
            # Inline the production critical section so the test exercises the
            # exact code path. Duplicating slightly to avoid the barrier
            # leaking into bot.handlers.
            if await session.scalar(
                select(Like).where(
                    Like.from_user_id == actor_id, Like.to_user_id == target_id,
                )
            ):
                return False, False
            session.add(Like(from_user_id=actor_id, to_user_id=target_id))
            session.add(RatingEvent(
                user_id=target_id, event_type="like_received",
                target_user_id=actor_id,
            ))
            await session.flush()
            # Force both txns to be in flight simultaneously on the FIRST attempt.
            # The barrier doesn't block retries — `barrier.wait()` after both
            # already passed once returns immediately on subsequent calls.
            try:
                async with asyncio.timeout(2.0):
                    await barrier.wait()
            except (asyncio.TimeoutError, asyncio.BrokenBarrierError):
                pass
            mutual = await session.scalar(
                select(Like).where(
                    Like.from_user_id == target_id, Like.to_user_id == actor_id,
                )
            )
            if not mutual:
                return True, False
            a, b = sorted([actor_id, target_id])
            existing = await session.scalar(
                select(Match).where(Match.user_a_id == a, Match.user_b_id == b)
            )
            if existing:
                return True, False
            session.add(Match(user_a_id=a, user_b_id=b))
            await session.flush()
            return True, True

        return await run_serializable(pg_factory, op, max_attempts=5)

    results = await asyncio.gather(
        operation(a_id, b_id),
        operation(b_id, a_id),
    )

    async with pg_factory() as s:
        likes = (await s.scalars(select(Like).where(
            Like.from_user_id.in_([a_id, b_id])
        ))).all()
        matches = (await s.scalars(select(Match).where(
            (Match.user_a_id.in_([a_id, b_id])) | (Match.user_b_id.in_([a_id, b_id]))
        ))).all()

    assert len(likes) == 2
    assert len(matches) == 1, (
        "Under SERIALIZABLE + retry, exactly one Match must exist. "
        f"Got {len(matches)}. Per-txn results: {results}"
    )
