"""
Celery application.

Architecture:
  - Broker: RabbitMQ (durable queues for interaction events).
  - Result backend: Redis (DB 1) — only used for occasional result checks.
  - Three queues with explicit routing:
      * `ratings`    — recalculation tasks (heavy; isolated so they can't
                       starve event handling).
      * `events`     — fan-out of user interactions (like/skip/match) for
                       downstream consumers (metrics, future analytics).
      * `maintenance`— periodic Beat tasks (full-rating recalc, cleanups).

The worker is sync — Celery on Python doesn't speak asyncio natively, so we use
the sync `psycopg` driver via `DATABASE_URL_SYNC`. The bot itself stays fully
async; only background work runs sync inside Celery.
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from kombu import Exchange, Queue

from bot.config import CELERY_BROKER_URL, CELERY_RESULT_BACKEND

celery_app = Celery(
    "dating_bot",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["bot.worker.tasks"],
)

# Queues / exchanges
default_exchange = Exchange("dating", type="topic", durable=True)

celery_app.conf.task_queues = (
    Queue("ratings",     default_exchange, routing_key="rating.#",      durable=True),
    Queue("events",      default_exchange, routing_key="event.#",       durable=True),
    Queue("maintenance", default_exchange, routing_key="maintenance.#", durable=True),
)
celery_app.conf.task_default_queue = "events"
celery_app.conf.task_default_exchange = "dating"
celery_app.conf.task_default_routing_key = "event.default"

celery_app.conf.task_routes = {
    "bot.worker.tasks.recalculate_user_rating":  {"queue": "ratings",     "routing_key": "rating.update"},
    "bot.worker.tasks.recalculate_all_ratings":  {"queue": "maintenance", "routing_key": "maintenance.recalc_all"},
    "bot.worker.tasks.cleanup_old_rating_events":{"queue": "maintenance", "routing_key": "maintenance.cleanup"},
    "bot.worker.tasks.process_interaction_event":{"queue": "events",      "routing_key": "event.interaction"},
}

# Reliability tuning
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True
celery_app.conf.worker_prefetch_multiplier = 4
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.timezone = "UTC"
celery_app.conf.enable_utc = True

# Beat schedule — periodic background work
celery_app.conf.beat_schedule = {
    "recalc-all-ratings-hourly": {
        "task": "bot.worker.tasks.recalculate_all_ratings",
        "schedule": crontab(minute=0),  # every hour
        "options": {"queue": "maintenance"},
    },
    "cleanup-old-rating-events-daily": {
        "task": "bot.worker.tasks.cleanup_old_rating_events",
        "schedule": crontab(minute=15, hour=3),  # 03:15 UTC daily
        "args": (30,),  # keep last 30 days
        "options": {"queue": "maintenance"},
    },
}
