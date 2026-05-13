"""
Event publisher — bot side of the bot ↔ worker boundary.

Handlers publish interaction events (like/skip/match/referral/profile update)
via Celery's `send_task`, which writes to RabbitMQ. Workers consume them
and run the heavy rating recalculation off the request path.

Why `send_task` instead of importing the task and calling `.delay()`:
  - The bot process must NOT import the sync worker code (psycopg, sync engine)
    just to enqueue a message. `send_task` only needs the broker URL.
  - Keeps bot and worker images decoupled — they can be deployed independently.

If RabbitMQ is unreachable, we DO NOT raise: the rating system is eventually
consistent (Celery Beat recomputes everything hourly anyway). Logging the
failure is enough so the Telegram interaction still completes.
"""
from __future__ import annotations

import logging
from typing import Any

from celery import Celery

from bot.config import CELERY_BROKER_URL, CELERY_RESULT_BACKEND
from bot.services.metrics import EVENT_PUBLISHED, EVENT_PUBLISH_FAILED

logger = logging.getLogger(__name__)

# Lightweight Celery client (bot side) — only used for `send_task`.
# Does NOT import `bot.worker.tasks` to keep the bot image lean.
_client = Celery(
    "dating_bot_client",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
)
_client.conf.task_default_exchange = "dating"
_client.conf.task_serializer = "json"
_client.conf.accept_content = ["json"]


def _publish(task_name: str, args: tuple = (), kwargs: dict | None = None,
             queue: str = "events", routing_key: str = "event.default") -> bool:
    try:
        _client.send_task(
            task_name,
            args=args,
            kwargs=kwargs or {},
            queue=queue,
            routing_key=routing_key,
            exchange="dating",
        )
        EVENT_PUBLISHED.labels(task=task_name).inc()
        return True
    except Exception as exc:
        EVENT_PUBLISH_FAILED.labels(task=task_name).inc()
        logger.warning("event publish failed (%s): %s", task_name, exc)
        return False


# ── Public API ────────────────────────────────────────────────────────────────

def publish_rating_recalc(user_id: int) -> bool:
    """Schedule an async rating recalc for a single user."""
    return _publish(
        "bot.worker.tasks.recalculate_user_rating",
        args=(user_id,),
        queue="ratings",
        routing_key="rating.update",
    )


def publish_interaction(event_type: str, actor_id: int | None = None,
                        target_id: int | None = None,
                        payload: dict[str, Any] | None = None) -> bool:
    """Publish an interaction event (like / skip / match / referral / …).

    The worker fans out to a rating recalc for the affected user(s) and can
    drive additional consumers (metrics, notifications) in the future.
    """
    event = {
        "type": event_type,
        "actor_id": actor_id,
        "target_id": target_id,
        "payload": payload or {},
    }
    return _publish(
        "bot.worker.tasks.process_interaction_event",
        args=(event,),
        queue="events",
        routing_key=f"event.{event_type}",
    )
