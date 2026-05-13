"""
Tests for the event publisher. We don't bring up a real RabbitMQ — we monkey-
patch the Celery client's `send_task` to capture publish attempts and verify
the routing.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bot.services import events


@pytest.fixture
def captured(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def fake_send(name, args=(), kwargs=None, queue=None, routing_key=None, exchange=None):
        calls.append({
            "name": name, "args": args, "kwargs": kwargs or {},
            "queue": queue, "routing_key": routing_key, "exchange": exchange,
        })
        return MagicMock(id="fake-task-id")

    monkeypatch.setattr(events._client, "send_task", fake_send)
    return calls


def test_publish_rating_recalc_routes_to_ratings_queue(captured):
    assert events.publish_rating_recalc(42) is True
    assert len(captured) == 1
    call = captured[0]
    assert call["name"] == "bot.worker.tasks.recalculate_user_rating"
    assert call["args"] == (42,)
    assert call["queue"] == "ratings"
    assert call["routing_key"] == "rating.update"


def test_publish_interaction_routes_by_event_type(captured):
    events.publish_interaction("like", actor_id=1, target_id=2)
    events.publish_interaction("skip", actor_id=3, target_id=4)
    events.publish_interaction("referral", actor_id=5)

    assert [c["routing_key"] for c in captured] == [
        "event.like", "event.skip", "event.referral",
    ]
    assert all(c["queue"] == "events" for c in captured)
    assert all(c["name"] == "bot.worker.tasks.process_interaction_event" for c in captured)


def test_publish_swallows_broker_errors(captured, monkeypatch):
    def fail(*a, **kw):
        raise ConnectionError("RabbitMQ down")
    monkeypatch.setattr(events._client, "send_task", fail)
    # Must NOT raise — bot interactions should continue even if MQ is dead.
    assert events.publish_rating_recalc(42) is False
    assert events.publish_interaction("like", actor_id=1, target_id=2) is False
