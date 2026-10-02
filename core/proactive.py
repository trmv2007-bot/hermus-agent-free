"""Safe proactive automation layer for HERMUS.

Proactive rules are explicit, durable, and opt-in. This layer reacts to
canonical events and hands work to the existing queue/runtime; it never executes
tools directly and never bypasses approval/red-line policy.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


ALLOWED_ACTIONS = {"runtime.turn", "agent.autonomous", "mission.start"}


@dataclass
class AutomationRule:
    id: str
    name: str
    event_type: str
    action_type: str
    task: str
    enabled: bool = False
    cooldown_seconds: float = 60.0
    max_fires: int | None = None
    fire_count: int = 0
    last_fired_at: float | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProactiveAutomation:
    """Evaluate event-driven rules and enqueue bounded, authorized work."""

    def __init__(
        self,
        path: str | Path = "data/automation_rules.json",
        *,
        enqueue: Callable[[str, dict[str, Any]], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.enqueue = enqueue
        self.clock = clock
        self._lock = threading.RLock()
        self.rules: dict[str, AutomationRule] = {}
        self._load()

    def add_rule(
        self,
        *,
        name: str,
        event_type: str,
        action_type: str,
        task: str,
        enabled: bool = False,
        cooldown_seconds: float = 60.0,
        max_fires: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> AutomationRule:
        if action_type not in ALLOWED_ACTIONS:
            raise ValueError(f"unsupported proactive action: {action_type}")
        if not str(event_type).strip() or not str(task).strip():
            raise ValueError("event_type and task are required")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")
        rule = AutomationRule(
            id=f"auto_{uuid4().hex[:12]}",
            name=str(name).strip() or "automation",
            event_type=str(event_type).strip(),
            action_type=action_type,
            task=str(task).strip(),
            enabled=bool(enabled),
            cooldown_seconds=float(cooldown_seconds),
            max_fires=max_fires,
            filters=dict(filters or {}),
        )
        with self._lock:
            self.rules[rule.id] = rule
            self._save()
        return rule

    def set_enabled(self, rule_id: str, enabled: bool) -> bool:
        with self._lock:
            rule = self.rules.get(rule_id)
            if rule is None:
                return False
            rule.enabled = bool(enabled)
            self._save()
            return True

    def remove_rule(self, rule_id: str) -> bool:
        with self._lock:
            if rule_id not in self.rules:
                return False
            del self.rules[rule_id]
            self._save()
            return True

    def list_rules(self) -> list[dict[str, Any]]:
        with self._lock:
            return [rule.as_dict() for rule in self.rules.values()]

    def handle_event(self, event: Any) -> list[dict[str, Any]]:
        event_type = getattr(event, "type", None) or (event.get("type") if isinstance(event, dict) else None)
        payload = getattr(event, "args_redacted", None) or (event.get("payload", {}) if isinstance(event, dict) else {})
        if not isinstance(payload, dict):
            payload = {}
        fired: list[dict[str, Any]] = []

        with self._lock:
            for rule in self.rules.values():
                if not self._matches(rule, event_type, payload):
                    continue
                now = self.clock()
                if rule.last_fired_at is not None and now - rule.last_fired_at < rule.cooldown_seconds:
                    continue
                if rule.max_fires is not None and rule.fire_count >= rule.max_fires:
                    continue

                job_payload = {
                    "task": rule.task,
                    "trigger": {"rule_id": rule.id, "event_type": event_type},
                    "automation": True,
                }
                if self.enqueue is None:
                    fired.append({"rule_id": rule.id, "queued": False, "reason": "no_queue"})
                    continue

                try:
                    result = self.enqueue(rule.action_type, job_payload)
                except Exception as exc:
                    fired.append({"rule_id": rule.id, "queued": False, "error": str(exc)[:300]})
                    continue

                rule.last_fired_at = now
                rule.fire_count += 1
                fired.append({"rule_id": rule.id, "queued": True, "result": result})
            if fired:
                self._save()
        return fired

    @staticmethod
    def _matches(rule: AutomationRule, event_type: str | None, payload: dict[str, Any]) -> bool:
        if not rule.enabled or event_type != rule.event_type:
            return False
        return all(payload.get(key) == value for key, value in rule.filters.items())

    def _load(self) -> None:
        try:
            rows = json.loads(self.path.read_text())
        except (OSError, ValueError):
            rows = []
        if not isinstance(rows, list):
            rows = []
        for row in rows:
            if not isinstance(row, dict) or row.get("action_type") not in ALLOWED_ACTIONS:
                continue
            try:
                rule = AutomationRule(**row)
            except TypeError:
                continue
            self.rules[rule.id] = rule

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps([r.as_dict() for r in self.rules.values()], indent=2))
            tmp.replace(self.path)
        except OSError:
            # Automation state must never break the event path.
            pass


__all__ = ["ALLOWED_ACTIONS", "AutomationRule", "ProactiveAutomation"]
