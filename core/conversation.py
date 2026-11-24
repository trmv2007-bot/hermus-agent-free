"""Phase 14 conversational control plane.

Conversation sessions sit above the canonical runtime. They provide bounded
follow-up context, active-run steering/cancellation, and durable notification
metadata without creating a second execution engine.

A turn's *thinking* — the plan it followed, the receipt that proves what it
changed, the tier it was routed to and why — rides along on the assistant turn
it belongs to. The replay path (``GET /conversation/{id}``) therefore returns
what the room showed live, so a reload redraws the plan and the receipt from the
server rather than from memory in the page.
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
    #: the plan / receipt / routing record the turn carried, when it had one
    thinking: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        if out.get("thinking") is None:
            out.pop("thinking", None)
        return out


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
        #: per-run scratch for the turn's thinking, dropped when the run ends
        self._live: dict[str, dict[str, Any]] = {}
        self._sink_remove = run_bus.add_sink(self._on_run_event)

    def get_or_create(
        self, session_id: str | None = None, *, user_id: str = "anonymous", platform: str = "api"
    ) -> ConversationSession:
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
            return [t.to_dict() for t in list(session.turns)[-max(1, min(30, int(limit))) :]]

    def attach_run(self, session_id: str, run_id: str) -> dict[str, Any]:
        session = self.get_or_create(session_id)
        with self._lock:
            session.active_run_id = str(run_id)
            session.interrupted = False
            session.updated_at = time.time()
            self._listen(run_id)
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

    # ------------------------------------------------------------ the record

    def _listen(self, run_id: str) -> None:
        """Start collecting this run's thinking; bounded so a dead run cannot leak."""
        while len(self._live) >= self._max_sessions * 2:
            self._live.pop(next(iter(self._live)), None)
        self._live[str(run_id)] = {"answer": "", "thinking": {}}
        # The job is submitted before the session claims it, so the first events
        # can already be on the bus: replay them rather than lose the plan.
        for event in run_bus.history(run_id, limit=400):
            event_type = event.get("type")
            if event_type in _THINKING_EVENTS or event_type == "agent_response":
                self._absorb(run_id, event_type, event.get("data") or {})

    def _absorb(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
        """Fold one run event into the turn's thinking record, ignoring the rest."""
        with self._lock:
            live = self._live.get(run_id)
            if live is None:
                return
            thinking: dict[str, Any] = live["thinking"]
            if event_type == "agent_response":
                text = str(data.get("text") or "").strip()
                if text:
                    live["answer"] = text[:4000]
            elif event_type == "plan_ready":
                thinking["plan"] = {
                    "goal": str(data.get("goal") or "")[:300],
                    "steps": [self._step(s) for s in (data.get("steps") or [])][:8],
                }
            elif event_type == "plan_finished":
                plan = thinking.setdefault("plan", {"goal": "", "steps": []})
                plan["steps"] = [self._step(s) for s in (data.get("steps") or [])][:8]
            elif event_type == "plan_step":
                steps = (thinking.get("plan") or {}).get("steps") or []
                number = int(data.get("step") or 0)
                if 0 < number <= len(steps):
                    step = steps[number - 1]
                    step["status"] = str(data.get("status") or step.get("status") or "planned")
                    note = data.get("evidence")
                    if isinstance(note, str) and note:
                        step["evidence"] = [*step.get("evidence", []), note[:120]][-3:]
            elif event_type == "receipt":
                thinking["receipt"] = self._receipt(data)
            elif event_type == "routing_decision":
                thinking["routing"] = {
                    "tier": str(data.get("tier") or "")[:24],
                    "model": str(data.get("model") or "")[:120],
                    "reason": str(data.get("reason") or "")[:300],
                }

    @staticmethod
    def _step(step: dict[str, Any]) -> dict[str, Any]:
        """The four fields a rail needs, and nothing that reads like a log."""
        return {
            "n": int(step.get("n") or 0),
            "goal": str(step.get("goal") or "")[:200],
            "action": str(step.get("action") or "")[:24],
            "label": str(step.get("label") or step.get("action") or "")[:24],
            "status": str(step.get("status") or "pending")[:12],
        }

    @staticmethod
    def _receipt(data: dict[str, Any]) -> dict[str, Any]:
        """Keep what a person is shown: the verdict and the citable evidence."""
        evidence = []
        for item in (data.get("evidence") or [])[:4]:
            if not isinstance(item, dict):
                continue
            info = item.get("data") or {}
            evidence.append(
                {
                    "kind": str(item.get("kind") or "")[:12],
                    "ok": bool(item.get("ok")),
                    "detail": str(item.get("detail") or "")[:300],
                    "data": {
                        key: (value if isinstance(value, (int, float, bool)) else str(value)[:300])
                        for key, value in info.items()
                        if key in ("path", "sha256", "size", "command", "returncode", "url", "tool")
                    },
                }
            )
        return {
            "verified": bool(data.get("verified")),
            "verdict": str(data.get("verdict") or "")[:32],
            "changed": bool(data.get("changed")),
            "claim": str(data.get("claim") or "")[:300],
            "problems": [str(p)[:240] for p in (data.get("problems") or [])[:3]],
            "evidence": evidence,
        }

    def _on_run_event(self, run_id: str, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        data = event.get("data") or {}
        if event_type in _THINKING_EVENTS or event_type == "agent_response":
            self._absorb(run_id, event_type, data)
        if event_type not in {"run_finished", "run_error", "cancel_requested", "steer_applied"}:
            return
        with self._lock:
            live = self._live.pop(run_id, None)
            sessions = [s for s in self._sessions.values() if s.active_run_id == run_id]
            for session in sessions:
                if event_type == "run_finished":
                    session.active_run_id = None
                if event_type in {"run_finished", "run_error", "cancel_requested"}:
                    self._record_answer(session, run_id, live or {})
                    session.notifications.append(
                        {
                            "run_id": run_id,
                            "type": event_type,
                            "data": dict(data),
                            "ts": event.get("ts"),
                        }
                    )
                session.updated_at = time.time()

    def _record_answer(self, session: ConversationSession, run_id: str, live: dict[str, Any]) -> None:
        """The answer the person heard, with the plan and receipt it carried."""
        answer = str(live.get("answer") or "").strip()
        if not answer:
            return
        thinking = {key: value for key, value in (live.get("thinking") or {}).items() if value}
        session.turns.append(
            ConversationTurn(
                "assistant",
                answer[:4000],
                run_id=run_id,
                thinking=thinking or None,
            )
        )


#: the run events that carry a turn's thinking
_THINKING_EVENTS = frozenset({"plan_ready", "plan_step", "plan_finished", "receipt", "routing_decision"})


conversation_manager = ConversationManager()

__all__ = ["ConversationTurn", "ConversationSession", "ConversationManager", "conversation_manager"]
