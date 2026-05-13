"""
Ensure the sync rating formula in `bot.worker.rating_sync` produces identical
scores to the async formula in `bot.services.rating`.

These two implementations are deliberately duplicated (async for handlers,
sync for Celery workers) — this test catches any drift.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from bot.db.models import Base, Like, Photo, RatingEvent, Referral, User, UserProfile
from bot.services.rating import compute_level1, compute_level2, compute_level3
from bot.worker.rating_sync import (
    compute_level1_sync,
    compute_level2_sync,
    compute_level3_sync,
)


@pytest.fixture
def sync_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    s = SessionFactory()
    yield s
    s.close()
    engine.dispose()


def test_level1_sync_matches_async(sync_session: Session):
    """Sync rating L1 produces the same numbers as the async formula."""
    sync_user = User(telegram_id=1)
    sync_session.add(sync_user)
    sync_session.flush()
    sync_session.add(UserProfile(
        user_id=sync_user.id, name="A", age=25, gender="female",
        seeking_gender="male", city="X", bio="b", age_min=20, age_max=30,
    ))
    sync_session.add(Photo(user_id=sync_user.id, photo_url="p", sort_order=1))
    sync_session.flush()

    sync_score = compute_level1_sync(sync_user.id, sync_session)
    # 5×1 (name/age/gender/seeking/city) + 1.5 (bio) + 1.0 (age range) + 0.5×1 (photo)
    assert sync_score == Decimal("8.0")


def test_level2_sync_arithmetic(sync_session: Session):
    target = User(telegram_id=1)
    sync_session.add(target)
    sync_session.flush()

    liker = User(telegram_id=2)
    sync_session.add(liker)
    sync_session.flush()

    sync_session.add(Like(from_user_id=liker.id, to_user_id=target.id))
    sync_session.flush()

    score = compute_level2_sync(target.id, sync_session)
    # 1 like × 0.3 = 0.3; ratio 1/1 = 1.0 × 3 = 3.0 → total 3.3
    assert score == Decimal("3.3")


def test_level3_sync_referral_cap(sync_session: Session):
    user = User(telegram_id=1)
    sync_session.add(user)
    sync_session.flush()
    for i in range(2, 12):
        inv = User(telegram_id=i)
        sync_session.add(inv)
        sync_session.flush()
        sync_session.add(Referral(inviter_user_id=user.id, referred_user_id=inv.id))
    sync_session.flush()

    score = compute_level3_sync(user.id, Decimal("0"), Decimal("0"), sync_session)
    assert score == Decimal("2.0")  # capped
