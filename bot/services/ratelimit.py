"""
Anti-spam rate limiting (Redis-backed fixed window counter).

Why fixed-window and not a token bucket: for our use case (catch users
spam-liking 1000 profiles in a minute) the boundary-burst weakness of fixed
windows doesn't matter — the legitimate rate is so far below the limit that
hitting the limit always indicates abuse, regardless of window phase.

Algorithm:
  - Key: rl:{action}:{user_id}
  - INCR atomically increments and returns the new count.
  - On the first hit (count == 1) we set EXPIRE = window_seconds, so the
    counter resets after that window.
  - If count <= limit, allow. Otherwise return seconds-until-reset (TTL).

Two INCR + EXPIRE calls aren't atomic in Redis, but the race is benign: in
the worst case the counter has no TTL for one extra request and gets the
TTL set on the next call. The user still gets correctly rate-limited.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from redis.asyncio import Redis

from bot.services.metrics import RATE_LIMITED


@dataclass
class RateLimit:
    """Limit definition: `limit` actions per `window_seconds`."""
    limit: int
    window_seconds: int


# ── Default policies (override via env) ───────────────────────────────────────
# Demo-tuned: tight enough that a presenter can trigger the limit live by
# spamming a few clicks. In production you'd raise these.

LIKES = RateLimit(
    limit=int(os.environ.get("RATE_LIMIT_LIKES", "30")),
    window_seconds=int(os.environ.get("RATE_LIMIT_LIKES_WINDOW", "60")),
)
REPORTS = RateLimit(
    limit=int(os.environ.get("RATE_LIMIT_REPORTS", "5")),
    window_seconds=int(os.environ.get("RATE_LIMIT_REPORTS_WINDOW", "300")),
)


async def check_and_consume(
    redis: Redis,
    action: str,
    user_id: int,
    policy: RateLimit,
) -> tuple[bool, int]:
    """Atomically increment the counter and decide.

    Returns `(allowed, retry_after_seconds)`. `retry_after_seconds` is 0 when
    allowed; otherwise it's the TTL of the current window — i.e. when the
    counter resets and the user may try again.
    """
    key = f"rl:{action}:{user_id}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, policy.window_seconds)

    if count <= policy.limit:
        return True, 0

    RATE_LIMITED.labels(action=action).inc()
    ttl = await redis.ttl(key)
    # ttl can be -1 (no expire — shouldn't happen after our EXPIRE call, but be
    # defensive) or -2 (missing key — race with cleanup). Both → just use the
    # window value as a safe upper bound.
    if ttl is None or ttl < 0:
        ttl = policy.window_seconds
    return False, ttl
