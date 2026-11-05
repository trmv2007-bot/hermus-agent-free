"""Canonical HERMUS engine facade.

The engine is the product/backend boundary.  It models HERMUS in terms of
intent, execution, conversation, capabilities and events instead of exposing
UI-specific concepts.  Transport adapters (HTTP, WebSocket, CLI, voice) call
this layer; the engine delegates execution to the existing queue/run bus.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.run_events import run_bus


@dataclass(frozen=True)
class Intent:
    text: str
    user_id: str = "default"
    session_id: str | None = None
    channel: str = "unknown"
    mode: str = "chat"
    priority: int = 0
    prefer: str | None = None


class HermusEngine:
    """Single backend interaction boundary for all HERMUS clients."""

    def submit(self, intent: Intent) -> dict[str, Any]:
        text = intent.text.strip()
        if not text:
            return {"accepted": False, "status": "rejected", "error": "Intent text is empty"}

        from gateway.queue import job_queue

        payload: dict[str, Any] = {
            "text": text,
            "user_id": intent.user_id,
            "platform": intent.channel,
            "mode": intent.mode,
            "stream": True,
        }
        if intent.session_id:
            payload["session_id"] = intent.session_id
        if intent.prefer:
            payload["prefer"] = intent.prefer

        try:
            job = job_queue.submit(
                "runtime.turn",
                payload,
                session_key=f"{intent.channel}:{intent.user_id}",
                priority=intent.priority,
            )
        except Exception as exc:
            return {
                "accepted": False,
                "status": "rejected",
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {
            "accepted": True,
            "run_id": job.run_id,
            "job_id": job.id,
            "status": job.status,
        }

    def snapshot(self, *, user_id: str = "default") -> dict[str, Any]:
        """Return a backend-centric snapshot for non-UI clients."""
        from gateway.queue import job_queue

        return {
            "version": 2,
            "user_id": user_id,
            "presence": self._presence(user_id),
            "execution": {
                "runs": run_bus.runs(),
                "active": job_queue.list_jobs(limit=20, status="running"),
                "recent": job_queue.list_jobs(limit=20),
                "queue": job_queue.status(),
            },
            "agents": self._agents(),
            "health": self._health(),
        }

    def run(self, run_id: str) -> dict[str, Any] | None:
        run = run_bus.get(run_id)
        if run is None:
            return None
        return {
            "run": run.to_dict(),
            "events": run_bus.history(run_id),
        }

    def cancel(self, run_id: str) -> bool:
        return run_bus.cancel(run_id)

    def steer(self, run_id: str, text: str) -> bool:
        return bool(text.strip()) and run_bus.steer(run_id, text.strip())

    def capabilities(self) -> dict[str, Any]:
        try:
            from core.capabilities import capability_registry

            return capability_registry.snapshot()
        except Exception:
            return {}

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


engine = HermusEngine()
