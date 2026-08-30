"""Dashboard event bus — bridged onto the canonical EventBus (Rebuild §4, §8).

This module is a **migration bridge**: it keeps the exact ``{id, type, ts, data}``
dict API that the gateway, speech and tests consume, so realtime streaming is
unchanged, but every event is **also** canonicalized into an
:class:`core.contracts.EventEnvelope` and published to the one durable
:class:`core.events.EventBus`. That makes the canonical bus the single
auditable/replayable event source while this bridge remains temporarily.

Deletion milestone: once every event producer is migrated to
``core.events.publish`` (and consumers read ``EventEnvelope``), this module is
deleted and the dict adapter is gone.
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from datetime import datetime
from typing import Any, Optional
from collections.abc import Callable


def _canonicalize(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    from .contracts import EventEnvelope, EventType, CommandStatus
    from .events import get_bus

    # Map the free-form dashboard event_type into the canonical envelope while
    # preserving the original label in `command`.
    env = EventEnvelope(
        type=EventType.STATE_CHANGED.value,
        command=str(event_type or "dashboard.event"),
        target=data.get("run_id") or data.get("session_id"),
        args_redacted=data,
        status=CommandStatus.PENDING.value,
        source="dashboard",
    )
    get_bus().publish(env)
    return env.to_dict()


class DashboardEventBus:
    """Small thread-safe publish/subscribe bus with a recent-event snapshot.

    Keeps the historical dict shape for compatibility; every publish also lands on
    the canonical EventBus (see :func:`_canonicalize`).
    """

    def __init__(self, max_events: int = 300):
        self._events: deque[dict[str, Any]] = deque(maxlen=max(20, int(max_events)))
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._lock = threading.RLock()

    def publish(self, event_type: str, data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        event = {
            "id": uuid.uuid4().hex,
            "type": str(event_type or "event"),
            "ts": datetime.now().astimezone().isoformat(),
            "data": dict(data or {}),
        }
        with self._lock:
            self._events.append(event)
            subscribers = list(self._subscribers)
        # Bridge onto the canonical durable event source.
        try:
            _canonicalize(event_type, dict(data or {}))
        except Exception:
            # The bridge must never break the interactive event path.
            pass
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                pass
        return event

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._events))[: max(0, int(limit))]


# Shared process-level event bus used by the FastAPI gateway.
dashboard_event_bus = DashboardEventBus()


def publish(event_type: str, data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return dashboard_event_bus.publish(event_type, data)
