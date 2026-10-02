"""HERMUS interaction orchestration layer.

This module is intentionally thin: existing subsystems remain the source of
truth, while the UI gets one coherent model for presence, missions, runs,
tools and system health. It is an adapter, not a second execution engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.run_events import run_bus


@dataclass(frozen=True)
class CommandResult:
    accepted: bool
    run_id: str | None = None
    job_id: str | None = None
    status: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "run_id": self.run_id,
            "job_id": self.job_id,
            "status": self.status,
            "error": self.error,
        }


class HERMUSOrchestrator:
    """Stable interaction facade over the existing HERMUS runtime."""

    def state(self, *, user_id: str = "default") -> dict[str, Any]:
        from gateway.queue import job_queue

        state: dict[str, Any] = {
            "presence": self._presence(user_id),
            "runs": run_bus.runs(),
            "queue": job_queue.status(),
            "active_jobs": job_queue.list_jobs(limit=20, status="running"),
            "recent_jobs": job_queue.list_jobs(limit=12),
            "agents": self._agents(),
            "health": self._health(),
        }
        return state

    def submit(self, text: str, *, user_id: str = "default", session_id: str | None = None,
               platform: str = "nexus", mode: str = "chat", prefer: str | None = None,
               priority: int = 0) -> CommandResult:
        text = str(text or "").strip()
        if not text:
            return CommandResult(False, status="rejected", error="Command text is empty")

        from gateway.queue import job_queue

        payload: dict[str, Any] = {
            "text": text,
            "user_id": user_id,
            "platform": platform,
            "mode": mode,
            "stream": True,
        }
        if session_id:
            payload["session_id"] = session_id
        if prefer:
            payload["prefer"] = prefer

        try:
            job = job_queue.submit(
                "runtime.turn",
                payload,
                session_key=f"{platform}:{user_id}",
                priority=int(priority),
            )
        except Exception as exc:
            return CommandResult(False, status="rejected", error=f"{type(exc).__name__}: {exc}")

        return CommandResult(True, run_id=job.run_id, job_id=job.id, status=job.status)

    def cancel(self, run_id: str) -> bool:
        return run_bus.cancel(run_id)

    def steer(self, run_id: str, text: str) -> bool:
        return run_bus.steer(run_id, text)

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


orchestrator = HERMUSOrchestrator()
