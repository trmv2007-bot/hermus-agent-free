"""Phase 14 conversational control plane.

Conversation sessions sit above the canonical runtime. They provide bounded
follow-up context, active-run steering/cancellation, and durable notification
metadata without creating a second execution engine.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from .run_events import run_bus


@dataclass
class ConversationTurn:
    role: str
    text: str
    ts: float = field(default_factory=time.time)
    run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConversationSession:
    session_id: str
    user_id: str = "anonymous"
    platform: str = "api"
    turns: deque[ConversationTurn] = field(default_factory=lambda: deque(maxlen=30))
    active_run_id: str | None = None
    notifications: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=50))
    interrupted: bool = False
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "platform": self.platform,
            "turns": [t.to_dict() for t in self.turns],
            "active_run_id": self.active_run_id,
            "notifications": list(self.notifications),
            "interrupted": self.interrupted,
            "updated_at": self.updated_at,
        }


class ConversationManager:
    """Thread-safe conversational state and run-control facade."""

    def __init__(self, *, max_sessions: int = 200):
        self._sessions: dict[str, ConversationSession] = {}
        self._lock = threading.RLock()
        self._max_sessions = max(20, int(max_sessions))
        self._sink_remove = run_bus.add_sink(self._on_run_event)

    def get_or_create(self, session_id: str | None = None, *, user_id: str = "anonymous", platform: str = "api") -> ConversationSession:
        sid = str(session_id or "").strip() or f"conv_{uuid.uuid4().hex[:10]}"
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                if len(self._sessions) >= self._max_sessions:
                    oldest = min(self._sessions.values(), key=lambda s: s.updated_at)
                    self._sessions.pop(oldest.session_id, None)
                session = ConversationSession(session_id=sid, user_id=str(user_id), platform=str(platform))
                self._sessions[sid] = session
            else:
                if user_id and session.user_id == "anonymous":
                    session.user_id = str(user_id)
                if platform:
                    session.platform = str(platform)
            session.updated_at = time.time()
            return session

    def add_turn(self, session_id: str, role: str, text: str, *, run_id: str | None = None) -> dict[str, Any]:
        session = self.get_or_create(session_id)
        value = str(text or "").strip()
        if not value:
            return session.snapshot()
        with self._lock:
            session.turns.append(ConversationTurn(str(role), value[:4000], run_id=run_id))
            session.updated_at = time.time()
        return session.snapshot()

    def context(self, session_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
        session = self.get_or_create(session_id)
        with self._lock:
            return [t.to_dict() for t in list(session.turns)[-max(1, min(30, int(limit))):]]

    def attach_run(self, session_id: str, run_id: str) -> dict[str, Any]:
        session = self.get_or_create(session_id)
        with self._lock:
            session.active_run_id = str(run_id)
            session.interrupted = False
            session.updated_at = time.time()
        run_bus.publish(run_id, "conversation_attached", {"session_id": session_id})
        return session.snapshot()

    def steer(self, session_id: str, instruction: str) -> dict[str, Any]:
        session = self.get_or_create(session_id)
        run_id = session.active_run_id
        if not run_id:
            return {"ok": False, "error": "no_active_run", "session_id": session_id}
        ok = run_bus.steer(run_id, instruction)
        if ok:
            self.add_turn(session_id, "steering", instruction, run_id=run_id)
            return {"ok": True, "session_id": session_id, "run_id": run_id, "action": "steer"}
        return {"ok": False, "error": "run_not_active", "session_id": session_id, "run_id": run_id}

    def interrupt(self, session_id: str, *, reason: str = "user_interrupt") -> dict[str, Any]:
        session = self.get_or_create(session_id)
        run_id = session.active_run_id
        if not run_id:
            session.interrupted = True
            return {"ok": True, "session_id": session_id, "action": "interrupt", "run_id": None}
        cancelled = run_bus.cancel(run_id)
        with self._lock:
            session.interrupted = True
            session.updated_at = time.time()
        return {
            "ok": bool(cancelled),
            "session_id": session_id,
            "run_id": run_id,
            "action": "interrupt",
            "reason": str(reason)[:200],
        }

    def notifications(self, session_id: str, *, consume: bool = False) -> list[dict[str, Any]]:
        session = self.get_or_create(session_id)
        with self._lock:
            items = list(session.notifications)
            if consume:
                session.notifications.clear()
            return items

    def snapshot(self, session_id: str) -> dict[str, Any]:
        return self.get_or_create(session_id).snapshot()

    def _on_run_event(self, run_id: str, event: dict[str, Any]) -> None:
        if event.get("type") not in {"run_finished", "run_error", "cancel_requested", "steer_applied"}:
            return
        data = event.get("data") or {}
        with self._lock:
            sessions = [s for s in self._sessions.values() if s.active_run_id == run_id]
            for session in sessions:
                if event.get("type") == "run_finished":
                    session.active_run_id = None
                if event.get("type") in {"run_finished", "run_error", "cancel_requested"}:
                    session.notifications.append({
                        "run_id": run_id,
                        "type": event.get("type"),
                        "data": dict(data),
                        "ts": event.get("ts"),
                    })
                session.updated_at = time.time()


conversation_manager = ConversationManager()

__all__ = ["ConversationTurn", "ConversationSession", "ConversationManager", "conversation_manager"]
