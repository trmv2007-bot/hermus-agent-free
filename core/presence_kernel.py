"""Unified HERMUS presence kernel.

The presence kernel is a read-model over the existing canonical subsystems:
PresenceManager, ExecutiveBrain, WorldModel, RunBus/JobQueue and EventBus.
It owns no execution, tool, model, memory or safety lifecycle.

Its job is to make HERMUS feel like one continuously coherent intelligence:
observe canonical events, derive an honest operational state, prioritize
attention, and expose one compact snapshot for clients.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from .events import get_bus
from .presence import get_presence
from .run_events import run_bus
from .world_awareness import world_awareness
from .world_model import world_model
from .executive import executive_brain


_MEANINGFUL = {
    "command.requested",
    "command.started",
    "command.completed",
    "command.failed",
    "job.lifecycle",
    "mission.started",
    "mission.completed",
    "mission.failed",
    "run_started",
    "run_finished",
    "run_error",
    "verification",
    "approval",
    "permission",
    "tool_call",
    "tool_result",
    "skill",
    "learning",
    "runtime_issue",
}

_FINISHED_WORDS = ("finished", "completed", "succeeded", "success", "done", "cancelled")
_ERROR_WORDS = ("error", "failed", "failure", "denied", "blocked")
_START_WORDS = ("requested", "started", "queued", "running", "created")
_APPROVAL_WORDS = ("approval", "permission", "authorize", "authorization")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _event_payload(event: Any) -> dict[str, Any]:
    raw = getattr(event, "args_redacted", None)
    if isinstance(raw, dict):
        return raw
    raw = getattr(event, "payload", None)
    return raw if isinstance(raw, dict) else {}


def _event_id(event: Any) -> str:
    return str(getattr(event, "event_id", "") or getattr(event, "id", "") or "")


class PresenceKernel:
    """One live, deterministic presence projection for HERMUS."""

    def __init__(
        self,
        *,
        presence=None,
        executive=None,
        world=None,
        awareness=None,
        bus=None,
        runs=None,
    ) -> None:
        self.presence = presence or get_presence()
        self.executive = executive or executive_brain
        self.world = world or world_model
        self.awareness = awareness or world_awareness
        self.runs = runs or run_bus
        self._bus = bus
        self._unsubscribe = None
        self._lock = threading.RLock()
        self._recent: deque[dict[str, Any]] = deque(maxlen=60)
        self._last_meaningful_at: str | None = None

    # ---------------------------------------------------------------- wiring
    def start(self) -> "PresenceKernel":
        bus = self._bus or get_bus()
        with self._lock:
            if self._bus is bus and self._unsubscribe is not None:
                return self
            if self._unsubscribe is not None:
                try:
                    self._unsubscribe()
                except Exception:
                    pass
            self._bus = bus
            subscriber = bus.subscribe()(self._on_event)
            self._subscriber = subscriber
            self._unsubscribe = lambda: bus.unsubscribe(subscriber)
        return self

    def stop(self) -> None:
        """Detach the canonical EventBus subscription without changing state."""
        with self._lock:
            unsubscribe = self._unsubscribe
            self._unsubscribe = None
            self._subscriber = None
        if unsubscribe is not None:
            try:
                unsubscribe()
            except Exception:
                pass

    # ---------------------------------------------------------------- events
    def _on_event(self, event: Any) -> None:
        try:
            event_type = _text(getattr(event, "type", ""), 120).lower()
            source = _text(getattr(event, "source", ""), 80).lower()
            if not event_type or event_type.startswith("presence."):
                return

            payload = _event_payload(event)
            status = _text(getattr(event, "status", "") or payload.get("status"), 60).lower()
            significant = event_type in _MEANINGFUL or any(
                word in event_type for word in ("mission", "approval", "permission", "verification", "lifecycle")
            )
            if not significant:
                return

            row = {
                "event_id": _event_id(event),
                "type": event_type,
                "source": source,
                "status": status or None,
                "run_id": getattr(event, "run_id", None),
                "mission_id": getattr(event, "mission_id", None),
                "summary": _text(
                    payload.get("message")
                    or payload.get("summary")
                    or getattr(event, "command", None)
                    or event_type.replace(".", " ")
                ),
                "at": _text(getattr(event, "timestamp", "") or getattr(event, "created_at", "") or _now(), 80),
            }
            with self._lock:
                self._recent.append(row)
                self._last_meaningful_at = row["at"]

            self._apply_presence_transition(event_type, status, payload, event)
        except Exception:
            # Presence is a projection. A broken observer must never break runtime.
            pass

    def _apply_presence_transition(
        self,
        event_type: str,
        status: str,
        payload: dict[str, Any],
        event: Any,
    ) -> None:
        combined = " ".join((event_type, status)).lower()

        state: str | None = None
        detail = ""

        if any(word in combined for word in _APPROVAL_WORDS) and not any(
            word in combined for word in ("approved", "granted", "resolved", "denied")
        ):
            state, detail = "waiting_approval", "approval required"
        elif any(word in combined for word in _ERROR_WORDS):
            state, detail = "error", _text(
                payload.get("error") or payload.get("message") or "runtime issue"
            )
        elif "verification" in combined:
            state, detail = "verifying", "checking the requested result"
        elif "learning" in combined or event_type.startswith("skill"):
            state, detail = "learning", "updating a reusable capability"
        elif any(word in combined for word in _START_WORDS):
            state, detail = ("thinking", "interpreting the request") if "requested" in combined else (
                "working", "working on the request"
            )
        elif any(word in combined for word in _FINISHED_WORDS):
            state, detail = self._terminal_state()

        if state is None:
            return

        run_id = getattr(event, "run_id", None)
        session_id = getattr(event, "session_id", None)
        user_id = getattr(event, "user_id", None)

        try:
            self.presence.set_state(
                state,
                detail=detail,
                run_id=run_id,
                session_id=session_id,
                user_id=user_id,
                goal=payload.get("goal"),
                last_error=payload.get("error") if state == "error" else None,
            )
        except Exception:
            pass

    def _terminal_state(self) -> tuple[str, str]:
        try:
            active = [
                row for row in self.runs.runs()
                if str(row.get("status") or "").lower() in {"queued", "running"}
            ]
        except Exception:
            active = []
        return ("working", "another task is still active") if active else ("idle", "ready")

    # ---------------------------------------------------------------- attention
    def attention(self, *, user_id: str = "default") -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        def add(item_id: str, severity: str, title: str, detail: str, source: str, actionable: bool = True):
            rows.append(
                {
                    "id": item_id,
                    "severity": severity,
                    "title": _text(title, 160),
                    "detail": _text(detail, 300),
                    "source": _text(source, 80),
                    "actionable": bool(actionable),
                }
            )

        # Safety / approvals come first.
        try:
            from .emergency_stop import get_emergency_stop

            if get_emergency_stop().active():
                add("emergency-stop", "critical", "Emergency stop is active", "Computer actions are blocked until the stop is cleared.", "safety")
        except Exception:
            pass

        try:
            from .permissions import permission_manager

            pending = permission_manager.pending() or []
            if pending:
                add("approvals", "high", f"{len(pending)} approval request(s) waiting", "HERMUS is waiting for an explicit authorization decision.", "permissions")
        except Exception:
            pass

        try:
            reliability = self._status("core.reliability.reliability", "status")
            incidents = reliability.get("incidents") if isinstance(reliability, dict) else []
            open_incidents = [r for r in incidents if str((r or {}).get("status") or "open").lower() not in {"closed", "resolved"}]
            if open_incidents:
                add("reliability", "high", f"{len(open_incidents)} reliability incident(s) open", "Runtime recovery has unresolved incidents.", "reliability")
        except Exception:
            pass

        try:
            distributed = self._status("core.distributed.distributed", "status")
            stale = int(distributed.get("stale", 0) or 0) if isinstance(distributed, dict) else 0
            if stale:
                add("distributed-stale", "medium", f"{stale} device/node(s) are stale", "A distributed HERMUS node has missed its freshness window.", "distributed")
        except Exception:
            pass

        try:
            due = self.presence.check_ins_due(user_id=user_id)
            if due:
                first = due[0]
                add(
                    "goal-checkin",
                    "medium",
                    "An ongoing goal is due for a check-in",
                    str(first.get("title") or "Review ongoing work"),
                    "presence",
                )
        except Exception:
            pass

        try:
            goals = self.executive.active_goals(limit=10)
            failed = [g for g in goals if str(g.get("status") or "").lower() == "failed"]
            if failed:
                add("goal-failure", "high", f"{len(failed)} executive goal(s) failed", "A persistent goal needs review or replanning.", "executive")
        except Exception:
            pass

        try:
            issues = self._recent_runtime_issues()
            if issues:
                latest = issues[-1]
                add(
                    "runtime-issue",
                    "medium",
                    f"Runtime issue in {latest.get('component') or 'unknown'}",
                    str(latest.get("error") or "Inspect the recent runtime issue log."),
                    "runtime",
                )
        except Exception:
            pass

        try:
            world_status = self.awareness.status()
            if world_status.get("fresh") is False and world_status.get("age_seconds") is not None:
                add(
                    "world-stale",
                    "low",
                    "World awareness is stale",
                    f"Last environment refresh was {round(float(world_status['age_seconds']))}s ago.",
                    "world",
                )
        except Exception:
            pass

        # A changed world is useful context, but should not interrupt unless higher
        # priority conditions are absent.
        try:
            recent_world = self.world.recent_events(limit=3)
            if recent_world:
                latest = recent_world[-1]
                if latest.event_type in {"world_reconciled", "perception_refreshed"} and latest.data.get("changed"):
                    add("world-changed", "low", "Your environment changed", "HERMUS detected a new workspace or runtime state change.", "world")
        except Exception:
            pass

        rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        return sorted(rows, key=lambda row: (rank.get(row["severity"], 9), row["id"]))

    def _status(self, module: str, attr: str) -> dict[str, Any]:
        import importlib

        owner = importlib.import_module(module)
        value = getattr(owner, attr, None)
        result = value() if callable(value) else value
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _recent_runtime_issues() -> list[dict[str, Any]]:
        try:
            from .run_events import recent_issues

            return recent_issues(limit=5)
        except Exception:
            return []

    def _world_projection(self) -> dict[str, Any]:
        """Return high-value world facts without dumping large process/browser payloads."""
        wanted = (
            ("runtime", "platform"),
            ("runtime", "os"),
            ("runtime", "architecture"),
            ("runtime", "cpu_percent"),
            ("runtime", "memory"),
            ("workspace.git", "branch"),
            ("workspace.git", "commit"),
            ("workspace.git", "status"),
            ("browser", "state"),
            ("world", "last_refresh_at"),
            ("world", "observation_digest"),
        )
        facts: dict[str, Any] = {}
        for subject, predicate in wanted:
            try:
                fact = self.world.get(subject, predicate)
            except Exception:
                fact = None
            if fact is None:
                continue
            value = fact.value
            if isinstance(value, str):
                value = value[:500]
            elif isinstance(value, dict):
                value = {str(k): v for k, v in list(value.items())[:12]}
            facts[f"{subject}.{predicate}"] = {
                "value": value,
                "source": fact.source,
                "confidence": fact.confidence,
                "observed_at": fact.observed_at,
            }
        try:
            recent = [event.to_dict() for event in self.world.recent_events(limit=8)]
        except Exception:
            recent = []
        return {"facts": facts, "recent_events": recent}

    # ---------------------------------------------------------------- snapshot
    def snapshot(self, *, user_id: str = "default", include_events: bool = True) -> dict[str, Any]:
        self.start()

        with self._lock:
            recent = list(self._recent)[-20:]

        try:
            presence = self.presence.snapshot(user_id=user_id)
        except Exception as exc:
            presence = {"state": "unknown", "detail": str(exc)[:200]}

        try:
            goals = self.executive.active_goals(limit=10)
            executive_events = self.executive.recent_events(limit=10)
        except Exception:
            goals, executive_events = [], []

        try:
            world_status = self.awareness.status()
        except Exception:
            world_status = {"fresh": False, "fact_count": 0, "event_count": 0}

        try:
            runs = self.runs.runs()
            active_runs = [
                row for row in runs if str(row.get("status") or "").lower() in {"queued", "running"}
            ]
        except Exception:
            active_runs = []
            runs = []

        try:
            from gateway.queue import job_queue

            queue = job_queue.status()
        except Exception:
            queue = {}

        payload = {
            "version": 1,
            "generated_at": _now(),
            "identity": presence.get("identity", {}),
            "presence": presence.get("presence", {}),
            "goals": goals,
            "world": {**world_status, **self._world_projection()},
            "attention": self.attention(user_id=user_id),
            "runtime": {
                "active_runs": active_runs[:20],
                "run_count": len(runs),
                "queue": queue,
            },
            "events": {
                "cursor": int(getattr(self._bus or get_bus(), "cursor", 0) or 0),
                "recent": recent if include_events else [],
                "last_meaningful_at": self._last_meaningful_at,
            },
            "executive": {
                "active_goals": goals,
                "recent_events": executive_events,
            },
        }
        payload["summary"] = self._summary(payload)
        return payload

    @staticmethod
    def _summary(snapshot: dict[str, Any]) -> dict[str, Any]:
        presence = snapshot.get("presence") or {}
        attention = snapshot.get("attention") or []
        active = snapshot.get("runtime", {}).get("active_runs") or []
        critical = sum(1 for item in attention if item.get("severity") == "critical")
        high = sum(1 for item in attention if item.get("severity") == "high")
        state = str(presence.get("state") or "idle")
        detail = str(presence.get("detail") or "ready")
        if attention:
            headline = str(attention[0].get("title") or "Attention required")
        elif active:
            headline = f"{len(active)} task(s) active"
        else:
            headline = "All systems ready"
        return {
            "state": state,
            "detail": detail,
            "headline": _text(headline, 180),
            "attention_count": len(attention),
            "critical_count": critical,
            "high_count": high,
            "active_runs": len(active),
        }

    def events_since(self, cursor: int = 0, limit: int = 60) -> dict[str, Any]:
        self.start()
        bus = self._bus or get_bus()
        limit = max(1, min(int(limit), 200))
        events = bus.replay(since_cursor=max(0, int(cursor)), limit=limit)
        return {
            "cursor": bus.cursor,
            "events": [
                {
                    "event_id": _event_id(event),
                    "type": _text(getattr(event, "type", ""), 120),
                    "source": _text(getattr(event, "source", ""), 80),
                    "status": _text(getattr(event, "status", ""), 60) or None,
                    "run_id": getattr(event, "run_id", None),
                    "mission_id": getattr(event, "mission_id", None),
                    "command": _text(getattr(event, "command", ""), 160),
                    "at": _text(getattr(event, "created_at", "") or _now(), 80),
                }
                for event in events
            ],
        }


presence_kernel = PresenceKernel()

__all__ = ["PresenceKernel", "presence_kernel"]
