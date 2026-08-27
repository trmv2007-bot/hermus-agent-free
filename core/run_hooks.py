"""Event-emitter helpers shared by the agent loop and the gateway.

The agent must never *depend* on a subscriber: emitting is best-effort, cheap and
exception-proof, so an SSE/WebSocket client vanishing mid-run cannot break a task.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional


def make_emitter(on_event: Optional[Callable[[str, Dict[str, Any]], None]]) -> Callable[..., None]:
    """Return ``emit(type, **data)``; a no-op when nobody is listening."""
    if on_event is None:
        return lambda *a, **k: None

    def emit(event_type: str, data: Optional[Dict[str, Any]] = None, **extra: Any) -> None:
        payload = dict(data or {})
        payload.update(extra)
        payload.setdefault("ts", time.time())
        try:
            on_event(str(event_type), payload)
        except Exception:
            pass  # telemetry must never interrupt the agent

    return emit


class CancelToken:
    """Cooperative cancellation: long loops poll ``cancelled`` (cheap)."""

    def __init__(self, check: Optional[Callable[[], bool]] = None, interval: float = 0.2):
        self._check = check
        self._flag = False
        self._interval = float(interval)
        self._last = 0.0

    def cancel(self) -> None:
        self._flag = True

    @property
    def cancelled(self) -> bool:
        if self._flag:
            return True
        if self._check is None:
            return False
        now = time.time()
        if now - self._last < self._interval:
            return False
        self._last = now
        try:
            self._flag = bool(self._check())
        except Exception:
            return False
        return self._flag

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise CancelledRun("run cancelled")


class CancelledRun(RuntimeError):
    """Raised inside the agent loop when cancellation is observed."""
