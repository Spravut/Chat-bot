import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from bot.config import BOT_TOKEN, REDIS_URL
from bot.middlewares.db import DatabaseMiddleware
from bot.handlers import browse, matches, photos, profile, registration, start

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # Redis is used for two purposes:
    #   1. FSM state storage (RedisStorage) — fast distributed state management
    #   2. Feed pre-ranking cache (dp["redis"]) — avoids repeated DB queries
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    storage = RedisStorage(redis=redis)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=storage)

    # Inject Redis instance into all handlers via keyword argument `redis`
    dp["redis"] = redis

    dp.message.middleware(DatabaseMiddleware())
    dp.callback_query.middleware(DatabaseMiddleware())

    dp.include_router(start.router)
    dp.include_router(registration.router)
    dp.include_router(profile.router)
    dp.include_router(browse.router)
    dp.include_router(photos.router)
    dp.include_router(matches.router)

    logger.info("Starting bot (Redis: %s)…", REDIS_URL)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await redis.aclose()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
