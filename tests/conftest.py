"""
Test fixtures.

We use aiosqlite for unit tests of the rating math — it has no Postgres-specific
features that the rating algorithm relies on (only `or_`, `func.count`, basic
selects). Schema is created from the SQLAlchemy models directly, not via
Alembic, since SQLite can't run the JSONB / Postgres-only DDL from migrations.

Two compatibility shims required for SQLite:
  1. `JSONB` is Postgres-only — substitute with the generic `JSON` type.
  2. `BigInteger PRIMARY KEY` does NOT autoincrement in SQLite. Only the literal
     `INTEGER PRIMARY KEY` is the ROWID alias that autoincrements. Compile
     BigInteger as INTEGER under the SQLite dialect so the production models
     (which use BigInteger everywhere for Postgres) still work in tests.
"""
from __future__ import annotations

import os

# Force test config BEFORE importing anything from `bot.*`.
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("METRICS_ENABLED", "false")

import pytest_asyncio
from sqlalchemy import BigInteger, JSON as _JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

# Shim 1: JSONB → JSON for SQLite. Must happen BEFORE models import.
import sqlalchemy.dialects.postgresql as _pg
_pg.JSONB = _JSON  # type: ignore[attr-defined]

# Shim 2: BigInteger → INTEGER under SQLite so PK autoincrement works.
@compiles(BigInteger, "sqlite")
def _bigint_to_int_sqlite(element, compiler, **kw):
    return "INTEGER"


from bot.db.models import Base  # noqa: E402  (import after monkeypatches)


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
