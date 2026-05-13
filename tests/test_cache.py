"""
Tests for the Redis feed cache.

Uses fakeredis-style behavior — we'd add `fakeredis[asyncio]` to do it right,
but for now we keep these as lightweight FIFO assertions against an in-memory
mock that mimics the subset of Redis we use.
"""
from __future__ import annotations

import pytest

from bot.services import cache


class FakeRedis:
    """Tiny stand-in for redis.asyncio.Redis (only methods cache.py uses)."""
    def __init__(self) -> None:
        self.store: dict[str, list[str]] = {}
        self.expires: dict[str, int] = {}

    async def llen(self, key: str) -> int:
        return len(self.store.get(key, []))

    async def rpush(self, key: str, *values: str) -> int:
        self.store.setdefault(key, []).extend(values)
        return len(self.store[key])

    async def lpop(self, key: str):
        lst = self.store.get(key)
        return lst.pop(0) if lst else None

    async def expire(self, key: str, ttl: int) -> None:
        self.expires[key] = ttl

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)
        self.expires.pop(key, None)


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


async def test_push_pop_is_fifo(redis):
    await cache.push_profiles(redis, 42, [1, 2, 3])
    assert await cache.pop_next(redis, 42) == 1
    assert await cache.pop_next(redis, 42) == 2
    assert await cache.pop_next(redis, 42) == 3
    assert await cache.pop_next(redis, 42) is None


async def test_needs_refill_threshold(redis):
    await cache.push_profiles(redis, 42, [1, 2, 3])
    assert await cache.needs_refill(redis, 42) is False
    await cache.pop_next(redis, 42)
    await cache.pop_next(redis, 42)
    # length is now 1, which is < REFILL_THRESHOLD (=2)
    assert await cache.needs_refill(redis, 42) is True


async def test_push_sets_ttl(redis):
    await cache.push_profiles(redis, 42, [1, 2])
    assert "feed:42" in redis.expires
    assert redis.expires["feed:42"] == 1800


async def test_clear_feed(redis):
    await cache.push_profiles(redis, 42, [1, 2, 3])
    await cache.clear_feed(redis, 42)
    assert await cache.feed_length(redis, 42) == 0


async def test_push_empty_list_is_noop(redis):
    await cache.push_profiles(redis, 42, [])
    assert await cache.feed_length(redis, 42) == 0
