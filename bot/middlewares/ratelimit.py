"""
Global per-user rate limiting middleware.

Applies to every Telegram update before handlers run. Drops over-limit
updates silently and (once per window) replies with a single warning so the
user knows why the bot stopped responding.

Why a middleware and not per-handler:
  - Spam attacks rarely target one feature; a global cap is the right scope.
  - Catching the abuse before `DatabaseMiddleware` runs avoids opening DB
    sessions for dropped updates — measurable resource savings under flood.

Why warn ONCE per window:
  - If a user spams 100 messages in 10 seconds, replying "slow down" to all
    100 of them would itself slam Telegram's outbound rate limit (1 msg/sec
    per chat). A single warning is enough to inform; subsequent over-limit
    messages are silently dropped.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from redis.asyncio import Redis

from bot.services.metrics import RATE_LIMITED
from bot.services.ratelimit import MESSAGES, RateLimit, check_and_consume

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, redis: Redis, policy: RateLimit | None = None) -> None:
        self.redis = redis
        # Policy is injectable so tests can supply tight limits; production
        # falls back to the env-driven module default.
        self.policy = policy or MESSAGES

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user = getattr(event, "from_user", None)
        if from_user is None:
            # Non-user events (e.g. chat_member changes from the bot itself) —
            # nothing to rate-limit on.
            return await handler(event, data)

        user_id = from_user.id
        allowed, retry_after = await check_and_consume(
            self.redis, "message", user_id, self.policy,
        )
        if allowed:
            return await handler(event, data)

        # Over the limit. Decide whether to send a warning this window.
        await self._maybe_warn(event, user_id, retry_after)
        # Drop the update — handler does not run, no DB session opened.
        return None

    async def _maybe_warn(
        self, event: TelegramObject, user_id: int, retry_after: int,
    ) -> None:
        """Send a single warning per window. Subsequent over-limit hits
        within the same window are silent."""
        warn_key = f"rl_warned:{user_id}"
        # SETNX = atomic "set if not exists", returns True only the first time.
        was_set = await self.redis.set(
            warn_key, "1", ex=max(retry_after, 1), nx=True,
        )
        if not was_set:
            return  # already warned this window

        text = (
            f"⚠️ Слишком много действий подряд. "
            f"Подожди {retry_after} сек и попробуй снова."
        )
        try:
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                # show_alert pops a modal — louder, harder to miss when spamming.
                await event.answer(text, show_alert=True)
        except Exception as exc:
            # Telegram itself may rate-limit us back — log and move on.
            logger.warning("rate-limit warning failed for user %s: %s", user_id, exc)
