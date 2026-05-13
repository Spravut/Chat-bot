"""
Sync mirror of `bot/services/rating.py:update_user_rating` for Celery workers.

Keeps the same arithmetic as the async version. We deliberately duplicate the
formula here (rather than importing the async one) because Celery is sync —
otherwise we'd need an event loop per task, which defeats the point of
delegating heavy work to a background worker.

If the formula changes, update BOTH files. The pytest suite asserts they stay
in sync (`tests/test_rating_consistency.py`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session

from bot.db.models import (
    Like, Match, Photo, Rating, RatingEvent, Referral, UserProfile,
)


def compute_level1_sync(user_id: int, session: Session) -> Decimal:
    profile = session.scalar(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    if not profile:
        return Decimal("0")

    score = 0.0
    if profile.name:           score += 1.0
    if profile.age:            score += 1.0
    if profile.gender:         score += 1.0
    if profile.seeking_gender: score += 1.0
    if profile.city:           score += 1.0
    if profile.bio:            score += 1.5
    if profile.age_min and profile.age_max:
        score += 1.0

    photo_count: int = session.scalar(
        select(func.count()).select_from(Photo).where(Photo.user_id == user_id)
    ) or 0
    score += min(photo_count * 0.5, 2.5)

    return Decimal(str(min(round(score, 4), 10.0)))


def compute_level2_sync(user_id: int, session: Session) -> Decimal:
    likes_received = session.scalar(
        select(func.count()).select_from(Like).where(Like.to_user_id == user_id)
    ) or 0
    skips_received = session.scalar(
        select(func.count()).select_from(RatingEvent).where(
            RatingEvent.user_id == user_id,
            RatingEvent.event_type == "skip_received",
        )
    ) or 0
    match_count = session.scalar(
        select(func.count()).select_from(Match).where(
            or_(Match.user_a_id == user_id, Match.user_b_id == user_id)
        )
    ) or 0

    score = 0.0
    score += min(likes_received * 0.3, 5.0)
    total = likes_received + skips_received
    if total > 0:
        score += (likes_received / total) * 3.0
    score += min(match_count * 0.4, 2.0)

    return Decimal(str(min(round(score, 4), 10.0)))


def compute_level3_sync(
    user_id: int, l1: Decimal, l2: Decimal, session: Session
) -> Decimal:
    referrals = session.scalar(
        select(func.count()).select_from(Referral).where(
            Referral.inviter_user_id == user_id
        )
    ) or 0
    bonus = min(referrals * 0.5, 2.0)
    combined = float(l1) * 0.4 + float(l2) * 0.6 + bonus
    return Decimal(str(round(combined, 4)))


def update_user_rating_sync(user_id: int, session: Session) -> Rating:
    l1 = compute_level1_sync(user_id, session)
    l2 = compute_level2_sync(user_id, session)
    l3 = compute_level3_sync(user_id, l1, l2, session)

    rating = session.get(Rating, user_id)
    if rating is None:
        rating = Rating(
            user_id=user_id,
            level1_score=l1, level2_score=l2, level3_score=l3,
        )
        session.add(rating)
    else:
        rating.level1_score = l1
        rating.level2_score = l2
        rating.level3_score = l3
        rating.computed_at = datetime.now(timezone.utc)
    session.flush()
    return rating
