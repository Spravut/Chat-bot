"""
Synchronous DB session for Celery tasks.

Celery is sync; the async session/engine in `bot/db/session.py` is for handlers.
We mirror the same models against a sync engine using psycopg.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from bot.config import DATABASE_URL_SYNC

engine = create_engine(DATABASE_URL_SYNC, pool_pre_ping=True, future=True)
SyncSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a sync session that commits on success / rolls back on error."""
    session = SyncSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
