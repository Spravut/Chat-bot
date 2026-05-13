"""
Tests for the moderation features:
  - banned users disappear from the feed
  - blocks exclude both directions from the feed
  - reports persist with default `pending` status
"""
from __future__ import annotations

import pytest

from bot.db.models import Block, Report, User, UserProfile
from bot.services.rating import get_ranked_candidates


async def _make_user(session, tg_id: int, **kwargs) -> User:
    u = User(telegram_id=tg_id, **kwargs)
    session.add(u)
    await session.flush()
    return u


async def _make_profile(session, user_id: int, **overrides) -> UserProfile:
    base = dict(
        user_id=user_id, name="X", age=25,
        gender="female", seeking_gender="male", city="X",
    )
    base.update(overrides)
    p = UserProfile(**base)
    session.add(p)
    await session.flush()
    return p


# ── Ban ───────────────────────────────────────────────────────────────────────

async def test_banned_users_excluded_from_feed(session):
    viewer = await _make_user(session, 1)
    await _make_profile(session, viewer.id, gender="female", seeking_gender="male")

    good = await _make_user(session, 2)
    await _make_profile(session, good.id, name="Bob", gender="male", seeking_gender="female")

    bad = await _make_user(session, 3, is_banned=True)
    await _make_profile(session, bad.id, name="Mallory", gender="male", seeking_gender="female")

    await session.flush()
    candidates = await get_ranked_candidates(viewer.id, session)
    assert good.id in candidates
    assert bad.id not in candidates


# ── Block ─────────────────────────────────────────────────────────────────────

async def test_outgoing_block_hides_blocked_from_feed(session):
    viewer = await _make_user(session, 1)
    await _make_profile(session, viewer.id, gender="female", seeking_gender="male")

    other = await _make_user(session, 2)
    await _make_profile(session, other.id, name="Bob", gender="male", seeking_gender="female")

    session.add(Block(blocker_id=viewer.id, blocked_id=other.id))
    await session.flush()

    candidates = await get_ranked_candidates(viewer.id, session)
    assert other.id not in candidates


async def test_incoming_block_also_hides(session):
    """If A blocks B, B's feed should not show A either (symmetric in effect)."""
    a = await _make_user(session, 1)
    await _make_profile(session, a.id, gender="female", seeking_gender="male")

    b = await _make_user(session, 2)
    await _make_profile(session, b.id, name="Bob", gender="male", seeking_gender="female")

    session.add(Block(blocker_id=a.id, blocked_id=b.id))
    await session.flush()

    candidates_b = await get_ranked_candidates(b.id, session)
    assert a.id not in candidates_b


async def test_block_uniqueness(session):
    """Duplicate (blocker, blocked) pairs are rejected by the unique index."""
    from sqlalchemy.exc import IntegrityError

    a = await _make_user(session, 1)
    b = await _make_user(session, 2)
    session.add(Block(blocker_id=a.id, blocked_id=b.id))
    await session.flush()

    session.add(Block(blocker_id=a.id, blocked_id=b.id))
    with pytest.raises(IntegrityError):
        await session.flush()


# ── Report ────────────────────────────────────────────────────────────────────

async def test_report_persists_with_pending_status(session):
    from sqlalchemy import select

    a = await _make_user(session, 1)
    b = await _make_user(session, 2)
    session.add(Report(
        reporter_id=a.id, reported_id=b.id,
        reason="spam", comment="sends ads",
    ))
    await session.commit()

    # Server-side default for `status` only applies after flush; re-read to
    # confirm what actually persisted.
    fresh = await session.scalar(
        select(Report).where(Report.reporter_id == a.id)
    )
    assert fresh is not None
    assert fresh.status == "pending"
    assert fresh.reason == "spam"
    assert fresh.comment == "sends ads"
    assert fresh.reviewed_at is None


async def test_report_self_rejected(session):
    """CHECK constraint forbids self-reports."""
    from sqlalchemy.exc import IntegrityError

    a = await _make_user(session, 1)
    session.add(Report(reporter_id=a.id, reported_id=a.id, reason="spam"))
    with pytest.raises(IntegrityError):
        await session.flush()
