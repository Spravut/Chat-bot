"""
Aiogram middleware that records Prometheus metrics for every update.

Captures:
  - count by update type (message / callback_query / etc.)
  - per-handler execution duration (histogram)
  - per-handler error count

The handler label is taken from the resolved handler callback name. This is a
low-cardinality dimension (one label per registered handler) so it's safe
for Prometheus.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.services.metrics import (
    TG_HANDLER_DURATION,
    TG_HANDLER_ERRORS,
    TG_UPDATES,
)


class MetricsMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update_type = type(event).__name__
        TG_UPDATES.labels(update_type=update_type).inc()

        # Best-effort handler name — `data['handler']` is set by aiogram when
        # the router resolves the callback; if it isn't (e.g. middleware ran
        # but no handler matched), fall back to "unmatched".
        handler_obj = data.get("handler")
        handler_name = (
            getattr(handler_obj.callback, "__name__", "unknown")
            if handler_obj else "unmatched"
        )

        start = time.perf_counter()
        try:
            return await handler(event, data)
        except Exception:
            TG_HANDLER_ERRORS.labels(handler=handler_name).inc()
            raise
        finally:
            TG_HANDLER_DURATION.labels(handler=handler_name).observe(
                time.perf_counter() - start
            )
