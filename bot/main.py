import asyncio

import structlog
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from bot.config import BOT_TOKEN, METRICS_ENABLED, METRICS_PORT, REDIS_URL
from bot.handlers import browse, matches, photos, profile, registration, start
from bot.logging_config import configure_logging
from bot.middlewares.db import DatabaseMiddleware
from bot.middlewares.metrics import MetricsMiddleware
from bot.middlewares.ratelimit import RateLimitMiddleware
from bot.services.metrics import start_metrics_server

configure_logging()
logger = structlog.get_logger(__name__)


async def main() -> None:
    if METRICS_ENABLED:
        start_metrics_server(METRICS_PORT)

    # Redis is used for:
    #   1. FSM state storage (RedisStorage) — fast distributed state management
    #   2. Feed pre-ranking cache (dp["redis"]) — avoids repeated DB queries
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    storage = RedisStorage(redis=redis)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=storage)

    # Inject Redis instance into all handlers via keyword argument `redis`
    dp["redis"] = redis

    # Middleware order (outer → inner):
    #   1. Metrics  — record every update including the dropped ones
    #   2. RateLimit — drop over-limit updates BEFORE opening a DB session
    #   3. DB       — open SQLAlchemy session only for updates that survive
    dp.message.middleware(MetricsMiddleware())
    dp.callback_query.middleware(MetricsMiddleware())
    dp.message.middleware(RateLimitMiddleware(redis))
    dp.callback_query.middleware(RateLimitMiddleware(redis))
    dp.message.middleware(DatabaseMiddleware())
    dp.callback_query.middleware(DatabaseMiddleware())

    dp.include_router(start.router)
    dp.include_router(registration.router)
    dp.include_router(profile.router)
    dp.include_router(browse.router)
    dp.include_router(photos.router)
    dp.include_router(matches.router)

    logger.info("bot starting", redis_url=REDIS_URL, metrics_port=METRICS_PORT)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await redis.aclose()
        logger.info("bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
