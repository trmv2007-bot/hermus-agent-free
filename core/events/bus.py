"""The canonical EventBus.

A single bus that:
* accepts one :class:`core.contracts.EventEnvelope`,
* persists to a durable append-only JSONL event log (so restarts don't erase truth),
* exposes subscribe (in-process) and replay (from log) APIs,
* returns a monotonic cursor so a UI can resume from ``since_cursor``.

Design: the backend is authoritative; the UI state is a projection of
snapshot + replay(events). The bus never fabricates state.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from ..contracts import EventEnvelope

# The set of event types a subscriber can filter on (None = everything).
Subscriber = Callable[[EventEnvelope], None]


class EventBus:
    """In-process + durable event bus with subscription and replay."""

    def __init__(self, log_path: Optional[os.PathLike] = None, *, max_memory: int = 20000):
        self._lock = threading.RLock()
        self._subscribers: list[tuple[Optional[str], Subscriber]] = []
        self._buffer: list[EventEnvelope] = []
        self._cursor = 0
        self._max_memory = max_memory
        self._log_path = Path(log_path) if log_path else None
        self._log_fh = None
        if self._log_path:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            # Open in append mode; existing lines are the durable history.
            self._log_fh = open(self._log_path, "a", encoding="utf-8")
            self._cursor = self._count_lines(self._log_path)

    # -- persistence ---------------------------------------------------------
    @staticmethod
    def _count_lines(path: Path) -> int:
        if not path.exists():
            return 0
        n = 0
        with open(path, "r", encoding="utf-8") as fh:
            for _ in fh:
                n += 1
        return n

    def _write_log(self, envelope: EventEnvelope) -> None:
        if self._log_fh is None:
            return
        try:
            self._log_fh.write(json.dumps(envelope.to_dict()) + "\n")
            self._log_fh.flush()
        except Exception:
            # Logging must never break the event path.
            pass

    # -- publish / subscribe ---------------------------------------------------
    def publish(self, envelope: EventEnvelope) -> EventEnvelope:
        """Publish one envelope, persist it, and fan out to subscribers."""
        with self._lock:
            self._buffer.append(envelope)
            self._cursor += 1
            if len(self._buffer) > self._max_memory:
                self._buffer = self._buffer[-self._max_memory:]
            self._write_log(envelope)
            subscribers = list(self._subscribers)
        for match, cb in subscribers:
            if match is None or match == envelope.type:
                try:
                    cb(envelope)
                except Exception:
                    # A broken subscriber must not break the system.
                    pass
        return envelope

    def subscribe(self, event_type: Optional[str] = None) -> Callable[[Subscriber], Subscriber]:
        """Decorator/registration helper.

        ``event_type`` is a wildcard when ``None``; otherwise the subscriber only
        receives events whose ``type`` matches exactly.
        """
        def register(cb: Subscriber) -> Subscriber:
            with self._lock:
                self._subscribers.append((event_type, cb))
            return cb
        return register

    def unsubscribe(self, cb: Subscriber) -> None:
        with self._lock:
            self._subscribers = [(t, c) for (t, c) in self._subscribers if c is not cb]

    # -- replay / snapshot ------------------------------------------------------
    @property
    def cursor(self) -> int:
        return self._cursor

    def replay(self, since_cursor: int = 0, *, event_type: Optional[str] = None,
               limit: Optional[int] = None) -> list[EventEnvelope]:
        """Replay events with cursor > ``since_cursor`` from the buffer + log.

        If a durable log exists, events are read from disk so replay survives a
        restart; otherwise the in-memory buffer is used.
        """
        out: list[EventEnvelope] = []
        if self._log_path and self._log_path.exists():
            with open(self._log_path, "r", encoding="utf-8") as fh:
                idx = 0
                for line in fh:
                    idx += 1
                    if idx <= since_cursor:
                        continue
                    try:
                        env = EventEnvelope.from_dict(json.loads(line))
                    except Exception:
                        continue
                    if event_type and env.type != event_type:
                        continue
                    out.append(env)
                    if limit and len(out) >= limit:
                        break
        else:
            for env in self._buffer:
                i = getattr(env, "_cursor", None)
                # buffer replay keyed by cursor ordering is approximate without log
                out.append(env)
                if limit and len(out) >= limit:
                    break
        return out

    def snapshot(self) -> list[EventEnvelope]:
        """Return the current in-memory buffer (recent events)."""
        with self._lock:
            return list(self._buffer)

    def recent(self, limit: int = 100, *, event_type: Optional[str] = None) -> list[EventEnvelope]:
        envs = self.replay(since_cursor=0, event_type=event_type, limit=limit)
        return envs[-limit:]

    def close(self) -> None:
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None


_bus: Optional[EventBus] = None
_bus_lock = threading.Lock()


def get_bus() -> EventBus:
    """Return the process-wide canonical bus."""
    global _bus
    with _bus_lock:
        if _bus is None:
            # Default to an in-memory bus; the gateway/main entry wires a durable log.
            _bus = EventBus()
        return _bus


def configure_bus(log_path: Optional[os.PathLike] = None, *, reset: bool = False) -> EventBus:
    """Install (or reconfigure) the process-wide bus with an optional durable log.

    Call this once at bootstrap/gateway startup so the canonical bus persists its
    event log to disk (survives restart). Safe to call again to point at a new log.
    """
    global _bus
    with _bus_lock:
        if _bus is not None and not reset:
            return _bus
        if _bus is not None:
            _bus.close()
        _bus = EventBus(log_path)
        return _bus


def publish(event: Any) -> EventEnvelope:
    """Publish an envelope (or dict that can be coerced into one) to the bus."""
    if isinstance(event, EventEnvelope):
        env = event
    elif isinstance(event, dict):
        env = EventEnvelope.from_dict(event)
    else:
        raise TypeError("publish() expects an EventEnvelope or a dict")
    return get_bus().publish(env)
