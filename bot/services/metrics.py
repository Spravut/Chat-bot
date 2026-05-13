"""
Prometheus metrics.

All counters/histograms are defined at module import time so they appear in
`/metrics` even before the first event fires (Prometheus best practice — avoid
missing series).

The metrics HTTP endpoint is started from `bot/main.py` via `start_metrics_server`.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from prometheus_client import Counter, Histogram, Gauge, start_http_server

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── Telegram-level metrics ────────────────────────────────────────────────────

TG_UPDATES = Counter(
    "dating_bot_tg_updates_total",
    "Telegram updates received, by type",
    ["update_type"],
)
TG_HANDLER_DURATION = Histogram(
    "dating_bot_handler_duration_seconds",
    "Handler execution time, by handler",
    ["handler"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
TG_HANDLER_ERRORS = Counter(
    "dating_bot_handler_errors_total",
    "Handler exceptions, by handler",
    ["handler"],
)

# ── Business metrics ──────────────────────────────────────────────────────────

USERS_REGISTERED = Counter("dating_bot_users_registered_total", "New user registrations")
LIKES_TOTAL      = Counter("dating_bot_likes_total",      "Likes sent")
SKIPS_TOTAL      = Counter("dating_bot_skips_total",      "Skips sent")
MATCHES_TOTAL    = Counter("dating_bot_matches_total",    "Matches created")
REFERRALS_TOTAL  = Counter("dating_bot_referrals_total",  "Referrals registered")

# ── Feed cache ────────────────────────────────────────────────────────────────

FEED_REFILLS = Counter(
    "dating_bot_feed_refills_total",
    "Times the Redis feed cache was refilled from DB",
)
FEED_CANDIDATES_FETCHED = Counter(
    "dating_bot_feed_candidates_fetched_total",
    "Total candidate IDs ever loaded into Redis feed",
)
FEED_QUEUE_LENGTH = Gauge(
    "dating_bot_feed_queue_length",
    "Current length of a user's feed queue (sampled on each pop)",
)

# ── Ranking ───────────────────────────────────────────────────────────────────

RANKING_QUERY_DURATION = Histogram(
    "dating_bot_ranking_query_seconds",
    "Time to compute ranked candidates from DB",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

# ── Photo storage (MinIO) ─────────────────────────────────────────────────────

PHOTO_UPLOADS = Counter(
    "dating_bot_photo_uploads_total",
    "Photo uploads to MinIO, by outcome",
    ["outcome"],  # success / failed
)
PHOTO_UPLOAD_DURATION = Histogram(
    "dating_bot_photo_upload_seconds",
    "Time to download from Telegram + upload to MinIO",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ── Rate limiting / anti-spam ─────────────────────────────────────────────────

RATE_LIMITED = Counter(
    "dating_bot_rate_limited_total",
    "Actions denied by the rate limiter, by action",
    ["action"],
)


# ── Celery event publishing (bot side) ────────────────────────────────────────

EVENT_PUBLISHED = Counter(
    "dating_bot_events_published_total",
    "Events published to RabbitMQ, by task",
    ["task"],
)
EVENT_PUBLISH_FAILED = Counter(
    "dating_bot_events_publish_failed_total",
    "Events that failed to publish",
    ["task"],
)


def start_metrics_server(port: int = 9100) -> None:
    """Start the Prometheus HTTP endpoint on `port`. Safe to call once at boot."""
    try:
        start_http_server(port)
        logger.info("metrics endpoint listening on :%s", port)
    except OSError as exc:
        # Common in tests where the port is already in use; non-fatal.
        logger.warning("failed to start metrics server on :%s: %s", port, exc)
