"""Live gateway event bus for the Hermus dashboard and Talking Mode.

Unlike the computer-agent journal (``core.computer.events``), this bus covers
short-lived gateway interactions: a user submits a directive, an agent starts
or finishes, speech is synthesized, and the UI changes state.  The bus is
intentionally dependency-free and in-memory; durable task history remains in
the existing task tracker and computer task store.
"""
from __future__ import annotations

import threading
import uuid
from collections import deque
from datetime import datetime
from typing import Any, Optional
from collections.abc import Callable


class DashboardEventBus:
    """Small thread-safe publish/subscribe bus with a recent-event snapshot."""

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
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                # Telemetry must never interrupt an agent request.
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
