"""
Celery tasks.

These tasks consume events published from the bot (via `bot.services.events`)
and perform background work that would otherwise block the Telegram handler.

The Bot publishes to RabbitMQ → Celery routes the task to the appropriate
queue → a worker picks it up and runs the sync DB logic.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from bot.db.models import RatingEvent, User
from bot.worker.celery_app import celery_app
from bot.worker.db import session_scope
from bot.worker.rating_sync import update_user_rating_sync

logger = logging.getLogger(__name__)


@celery_app.task(
    name="bot.worker.tasks.recalculate_user_rating",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)
def recalculate_user_rating(self, user_id: int) -> dict:
    """Recompute rating for a single user. Called after each interaction event."""
    with session_scope() as session:
        rating = update_user_rating_sync(user_id, session)
        result = {
            "user_id": user_id,
            "l1": float(rating.level1_score),
            "l2": float(rating.level2_score),
            "l3": float(rating.level3_score),
        }
    logger.info("rating recalculated", extra=result)
    return result


@celery_app.task(name="bot.worker.tasks.recalculate_all_ratings")
def recalculate_all_ratings() -> dict:
    """Periodic Beat task: recompute every user's rating.

    Catches users whose rating got stale (e.g. event-driven recalc missed due
    to worker downtime, or the formula changed).
    """
    processed = 0
    with session_scope() as session:
        user_ids = [row[0] for row in session.execute(select(User.id)).all()]
    for uid in user_ids:
        try:
            with session_scope() as session:
                update_user_rating_sync(uid, session)
            processed += 1
        except Exception:
            logger.exception("rating recalc failed for user_id=%s", uid)
    logger.info("bulk rating recalc done: %s users", processed)
    return {"processed": processed}


@celery_app.task(name="bot.worker.tasks.cleanup_old_rating_events")
def cleanup_old_rating_events(days_to_keep: int = 30) -> dict:
    """Periodic cleanup: drop rating_events older than N days.

    The 24h skip-cooldown only reads recent rows, and Level 2 aggregates
    don't need ancient history — keep the table from growing unbounded.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_to_keep)
    with session_scope() as session:
        result = session.execute(
            delete(RatingEvent).where(RatingEvent.created_at < cutoff)
        )
        deleted = result.rowcount or 0
    logger.info("cleanup: deleted %s old rating events", deleted)
    return {"deleted": deleted, "cutoff": cutoff.isoformat()}


@celery_app.task(name="bot.worker.tasks.process_interaction_event")
def process_interaction_event(event: dict) -> dict:
    """Generic interaction event handler.

    Currently fans out to `recalculate_user_rating` for the affected user(s).
    Designed to be extensible — future consumers (notifications, analytics)
    can subscribe to the `event.#` routing key without modifying handlers.
    """
    event_type = event.get("type")
    actor_id = event.get("actor_id")
    target_id = event.get("target_id")

    affected: list[int] = []
    if event_type in ("like", "match"):
        if target_id:
            affected.append(target_id)
        if event_type == "match" and actor_id:
            affected.append(actor_id)
    elif event_type == "skip":
        if target_id:
            affected.append(target_id)
    elif event_type == "profile_updated" and actor_id:
        affected.append(actor_id)
    elif event_type == "referral" and actor_id:
        affected.append(actor_id)  # inviter

    for uid in set(affected):
        recalculate_user_rating.apply_async(args=(uid,))

    return {"event": event, "scheduled_recalcs": affected}
