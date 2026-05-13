"""
Structured logging setup with structlog.

Why structured logs:
  - Telegram bots get a lot of similar-looking text events — JSON logs are
    indexable in Loki/ELK/etc. without regex parsing.
  - Each log line gets `event`, `level`, `timestamp`, plus contextual fields
    bound via `structlog.contextvars` (e.g. `user_id`, `chat_id`).
  - Bridges the stdlib `logging` module too, so libraries' logs stay
    consistent.
"""
from __future__ import annotations

import logging
import os
import sys

import structlog

_configured = False


def configure_logging(level: str | int | None = None) -> None:
    """Idempotent — safe to call from worker and bot entry points."""
    global _configured
    if _configured:
        return

    log_level_name = (level if isinstance(level, str) else None) or os.environ.get(
        "LOG_LEVEL", "INFO"
    )
    if isinstance(level, int):
        log_level = level
    else:
        log_level = getattr(logging, str(log_level_name).upper(), logging.INFO)

    # Format depends on env: JSON in containerized environments, pretty in TTY.
    use_json = os.environ.get("LOG_JSON", "true").lower() == "true"
    renderer = (
        structlog.processors.JSONRenderer()
        if use_json
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging (aiogram, sqlalchemy, etc.) into structlog.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )
    # Silence the chattiest libraries unless we're explicitly debugging.
    for noisy in ("sqlalchemy.engine", "aiormq", "pika"):
        logging.getLogger(noisy).setLevel(max(log_level, logging.WARNING))

    _configured = True
