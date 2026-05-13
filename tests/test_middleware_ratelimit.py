"""
Tests for the global RateLimitMiddleware that wraps every Telegram update.

Verifies:
  - within-limit updates pass through to the handler
  - over-limit updates are dropped (handler NOT called)
  - a single warning is sent on the FIRST over-limit hit per window
  - subsequent over-limit hits in the same window are silent
  - updates without a from_user (rare bot-level events) pass through unfiltered
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.middlewares.ratelimit import RateLimitMiddleware
from bot.services.ratelimit import RateLimit

# Tight policy for tests — middleware accepts an explicit override so we
# don't depend on env-driven module-level defaults.
TIGHT_POLICY = RateLimit(limit=3, window_seconds=60)


class FakeRedis:
    """Minimal Redis subset: INCR, EXPIRE, TTL, SET (with NX/EX)."""
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        v = int(self.store.get(key, "0")) + 1
        self.store[key] = str(v)
        return v

    async def expire(self, key: str, ttl: int) -> None:
        self.expires[key] = ttl

    async def ttl(self, key: str) -> int:
        return self.expires.get(key, -2)

    async def set(self, key: str, value: str, ex: int | None = None,
                  nx: bool = False) -> bool | None:
        if nx and key in self.store:
            return None  # already exists
        self.store[key] = value
        if ex is not None:
            self.expires[key] = ex
        return True


def _fake_message(user_id: int) -> MagicMock:
    """Mimics aiogram's Message just enough for the middleware."""
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    # isinstance(event, Message) check — make the mock pass it.
    from aiogram.types import Message
    msg.__class__ = Message
    return msg


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def downstream_handler():
    """Records whether the wrapped handler ran. Returns sentinel value."""
    handler = AsyncMock(return_value="HANDLER_RAN")
    return handler


async def test_within_limit_passes_through(redis, downstream_handler):
    mw = RateLimitMiddleware(redis, policy=TIGHT_POLICY)
    msg = _fake_message(user_id=42)

    # 3 calls within the limit of 3 → all should reach the handler
    for _ in range(3):
        result = await mw(downstream_handler, msg, {})
        assert result == "HANDLER_RAN"
    assert downstream_handler.await_count == 3
    msg.answer.assert_not_called()


async def test_over_limit_drops_and_warns_once(redis, downstream_handler):
    mw = RateLimitMiddleware(redis, policy=TIGHT_POLICY)
    msg = _fake_message(user_id=42)

    # Burn the budget
    for _ in range(3):
        await mw(downstream_handler, msg, {})
    downstream_handler.reset_mock()

    # 4th call — over limit. Handler must NOT run. ONE warning sent.
    result = await mw(downstream_handler, msg, {})
    assert result is None
    downstream_handler.assert_not_called()
    assert msg.answer.await_count == 1
    warning = msg.answer.await_args.args[0]
    assert "Слишком много" in warning

    # 5th, 6th — still over limit, but no further warnings (silent drop).
    msg.answer.reset_mock()
    await mw(downstream_handler, msg, {})
    await mw(downstream_handler, msg, {})
    downstream_handler.assert_not_called()
    msg.answer.assert_not_called()


async def test_per_user_counters_independent(redis, downstream_handler):
    mw = RateLimitMiddleware(redis, policy=TIGHT_POLICY)
    alice = _fake_message(user_id=1)
    bob   = _fake_message(user_id=2)

    # Alice burns her budget
    for _ in range(3):
        await mw(downstream_handler, alice, {})
    # Bob's first message — should still be allowed.
    result = await mw(downstream_handler, bob, {})
    assert result == "HANDLER_RAN"


async def test_event_without_user_bypasses_limit(redis, downstream_handler):
    mw = RateLimitMiddleware(redis, policy=TIGHT_POLICY)
    no_user_event = MagicMock(spec=[])  # no from_user attribute

    # Even if we somehow call it many times, no Redis interaction happens.
    for _ in range(10):
        await mw(downstream_handler, no_user_event, {})
    assert downstream_handler.await_count == 10
    # Redis store stays empty — no rate-limit key was touched.
    assert redis.store == {}
