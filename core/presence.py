"""Hermus presence: a small, durable continuity layer for the agent.

Presence is deliberately different from the model, memory and world state:

* **identity** is the stable, user-editable description of who Hermus is;
* **presence** is the current operational state (idle, thinking, working, ...);
* **goals** are the user's ongoing commitments that Hermus can safely follow up;
* **moments** are short local-only continuity notes, not a second chat history.

The module gives Hermus a visible sense of continuity without pretending that an
LLM is conscious. It is safe by default: the heartbeat updates status and emits
check-in suggestions, but it never starts model calls or performs actions on its
own. Any proactive work must still go through the normal queue, permissions and
emergency-stop paths.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import config


PRESENCE_STATES = (
    "offline",
    "idle",
    "thinking",
    "working",
    "verifying",
    "learning",
    "waiting_approval",
    "error",
    "sleeping",
)

_ACTIVE_STATES = {"thinking", "working", "verifying", "learning", "waiting_approval"}

_SECRET_PATTERNS = (
    (re.compile(r"(?i)(api[_ -]?key|access[_ -]?token|password|secret|token)\s*[:=]\s*[^\s,;]+"), r"\1=[redacted]"),
    (re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_-]{12,})\b"), "[redacted-token]"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe_text(value: Any, limit: int = 400) -> str:
    """Bound local continuity text and remove obvious credential-shaped values."""
    text = " ".join(str(value or "").split())
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:limit] + ("…" if len(text) > limit else "")


def _clone(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _redact_value(value: Any, *, limit: int = 500) -> Any:
    """Copy a small JSON-shaped value while redacting credential-like strings."""
    if isinstance(value, str):
        return _safe_text(value, limit)
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:32]:
            key_text = str(key)[:80]
            if re.search(r"(?i)(password|api[_ -]?key|access[_ -]?token|secret|token|authorization|cookie)", key_text):
                result[key_text] = "[redacted]"
            else:
                result[key_text] = _redact_value(item, limit=limit)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, limit=limit) for item in list(value)[:32]]
    return _clone(value)


def _visible_to_user(item: dict[str, Any], user_id: Optional[str]) -> bool:
    """Global continuity is visible to everyone; scoped continuity is private."""
    if user_id is None:
        return True
    owner = str(item.get("user_id") or "")
    requested = str(user_id or "")
    return not owner or owner == requested


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class PresenceManager:
    """Thread-safe, atomically persisted identity/presence/goal state."""

    def __init__(self, state_path: Optional[str | os.PathLike[str]] = None):
        raw = state_path or getattr(config, "presence_state_path", "data/presence.json")
        path = Path(os.path.expanduser(str(raw)))
        if not path.is_absolute():
            path = config.resolve_path(str(path))
        self.path = path
        self._lock = threading.RLock()
        self._data = self._defaults()
        self._load()

    # ------------------------------------------------------------------ storage
    @staticmethod
    def _defaults() -> dict[str, Any]:
        now = _now()
        return {
            "schema_version": 1,
            "identity": {
                "name": "Hermus",
                "role": "a local-first AI partner for coding, research and automation",
                "tone": "warm, concise, honest and proactive only within approved boundaries",
                "values": ["honesty", "continuity", "curiosity", "user control"],
                "greeting": "I'm here. What should we work on?",
                "created_at": now,
                "updated_at": now,
            },
            "presence": {
                "state": "idle",
                "detail": "ready",
                "session_id": None,
                "run_id": None,
                "active_goal": None,
                "last_goal": None,
                "last_result": None,
                "last_error": None,
                "changed_at": now,
                "last_seen": now,
                "last_heartbeat": None,
                "heartbeat_count": 0,
            },
            "goals": [],
            "moments": [],
        }

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            with self._lock:
                for key in ("identity", "presence"):
                    if isinstance(raw.get(key), dict):
                        self._data[key].update(raw[key])
                if isinstance(raw.get("goals"), list):
                    self._data["goals"] = raw["goals"][-100:]
                if isinstance(raw.get("moments"), list):
                    self._data["moments"] = raw["moments"][-80:]
                self._data["schema_version"] = int(raw.get("schema_version", 1) or 1)
                # A process cannot still be thinking after a restart. Preserve
                # the fact that it resumed, but never display stale active work.
                if self._data["presence"].get("state") in _ACTIVE_STATES:
                    self._data["presence"].update({
                        "state": "idle",
                        "detail": "resumed after restart",
                        "session_id": None,
                        "run_id": None,
                        "active_goal": None,
                        "changed_at": _now(),
                        "last_seen": _now(),
                    })
        except (OSError, ValueError, TypeError):
            # Presence is helpful state, never a reason for Hermus not to boot.
            return

    def _save_locked(self) -> None:
        """Atomically replace the state file so a crash cannot leave half JSON."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            # The runtime can keep an in-memory presence if the disk is read-only.
            try:
                if 'tmp' in locals() and tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ events
    def _emit(self, event_kind: str, payload: dict[str, Any], *, actor: str = "agent") -> None:
        """Publish a canonical, redacted presence event without breaking a turn."""
        try:
            from .contracts import EventEnvelope
            from .events import get_bus

            data = _redact_value(payload or {}, limit=500)
            with self._lock:
                state = _clone(self._data.get("presence", {}))
                identity = _clone(self._data.get("identity", {}))
            env = EventEnvelope(
                run_id=state.get("run_id") or None,
                session_id=state.get("session_id") or "default",
                actor=actor,
                source="presence",
                type=f"presence.{event_kind}",
                command=f"presence.{event_kind}",
                target=identity.get("name") or "Hermus",
                args_redacted=data,
                status=str(state.get("state") or "idle"),
            )
            get_bus().publish(env)
        except Exception:
            # Presence is observability/continuity; it must never break the agent.
            pass

    # ------------------------------------------------------------------ snapshots
    def snapshot(self, *, user_id: Optional[str] = None) -> dict[str, Any]:
        """Return a copy of presence, optionally scoped to one continuity owner."""
        with self._lock:
            data = _clone(self._data)
            if user_id is not None:
                data["goals"] = [g for g in data.get("goals", []) if _visible_to_user(g, user_id)]
                data["moments"] = [m for m in data.get("moments", []) if _visible_to_user(m, user_id)]
            due = self._check_ins_due_locked(user_id=user_id)
            data["check_ins_due"] = due
            data["heartbeat"] = {
                "enabled": bool(getattr(config, "presence_enabled", True)),
                "interval_seconds": int(getattr(config, "presence_heartbeat_seconds", 30)),
                "last_at": data["presence"].get("last_heartbeat"),
                "count": int(data["presence"].get("heartbeat_count") or 0),
            }
            data["active_goal_count"] = sum(1 for g in data.get("goals", []) if g.get("status") == "active")
            return data

    def identity(self) -> dict[str, Any]:
        with self._lock:
            return _clone(self._data["identity"])

    def current(self) -> dict[str, Any]:
        with self._lock:
            return _clone(self._data["presence"])

    # ---------------------------------------------------------------- identity
    def update_identity(
        self,
        *,
        name: Optional[str] = None,
        role: Optional[str] = None,
        tone: Optional[str] = None,
        values: Optional[list[str]] = None,
        greeting: Optional[str] = None,
    ) -> dict[str, Any]:
        """Update only user-editable identity fields; returns the new identity."""
        with self._lock:
            identity = self._data["identity"]
            if name is not None and str(name).strip():
                identity["name"] = _safe_text(name, 80)
            if role is not None:
                identity["role"] = _safe_text(role, 240)
            if tone is not None:
                identity["tone"] = _safe_text(tone, 240)
            if values is not None:
                identity["values"] = [_safe_text(v, 80) for v in values if str(v).strip()][:12]
            if greeting is not None:
                identity["greeting"] = _safe_text(greeting, 240)
            identity["updated_at"] = _now()
            self._save_locked()
            result = _clone(identity)
        self._emit("identity_changed", result, actor="user")
        return result

    # --------------------------------------------------------------- transitions
    def set_state(
        self,
        state: str,
        *,
        detail: str = "",
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        goal: Optional[str] = None,
        last_result: Optional[str] = None,
        last_error: Optional[str] = None,
        force_event: bool = False,
    ) -> dict[str, Any]:
        state = str(state or "idle").strip().lower()
        if state not in PRESENCE_STATES:
            state = "idle"
        with self._lock:
            current = self._data["presence"]
            old_state = current.get("state")
            changed = old_state != state or bool(detail)
            current["state"] = state
            if detail:
                current["detail"] = _safe_text(detail, 240)
            if session_id is not None:
                current["session_id"] = session_id or None
            if run_id is not None:
                current["run_id"] = run_id or None
            if goal is not None:
                current["active_goal"] = _safe_text(goal, 240) if goal else None
                if goal:
                    current["last_goal"] = _safe_text(goal, 240)
            if last_result is not None:
                current["last_result"] = _safe_text(last_result, 500) if last_result else None
            if last_error is not None:
                current["last_error"] = _safe_text(last_error, 500) if last_error else None
            current["last_seen"] = _now()
            if changed:
                current["changed_at"] = current["last_seen"]
            self._save_locked()
            result = _clone(current)
        if changed or force_event:
            self._emit("changed", result)
        return result

    def touch(self, *, detail: Optional[str] = None) -> dict[str, Any]:
        with self._lock:
            state = self._data["presence"]
            state["last_seen"] = _now()
            if detail is not None:
                state["detail"] = _safe_text(detail, 240)
            self._save_locked()
            return _clone(state)

    def begin_turn(self, goal: str, *, session_id: str = "", run_id: str = "", user_id: str = "") -> dict[str, Any]:
        detail = "listening" if not goal else f"focusing on {_safe_text(goal, 150)}"
        state = self.set_state(
            "thinking", detail=detail, session_id=session_id, run_id=run_id,
            goal=goal,
        )
        self.record_moment(
            "focus_started",
            f"Started working on {_safe_text(goal, 220) or 'a new request'}",
            session_id=session_id, run_id=run_id, user_id=user_id,
            metadata={"user_id": _safe_text(user_id, 80)} if user_id else None,
            emit=False,
        )
        return state

    def activity(self, state: str, *, detail: str = "", session_id: str = "", run_id: str = "", goal: str = "") -> dict[str, Any]:
        return self.set_state(
            state, detail=detail, session_id=session_id or None, run_id=run_id or None,
            goal=goal or None,
        )

    def finish_turn(
        self,
        *,
        goal: str,
        response: str = "",
        session_id: str = "",
        run_id: str = "",
        user_id: str = "",
        success: bool = True,
        waiting_for_approval: bool = False,
        error: str = "",
    ) -> dict[str, Any]:
        if waiting_for_approval:
            state = self.set_state(
                "waiting_approval", detail="waiting for your approval", session_id=session_id or None,
                run_id=run_id or None, goal=goal,
            )
            kind = "approval_waiting"
        else:
            detail = "ready" if success else "ready · last run failed"
            state = self.set_state(
                "idle", detail=detail, session_id=session_id or None,
                run_id=None, goal=None, last_result=response,
                last_error=error if not success else None,
            )
            kind = "turn_completed" if success else "turn_failed"
        summary = _safe_text(response or error or "No response generated", 360)
        self.record_moment(
            kind,
            f"{_safe_text(goal, 180)} — {summary}",
            session_id=session_id, run_id=run_id, user_id=user_id, emit=False,
        )
        return state

    # ------------------------------------------------------------------ moments
    def record_moment(
        self,
        kind: str,
        summary: str,
        *,
        session_id: str = "",
        run_id: str = "",
        user_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
        emit: bool = True,
    ) -> dict[str, Any]:
        moment = {
            "id": _id("moment"),
            "kind": _safe_text(kind, 60),
            "summary": _safe_text(summary, 420),
            "at": _now(),
            "session_id": session_id or None,
            "run_id": run_id or None,
            "user_id": _safe_text(user_id, 80) or None,
            "metadata": _redact_value(metadata or {}, limit=500),
        }
        with self._lock:
            self._data["moments"].append(moment)
            self._data["moments"] = self._data["moments"][-80:]
            self._save_locked()
        if emit:
            self._emit("moment", moment)
        return moment

    # -------------------------------------------------------------------- goals
    def add_goal(
        self,
        title: str,
        *,
        priority: int = 3,
        due_at: Optional[str] = None,
        source: str = "user",
        notes: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        title = _safe_text(title, 240)
        if not title:
            return {"success": False, "error": "goal title required"}
        try:
            priority = max(1, min(5, int(priority)))
        except (TypeError, ValueError):
            priority = 3
        now = _now()
        goal = {
            "id": _id("goal"),
            "title": title,
            "status": "active",
            "priority": priority,
            "due_at": _safe_text(due_at, 80) if due_at else None,
            "source": _safe_text(source, 80),
            "notes": _safe_text(notes, 300),
            "user_id": _safe_text(user_id, 80) or None,
            "created_at": now,
            "updated_at": now,
            "last_touched_at": now,
            "last_checkin_at": None,
            "checkin_count": 0,
        }
        with self._lock:
            self._data["goals"].append(goal)
            self._data["goals"] = self._data["goals"][-100:]
            self._save_locked()
        self.record_moment("goal_added", f"New ongoing goal: {title}", user_id=user_id, emit=False)
        self._emit("goal_added", goal, actor="user")
        return {"success": True, "goal": _clone(goal)}

    def list_goals(self, status: Optional[str] = None, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            goals = _clone(self._data["goals"])
        if status:
            goals = [g for g in goals if g.get("status") == status]
        if user_id is not None:
            goals = [g for g in goals if _visible_to_user(g, user_id)]
        return list(reversed(goals))

    def complete_goal(self, goal_id: str, *, note: str = "") -> dict[str, Any]:
        with self._lock:
            found = None
            for goal in self._data["goals"]:
                if goal.get("id") == str(goal_id):
                    goal["status"] = "completed"
                    goal["updated_at"] = _now()
                    goal["completed_at"] = goal["updated_at"]
                    if note:
                        goal["notes"] = _safe_text(note, 300)
                    found = _clone(goal)
                    break
            if found is None:
                return {"success": False, "error": f"goal '{goal_id}' not found"}
            self._save_locked()
        self.record_moment(
            "goal_completed", f"Completed goal: {found['title']}",
            user_id=str(found.get("user_id") or ""), emit=False,
        )
        self._emit("goal_completed", found, actor="user")
        return {"success": True, "goal": found}

    def touch_goal(self, goal_id: str) -> dict[str, Any]:
        with self._lock:
            for goal in self._data["goals"]:
                if goal.get("id") == str(goal_id):
                    goal["last_touched_at"] = _now()
                    goal["updated_at"] = goal["last_touched_at"]
                    self._save_locked()
                    return {"success": True, "goal": _clone(goal)}
        return {"success": False, "error": f"goal '{goal_id}' not found"}

    def mark_checkin(self, goal_id: str) -> dict[str, Any]:
        with self._lock:
            for goal in self._data["goals"]:
                if goal.get("id") == str(goal_id):
                    now = _now()
                    goal["last_checkin_at"] = now
                    goal["checkin_count"] = int(goal.get("checkin_count") or 0) + 1
                    goal["updated_at"] = now
                    self._save_locked()
                    result = _clone(goal)
                    break
            else:
                return {"success": False, "error": f"goal '{goal_id}' not found"}
        self._emit("goal_checkin", result)
        return {"success": True, "goal": result}

    def _check_ins_due_locked(self, *, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        try:
            after_minutes = max(1, int(getattr(config, "presence_checkin_after_minutes", 240)))
        except (TypeError, ValueError):
            after_minutes = 240
        now = datetime.now(timezone.utc)
        due = []
        for goal in self._data.get("goals", []):
            if goal.get("status") != "active":
                continue
            if not _visible_to_user(goal, user_id):
                continue
            anchor = goal.get("last_checkin_at") or goal.get("last_touched_at") or goal.get("created_at")
            try:
                age = (now - datetime.fromisoformat(str(anchor).replace("Z", "+00:00"))).total_seconds() / 60
            except (TypeError, ValueError):
                age = 0
            if age >= after_minutes:
                due.append({
                    "id": goal.get("id"),
                    "title": goal.get("title"),
                    "priority": goal.get("priority", 3),
                    "age_minutes": round(age, 1),
                    "due_at": goal.get("due_at"),
                })
        return sorted(due, key=lambda x: (-int(x.get("priority") or 0), -float(x.get("age_minutes") or 0)))

    def check_ins_due(self, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            return self._check_ins_due_locked(user_id=user_id)

    # ---------------------------------------------------------------- heartbeat
    def heartbeat(self, *, force_event: bool = False) -> dict[str, Any]:
        """Record a beat and return the current continuity snapshot.

        Heartbeats are intentionally side-effect-light. They update presence,
        emit an occasional canonical event and expose due check-ins. They never
        call an LLM, execute a tool or bypass permissions.
        """
        with self._lock:
            now = _now()
            state = self._data["presence"]
            state["last_heartbeat"] = now
            state["last_seen"] = now
            state["heartbeat_count"] = int(state.get("heartbeat_count") or 0) + 1
            due = self._check_ins_due_locked()
            if state.get("state") == "idle":
                state["detail"] = f"ready · {len(due)} check-in(s) due" if due else "ready"
            self._save_locked()
            beat = int(state["heartbeat_count"])
            payload = {
                "state": state.get("state"),
                "detail": state.get("detail"),
                "heartbeat_count": beat,
                "check_ins_due": due[:12],
                "last_heartbeat": now,
            }
        every = max(1, int(getattr(config, "presence_event_every", 5) or 5))
        if force_event or beat == 1 or beat % every == 0 or due:
            self._emit("heartbeat", payload, actor="system")
        return self.snapshot()

    # -------------------------------------------------------------- prompt text
    def prompt_block(self, *, session_id: str = "", user_id: str = "") -> str:
        """Stable identity + short continuity context for the model prompt."""
        snap = self.snapshot(user_id=user_id or None)
        identity = snap.get("identity") or {}
        current = snap.get("presence") or {}
        goals = [g for g in snap.get("goals", []) if g.get("status") == "active"][:5]
        moments = list(reversed(snap.get("moments") or []))[:4]
        lines = [
            "Hermus identity and continuity (local operational state):",
            f"- Name: {identity.get('name') or 'Hermus'}",
            f"- Role: {identity.get('role') or 'AI partner'}",
            f"- Tone: {identity.get('tone') or 'warm and honest'}",
            f"- Values: {', '.join(identity.get('values') or [])}",
            f"- Current state: {current.get('state', 'idle')} — {current.get('detail', 'ready')}",
        ]
        if goals:
            lines.append("- Ongoing user-approved goals:")
            lines.extend(f"  - {g.get('title')} (priority {g.get('priority', 3)})" for g in goals)
        if moments:
            lines.append("- Recent continuity moments:")
            lines.extend(f"  - {m.get('summary')}" for m in moments)
        lines.extend([
            "- Speak warmly and naturally, referring back to relevant continuity when useful.",
            "- Operational state is not proof of consciousness: never claim feelings, sentience or actions you did not actually perform.",
        ])
        return "\n".join(lines)


_manager: Optional[PresenceManager] = None
_manager_lock = threading.Lock()


def get_presence() -> PresenceManager:
    """Return the process-wide canonical presence manager."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = PresenceManager()
        return _manager


presence = get_presence()

__all__ = ["PRESENCE_STATES", "PresenceManager", "get_presence", "presence"]
