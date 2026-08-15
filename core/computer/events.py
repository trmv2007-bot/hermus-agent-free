"""Live event bus for the Hermus Computer Agent.

Computer-agent processes (CLI, gateway, tests) publish structured events here.
Every publish is mirrored to a small JSONL journal under ``data/recordings`` so
the dashboard's WebSocket endpoint can tail live activity from *another*
process (e.g. ``hermus computer run`` while the gateway/dashboard is open).

Event types (stable, documented for consumers):
    task_started      - a task began ({task_id, task, states, source})
    plan_created      - the executable plan graph is ready
    state_changed     - the state machine moved between plan states
    screen_event      - a before/after screen sample was captured
    action_started    - a plan action begins ({state, attempt, action})
    action_completed  - a plan action finished ({state, attempt, outcome, ...})
    verification_started / verification_completed - visual verification lifecycle
    repair_started    - a failure was diagnosed and a repair planned
    repair_completed  - a repair step executed and was verified
    skill_recalled    - a learned skill was selected for this plan
    checkpoint_saved  - crash-safe task state was persisted
    task_completed    - task ended successfully (final summary payload)
    task_failed       - task ended in failure
    task_interrupted  - task halted by interrupt/exception
    world_changed     - the shared WorldState snapshot changed
    emergency_stop    - emergency stop engaged/released

``verification`` remains as a compatibility alias for older consumers.

The bus is dependency-free (stdlib only) and safe to import from anywhere.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

_EVENT_TYPES = {
    "task_started", "plan_created", "state_changed", "screen_event",
    "action_started", "action_completed", "verification_started",
    "verification_completed", "verification", "repair_started",
    "repair_completed", "skill_recalled", "checkpoint_saved",
    "task_completed", "task_failed", "task_interrupted", "world_changed",
    "emergency_stop",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _default_journal_path() -> Path:
    root = Path(__file__).resolve().parents[2] / "data" / "recordings"
    return root / ".events.jsonl"


class ComputerEventBus:
    """Thread-safe pub/sub bus with an on-disk journal for cross-process tails."""

    def __init__(self, journal_path: Optional[str] = None, max_journal_bytes: int = 16 * 1024 * 1024):
        self.journal_path = Path(journal_path).expanduser().resolve() if journal_path else _default_journal_path()
        self.max_journal_bytes = int(max_journal_bytes)
        self._lock = threading.RLock()
        self._subscribers: List[Callable[[Dict[str, Any]], None]] = []
        self._recent: Deque[Dict[str, Any]] = deque(maxlen=500)
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # -- publishing -----------------------------------------------------
    def publish(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        event = {
            "id": uuid.uuid4().hex,
            "type": event_type,
            "ts": _now(),
            "data": dict(data or {}),
        }
        if event_type not in _EVENT_TYPES:
            event["type"] = "custom:" + str(event_type)
        with self._lock:
            self._recent.append(event)
            for subscriber in list(self._subscribers):
                try:
                    subscriber(event)
                except Exception:  # noqa: BLE001 - telemetry must never break agents
                    pass
        self._append_journal(event)
        return event

    def _append_journal(self, event: Dict[str, Any]) -> None:
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            if self.journal_path.exists() and self.journal_path.stat().st_size > self.max_journal_bytes:
                self._rotate_journal()
            line = json.dumps(event, default=str) + "\n"
            with self.journal_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
        except OSError:
            pass

    def _rotate_journal(self) -> None:
        """Keep the journal bounded: retain the most recent lines."""
        try:
            lines = self.journal_path.read_text(encoding="utf-8").splitlines()
            kept = lines[-1000:]
            temporary = self.journal_path.with_suffix(".jsonl.tmp")
            temporary.write_text("\n".join(kept) + "\n", encoding="utf-8")
            temporary.replace(self.journal_path)
        except OSError:
            pass

    def subscribe(self, callback: Callable[[Dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    # -- reading --------------------------------------------------------
    def recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Most recent events, newest first. Falls back to memory if journaling is off."""
        with self._lock:
            memory = list(self._recent)
        journal = self.read_journal(limit=limit, reverse=True)
        if journal:
            return journal
        return list(reversed(memory))[: limit]

    def read_journal(self, limit: int = 200, reverse: bool = False) -> List[Dict[str, Any]]:
        """Read events from the journal file (oldest first by default)."""
        try:
            if not self.journal_path.exists():
                return []
            lines = self.journal_path.read_text(encoding="utf-8").splitlines()
            lines = lines[-int(limit):]
            events: List[Dict[str, Any]] = []
            for line in lines:
                try:
                    event = json.loads(line)
                    if isinstance(event, dict) and event.get("type"):
                        events.append(event)
                except (ValueError, TypeError):
                    continue
            if reverse:
                events.reverse()
            return events
        except OSError:
            return []

    def journal_lines(self) -> List[str]:
        try:
            if not self.journal_path.exists():
                return []
            return self.journal_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []


computer_event_bus = ComputerEventBus()


def publish(event_type: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Publish an event on the global computer event bus."""
    return computer_event_bus.publish(event_type, data)


def _action_label(action: Any) -> str:
    if not isinstance(action, dict):
        return str(action or "")
    kind = str(action.get("kind") or action.get("action") or "")
    target = str(
        action.get("target")
        or action.get("name")
        or action.get("text")
        or action.get("condition")
        or action.get("key")
        or ""
    )
    return f"{kind} {target}".strip() or str(action.get("description") or kind)


def machine_event(
    event: Dict[str, Any],
    task_id: str = "",
    task: str = "",
    emit_lifecycle_starts: bool = True,
) -> List[Dict[str, Any]]:
    """Translate one durable state-machine trace event into public bus events.

    ``ComputerAgent`` emits true before-action/before-verification lifecycle
    events and passes ``emit_lifecycle_starts=False`` here.  The default keeps
    this adapter useful on its own and backwards compatible.
    """
    if not isinstance(event, dict):
        return []
    phase = str(event.get("phase") or "")
    state = str(event.get("state") or "")
    context = {"task_id": task_id, "task": task, "state": state, "phase": phase}
    published: List[Dict[str, Any]] = []

    if phase == "transition":
        published.append(publish("state_changed", {
            **context,
            "next_state": event.get("next_state"),
            "outcome": event.get("outcome"),
            "failure_reason": event.get("failure_reason"),
        }))
    elif phase == "original_action":
        attempt = int(event.get("attempt", 1) or 1)
        action = event.get("action") or event.get("action_spec") or {}
        action_label = _action_label(event.get("action_spec") or action)
        if emit_lifecycle_starts:
            published.append(publish("action_started", {
                **context,
                "attempt": attempt,
                "action": action_label,
                "spec": event.get("action_spec"),
            }))
        outcome = event.get("outcome")
        if emit_lifecycle_starts and outcome in ("success", "failure"):
            published.append(publish("action_completed", {
                **context,
                "attempt": attempt,
                "outcome": outcome,
                "action": action_label,
                "detail": event.get("failure_reason") or event.get("action", {}).get("detail", ""),
                "ok": outcome == "success",
                "expected": event.get("expected"),
            }))
        verification = event.get("verification")
        if emit_lifecycle_starts and isinstance(verification, dict) and "ok" in verification:
            payload = {
                **context,
                "phase": "action_verification",
                "ok": bool(verification.get("ok")),
                "matched": bool(verification.get("matched", verification.get("ok"))),
                "confidence": verification.get("confidence", 0.0),
                "detail": verification.get("detail", ""),
                "expected": event.get("expected", ""),
            }
            published.append(publish("verification_completed", payload))
            published.append(publish("verification", payload))
    elif phase == "diagnose":
        diagnosis = event.get("diagnosis") if isinstance(event.get("diagnosis"), dict) else {}
        plan = event.get("repair_plan") if isinstance(event.get("repair_plan"), dict) else {}
        published.append(publish("repair_started", {
            **context,
            "attempt": event.get("attempt"),
            "kind": diagnosis.get("kind", "unknown"),
            "summary": diagnosis.get("summary", ""),
            "confidence": diagnosis.get("confidence", 0.0),
            "plan_id": plan.get("plan_id", ""),
            "steps": len(plan.get("steps") or []),
            "repair_available": bool(plan.get("available")),
            "failure_reason": event.get("failure_reason"),
        }))
    elif phase == "repair":
        action = event.get("action") or event.get("action_spec") or {}
        published.append(publish("repair_completed", {
            **context,
            "repair_state": event.get("repair_state"),
            "repair_for": event.get("repair_for"),
            "plan_id": event.get("repair_plan_id"),
            "attempt": event.get("attempt"),
            "outcome": event.get("outcome"),
            "ok": event.get("outcome") == "success",
            "action": _action_label(event.get("action_spec") or action),
            "expected": event.get("expected"),
            "failure_reason": event.get("failure_reason"),
        }))
        verification = event.get("verification")
        if emit_lifecycle_starts and isinstance(verification, dict) and "ok" in verification:
            payload = {
                **context,
                "phase": "repair_verification",
                "ok": bool(verification.get("ok")),
                "confidence": verification.get("confidence", 0.0),
                "detail": verification.get("detail", ""),
                "expected": event.get("expected", ""),
            }
            published.append(publish("verification_completed", payload))
            published.append(publish("verification", payload))
    # The terminal phase is intentionally not published here: ComputerAgent
    # emits the authoritative task_completed/task_failed (with duration, action
    # counts, and recording path) after artifacts are written, and emits
    # task_interrupted on interrupt/crash paths. Publishing both would create
    # duplicate completion events in the dashboard feed.
    return published
