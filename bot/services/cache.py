"""
Redis feed cache.

Strategy:
  - Key  feed:{user_id}  stores a list of candidate profile user_ids.
  - When the list drops below REFILL_THRESHOLD, the browse handler fetches
    10 new candidates from the DB and pushes them to the right end.
  - The browse handler pops from the left — FIFO queue.
  - TTL is reset on every refill so idle queues expire automatically.
"""
from __future__ import annotations

from redis.asyncio import Redis

from bot.services.metrics import (
    FEED_CANDIDATES_FETCHED,
    FEED_QUEUE_LENGTH,
    FEED_REFILLS,
)

_FEED_KEY = "feed:{user_id}"
_FEED_TTL = 1800          # 30 minutes
REFILL_THRESHOLD = 2      # refill when fewer than this many profiles remain


async def feed_length(redis: Redis, user_id: int) -> int:
    return await redis.llen(_FEED_KEY.format(user_id=user_id))


async def needs_refill(redis: Redis, user_id: int) -> bool:
    return await feed_length(redis, user_id) < REFILL_THRESHOLD


async def push_profiles(redis: Redis, user_id: int, profile_ids: list[int]) -> None:
    if not profile_ids:
        return
    key = _FEED_KEY.format(user_id=user_id)
    await redis.rpush(key, *[str(pid) for pid in profile_ids])
    await redis.expire(key, _FEED_TTL)
    FEED_REFILLS.inc()
    FEED_CANDIDATES_FETCHED.inc(len(profile_ids))


async def pop_next(redis: Redis, user_id: int) -> int | None:
    key = _FEED_KEY.format(user_id=user_id)
    value = await redis.lpop(key)
    FEED_QUEUE_LENGTH.set(await redis.llen(key))
    return int(value) if value is not None else None


async def clear_feed(redis: Redis, user_id: int) -> None:
    await redis.delete(_FEED_KEY.format(user_id=user_id))
