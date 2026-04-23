"""
Rating service — three-level ranking algorithm.

Level 1 (primary):  profile completeness + photo count
Level 2 (behavioral): likes received, like/skip ratio, matches
Level 3 (combined):   weighted mix of L1+L2 + referral bonus
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import (
    Like, Match, Photo, Rating, RatingEvent, Referral, User, UserProfile,
)


# ── Level 1: profile completeness ─────────────────────────────────────────────

async def compute_level1(user_id: int, session: AsyncSession) -> Decimal:
    profile = await session.scalar(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    if not profile:
        return Decimal("0")

    score = 0.0
    if profile.name:            score += 1.0
    if profile.age:             score += 1.0
    if profile.gender:          score += 1.0
    if profile.seeking_gender:  score += 1.0
    if profile.city:            score += 1.0
    if profile.bio:             score += 1.5
    if profile.age_min and profile.age_max:
        score += 1.0

    photo_count: int = await session.scalar(
        select(func.count()).select_from(Photo).where(Photo.user_id == user_id)
    ) or 0
    score += min(photo_count * 0.5, 2.5)

    return Decimal(str(min(round(score, 4), 10.0)))


# ── Level 2: behavioral ────────────────────────────────────────────────────────

async def compute_level2(user_id: int, session: AsyncSession) -> Decimal:
    likes_received: int = await session.scalar(
        select(func.count()).select_from(Like).where(Like.to_user_id == user_id)
    ) or 0

    skips_received: int = await session.scalar(
        select(func.count()).select_from(RatingEvent).where(
            RatingEvent.user_id == user_id,
            RatingEvent.event_type == "skip_received",
        )
    ) or 0

    match_count: int = await session.scalar(
        select(func.count()).select_from(Match).where(
            or_(Match.user_a_id == user_id, Match.user_b_id == user_id)
        )
    ) or 0

    score = 0.0
    score += min(likes_received * 0.3, 5.0)

    total_views = likes_received + skips_received
    if total_views > 0:
        like_ratio = likes_received / total_views
        score += like_ratio * 3.0

    score += min(match_count * 0.4, 2.0)

    return Decimal(str(min(round(score, 4), 10.0)))


# ── Level 3: combined + referral bonus ────────────────────────────────────────

async def compute_level3(
    user_id: int,
    l1: Decimal,
    l2: Decimal,
    session: AsyncSession,
) -> Decimal:
    referral_count: int = await session.scalar(
        select(func.count()).select_from(Referral).where(
            Referral.inviter_user_id == user_id
        )
    ) or 0

    referral_bonus = min(referral_count * 0.5, 2.0)
    combined = float(l1) * 0.4 + float(l2) * 0.6 + referral_bonus
    return Decimal(str(round(combined, 4)))


# ── Persist / update rating row ───────────────────────────────────────────────

async def update_user_rating(user_id: int, session: AsyncSession) -> Rating:
    l1 = await compute_level1(user_id, session)
    l2 = await compute_level2(user_id, session)
    l3 = await compute_level3(user_id, l1, l2, session)

    rating = await session.get(Rating, user_id)
    if rating is None:
        rating = Rating(
            user_id=user_id,
            level1_score=l1,
            level2_score=l2,
            level3_score=l3,
        )
        session.add(rating)
    else:
        rating.level1_score = l1
        rating.level2_score = l2
        rating.level3_score = l3
        rating.computed_at = func.now()

    await session.flush()
    return rating


# ── Candidate feed for a given viewer ─────────────────────────────────────────

async def get_ranked_candidates(
    viewer_id: int,
    session: AsyncSession,
    limit: int = 10,
) -> list[int]:
    """Return up to `limit` candidate user IDs ranked by combined rating."""
    viewer_profile = await session.scalar(
        select(UserProfile).where(UserProfile.user_id == viewer_id)
    )
    if not viewer_profile:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    # Already liked by the viewer
    liked_sq = select(Like.to_user_id).where(Like.from_user_id == viewer_id)

    # Skipped by the viewer in the last 24 h
    skipped_sq = (
        select(RatingEvent.target_user_id).where(
            RatingEvent.user_id == viewer_id,
            RatingEvent.event_type == "skipped",
            RatingEvent.created_at > cutoff,
            RatingEvent.target_user_id.isnot(None),
        )
    )

    stmt = (
        select(UserProfile.user_id)
        .outerjoin(Rating, Rating.user_id == UserProfile.user_id)
        .where(
            UserProfile.user_id != viewer_id,
            UserProfile.user_id.not_in(liked_sq),
            UserProfile.user_id.not_in(skipped_sq),
            UserProfile.name.isnot(None),
        )
        .order_by(Rating.level3_score.desc().nullslast())
        .limit(limit)
    )

    # Gender filter
    if viewer_profile.seeking_gender and viewer_profile.seeking_gender != "any":
        stmt = stmt.where(UserProfile.gender == viewer_profile.seeking_gender)

    # Age range filters
    if viewer_profile.age_min:
        stmt = stmt.where(UserProfile.age >= viewer_profile.age_min)
    if viewer_profile.age_max:
        stmt = stmt.where(UserProfile.age <= viewer_profile.age_max)

    # Candidate must also be seeking the viewer's gender
    if viewer_profile.gender:
        stmt = stmt.where(
            or_(
                UserProfile.seeking_gender == viewer_profile.gender,
                UserProfile.seeking_gender == "any",
                UserProfile.seeking_gender.is_(None),
            )
        )

    result = await session.execute(stmt)
    return [row[0] for row in result.all()]
