"""
Tests for the Redis-backed fixed-window rate limiter.

Uses the same FakeRedis stand-in as test_cache — minimal subset of the Redis
API the limiter actually calls (INCR, EXPIRE, TTL).
"""
from __future__ import annotations

import pytest

from bot.services.ratelimit import RateLimit, check_and_consume


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.expires[key] = ttl

    async def ttl(self, key: str) -> int:
        return self.expires.get(key, -2)


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


async def test_within_limit_allowed(redis):
    policy = RateLimit(limit=3, window_seconds=60)
    for i in range(3):
        allowed, retry = await check_and_consume(redis, "like", 1, policy)
        assert allowed is True
        assert retry == 0


async def test_exceeding_limit_denied_with_ttl(redis):
    policy = RateLimit(limit=2, window_seconds=42)
    await check_and_consume(redis, "like", 1, policy)
    await check_and_consume(redis, "like", 1, policy)
    allowed, retry = await check_and_consume(redis, "like", 1, policy)
    assert allowed is False
    assert retry == 42  # window TTL


async def test_first_hit_sets_expire(redis):
    policy = RateLimit(limit=5, window_seconds=120)
    await check_and_consume(redis, "report", 7, policy)
    assert redis.expires["rl:report:7"] == 120


async def test_different_users_have_separate_counters(redis):
    policy = RateLimit(limit=1, window_seconds=60)
    a_ok, _ = await check_and_consume(redis, "like", 1, policy)
    b_ok, _ = await check_and_consume(redis, "like", 2, policy)
    # Both first hits should pass — counters are per-user
    assert a_ok and b_ok
    # Second hit on user 1 → denied
    a_again, _ = await check_and_consume(redis, "like", 1, policy)
    assert a_again is False


async def test_different_actions_have_separate_counters(redis):
    likes = RateLimit(limit=1, window_seconds=60)
    reports = RateLimit(limit=1, window_seconds=60)
    ok_like, _ = await check_and_consume(redis, "like", 1, likes)
    ok_report, _ = await check_and_consume(redis, "report", 1, reports)
    # Hitting "like" once and "report" once for the same user should both pass.
    assert ok_like and ok_report
