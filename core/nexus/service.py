"""Nexus orchestration service.

This is the compatibility boundary between the product model and existing
HERMUS execution services. It owns missions and translates commands into the
canonical runtime.turn queue; it does not execute agents itself.
"""
from __future__ import annotations

import threading
import uuid
from collections import deque
from typing import Any

from core.nexus.models import Mission, NexusCommand, NexusEvent
from core.run_events import run_bus


class NexusService:
    def __init__(self, *, max_missions: int = 500, max_events: int = 1000) -> None:
        self._missions: dict[str, Mission] = {}
        self._order: deque[str] = deque(maxlen=max_missions)
        self._events: deque[NexusEvent] = deque(maxlen=max_events)
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- commands
    def submit(self, command: NexusCommand) -> dict[str, Any]:
        command = command.normalized()
        if not command.text:
            return {"accepted": False, "status": "rejected", "error": "Command text is empty"}

        mission_id = f"mission_{uuid.uuid4().hex}"
        mission = Mission(id=mission_id, intent=command.text, user_id=command.user_id, state="queued")
        self._store(mission)
        self._emit("mission.created", mission=mission)

        from gateway.queue import job_queue

        payload: dict[str, Any] = {
            "text": command.text,
            "user_id": command.user_id,
            "platform": command.channel,
            "mode": command.mode,
            "stream": True,
            "mission_id": mission_id,
        }
        if command.session_id:
            payload["session_id"] = command.session_id
        prefer = command.metadata.get("prefer")
        if prefer:
            payload["prefer"] = str(prefer)

        try:
            job = job_queue.submit(
                "runtime.turn",
                payload,
                session_key=f"{command.channel}:{command.user_id}",
                priority=command.priority,
            )
        except Exception as exc:
            mission.transition("failed", error=f"{type(exc).__name__}: {exc}")
            self._emit("mission.failed", mission=mission, data={"error": mission.error})
            return {
                "accepted": False,
                "mission_id": mission_id,
                "status": "rejected",
                "error": mission.error,
            }

        mission.transition("running", run_id=job.run_id, job_id=job.id)
        self._emit("mission.started", mission=mission)
        return {
            "accepted": True,
            "mission_id": mission_id,
            "run_id": job.run_id,
            "job_id": job.id,
            "status": job.status,
        }

    # ----------------------------------------------------------------- state
    def state(self, *, user_id: str = "default") -> dict[str, Any]:
        from gateway.queue import job_queue

        with self._lock:
            missions = [m.to_dict() for m in self._missions.values() if m.user_id == user_id]
            events = [e.to_dict() for e in self._events]

        return {
            "version": 1,
            "presence": self._presence(user_id),
            "missions": missions[-50:],
            "events": events[-100:],
            "runs": run_bus.runs(),
            "queue": job_queue.status(),
            "active_jobs": job_queue.list_jobs(limit=20, status="running"),
            "recent_jobs": job_queue.list_jobs(limit=12),
            "agents": self._agents(),
            "health": self._health(),
        }

    def mission(self, mission_id: str) -> dict[str, Any] | None:
        with self._lock:
            mission = self._missions.get(mission_id)
            return mission.to_dict() if mission else None

    def cancel(self, run_id: str) -> bool:
        ok = run_bus.cancel(run_id)
        if ok:
            self._sync_run(run_id, "cancelled")
        return ok

    def steer(self, run_id: str, text: str) -> bool:
        return run_bus.steer(run_id, text)

    def sync_runs(self) -> None:
        """Project canonical run state into missions without owning execution."""
        for run in run_bus.runs():
            run_id = run.get("run_id")
            if not run_id:
                continue
            status = str(run.get("status") or "running")
            self._sync_run(run_id, status, run)

    # -------------------------------------------------------------- internals
    def _store(self, mission: Mission) -> None:
        with self._lock:
            self._missions[mission.id] = mission
            self._order.append(mission.id)
            while len(self._missions) > self._order.maxlen:
                victim = self._order.popleft()
                self._missions.pop(victim, None)

    def _sync_run(self, run_id: str, status: str, snapshot: dict[str, Any] | None = None) -> None:
        mapped = {
            "running": "running",
            "finished": "succeeded",
            "error": "failed",
            "cancelled": "cancelled",
        }.get(status)
        if mapped is None:
            return
        with self._lock:
            matches = [m for m in self._missions.values() if m.run_id == run_id]
            for mission in matches:
                mission.transition(mapped, result=(snapshot or {}).get("result"), error=(snapshot or {}).get("error"))

    def _emit(self, event_type: str, *, mission: Mission | None = None, data: dict[str, Any] | None = None) -> None:
        event = NexusEvent(
            type=event_type,
            mission_id=mission.id if mission else None,
            run_id=mission.run_id if mission else None,
            data=dict(data or {}),
        )
        with self._lock:
            self._events.append(event)

    @staticmethod
    def _presence(user_id: str) -> dict[str, Any]:
        try:
            from core.presence import get_presence
            return get_presence().snapshot(user_id=user_id)
        except Exception as exc:
            return {"state": "unknown", "error": f"{type(exc).__name__}: {exc}"}

    @staticmethod
    def _agents() -> dict[str, Any]:
        try:
            from core.task_tracker import task_tracker
            return task_tracker.get_status()
        except Exception:
            return {}

    @staticmethod
    def _health() -> dict[str, Any]:
        checks: dict[str, Any] = {}
        try:
            from gateway.queue import job_queue
            checks["queue"] = {"ok": bool(job_queue.enabled), "running": bool(job_queue._started)}
        except Exception:
            checks["queue"] = {"ok": False}
        try:
            from core.nollama import nollama_manager
            checks["local_engine"] = {
                "installed": bool(nollama_manager.installed()),
                "running": bool(nollama_manager.running()),
            }
        except Exception:
            checks["local_engine"] = {"installed": False, "running": False}
        return checks


nexus = NexusService()
