"""
Unit tests for the rating algorithm.

Covers each level independently plus the combined Level 3 formula. These tests
pin the exact arithmetic — if the rating formula changes intentionally,
update both the production code AND these expected values.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from bot.db.models import (
    Like, Match, Photo, Rating, RatingEvent, Referral, User, UserProfile,
)
from bot.services.rating import (
    compute_level1,
    compute_level2,
    compute_level3,
    get_ranked_candidates,
    update_user_rating,
)


async def _make_user(session, tg_id: int) -> User:
    u = User(telegram_id=tg_id)
    session.add(u)
    await session.flush()
    return u


async def _make_profile(session, user_id: int, **kwargs) -> UserProfile:
    defaults = dict(
        user_id=user_id,
        name="Alice",
        age=25,
        gender="female",
        seeking_gender="male",
        city="Moscow",
        bio="hello",
        age_min=20,
        age_max=30,
    )
    defaults.update(kwargs)
    p = UserProfile(**defaults)
    session.add(p)
    await session.flush()
    return p


# ── Level 1 ───────────────────────────────────────────────────────────────────

async def test_level1_empty_profile_returns_zero(session):
    user = await _make_user(session, 1)
    assert await compute_level1(user.id, session) == Decimal("0")


async def test_level1_full_profile_with_two_photos(session):
    user = await _make_user(session, 1)
    await _make_profile(session, user.id)
    session.add_all([
        Photo(user_id=user.id, photo_url="a", sort_order=1),
        Photo(user_id=user.id, photo_url="b", sort_order=2),
    ])
    await session.flush()
    # 5×1 (name/age/gender/seeking/city) + 1.5 (bio) + 1.0 (age range) + 2×0.5 (photos) = 8.5
    assert await compute_level1(user.id, session) == Decimal("8.5")


async def test_level1_photo_cap_at_5_photos(session):
    user = await _make_user(session, 1)
    await _make_profile(session, user.id)
    for i in range(1, 8):  # 7 photos but cap is at 2.5 (= 5 photos × 0.5)
        session.add(Photo(user_id=user.id, photo_url=f"p{i}", sort_order=i))
    await session.flush()
    # 5 + 1.5 + 1.0 + 2.5 (capped) = 10.0
    assert await compute_level1(user.id, session) == Decimal("10.0")


# ── Level 2 ───────────────────────────────────────────────────────────────────

async def test_level2_with_likes_and_skips(session):
    target = await _make_user(session, 1)
    likers = [await _make_user(session, i) for i in range(2, 5)]  # 3 likers
    for liker in likers:
        session.add(Like(from_user_id=liker.id, to_user_id=target.id))
    # 2 skips
    session.add_all([
        RatingEvent(user_id=target.id, event_type="skip_received", target_user_id=likers[0].id),
        RatingEvent(user_id=target.id, event_type="skip_received", target_user_id=likers[1].id),
    ])
    await session.flush()

    result = await compute_level2(target.id, session)
    # 3 likes × 0.3 = 0.9; ratio 3/5 = 0.6, ×3 = 1.8 → total 2.7
    assert result == Decimal("2.7")


async def test_level2_caps_likes_at_5(session):
    target = await _make_user(session, 1)
    for i in range(2, 25):  # 23 likers — but cap should hit at 5.0
        liker = await _make_user(session, i)
        session.add(Like(from_user_id=liker.id, to_user_id=target.id))
    await session.flush()
    score = await compute_level2(target.id, session)
    # Likes cap = 5.0, no skips so ratio = 1.0 × 3.0 = 3.0 → total 8.0
    assert score == Decimal("8.0")


# ── Level 3 ───────────────────────────────────────────────────────────────────

async def test_level3_combines_levels_with_referral_bonus(session):
    user = await _make_user(session, 1)
    # Two referrals → +1.0 bonus
    for tg_id in (2, 3):
        inv = await _make_user(session, tg_id)
        session.add(Referral(inviter_user_id=user.id, referred_user_id=inv.id))
    await session.flush()

    l3 = await compute_level3(user.id, Decimal("8.0"), Decimal("6.0"), session)
    # 8 * 0.4 + 6 * 0.6 + 1.0 = 3.2 + 3.6 + 1.0 = 7.8
    assert l3 == Decimal("7.8")


async def test_level3_referral_bonus_capped(session):
    user = await _make_user(session, 1)
    for tg_id in range(2, 12):  # 10 referrals — but bonus cap is +2.0
        inv = await _make_user(session, tg_id)
        session.add(Referral(inviter_user_id=user.id, referred_user_id=inv.id))
    await session.flush()
    l3 = await compute_level3(user.id, Decimal("0"), Decimal("0"), session)
    assert l3 == Decimal("2.0")


# ── update_user_rating persists ────────────────────────────────────────────────

async def test_update_user_rating_inserts_then_updates(session):
    user = await _make_user(session, 1)
    await _make_profile(session, user.id)
    rating1 = await update_user_rating(user.id, session)
    assert rating1.level1_score > 0
    saved = await session.get(Rating, user.id)
    assert saved is not None
    # Second call should update in place
    rating2 = await update_user_rating(user.id, session)
    assert rating2.user_id == rating1.user_id


# ── Candidate ranking ──────────────────────────────────────────────────────────

async def test_ranking_excludes_already_liked(session):
    viewer = await _make_user(session, 1)
    await _make_profile(session, viewer.id)
    other = await _make_user(session, 2)
    await _make_profile(session, other.id, name="Bob", gender="male", seeking_gender="female")
    session.add(Like(from_user_id=viewer.id, to_user_id=other.id))
    await session.flush()

    candidates = await get_ranked_candidates(viewer.id, session)
    assert other.id not in candidates


async def test_ranking_respects_gender_seeking(session):
    viewer = await _make_user(session, 1)
    await _make_profile(session, viewer.id, gender="female", seeking_gender="male")

    male = await _make_user(session, 2)
    await _make_profile(session, male.id, name="Bob", gender="male", seeking_gender="female")

    female = await _make_user(session, 3)
    await _make_profile(session, female.id, name="Carol", gender="female", seeking_gender="male")

    await session.flush()
    candidates = await get_ranked_candidates(viewer.id, session)
    assert male.id in candidates
    assert female.id not in candidates  # wrong gender for viewer's preference
