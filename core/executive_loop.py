"""Executive lifecycle coordinator for HERMUS."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .executive import ExecutiveBrain, executive_brain
from .executive_memory import ExecutiveMemory, executive_memory
from .executive_runtime import execute_with_executive
from .world_model import WorldModel, world_model


class ExecutiveLoop:
    """Coordinate perception, planning, execution, verification and learning."""

    def __init__(self, *, brain=None, world=None, memory=None) -> None:
        self.brain = brain or executive_brain
        self.world = world or world_model
        self.memory = memory or executive_memory

    def perceive(self, *, platform: str = "api", user_id: str = "anonymous") -> dict[str, Any]:
        profile = self.world.refresh_runtime(source="executive.perception", permission_scope="system.read")
        self.world.observe("session", "identity", {"platform": platform, "user_id": user_id}, source="executive.perception", permission_scope="session.read")
        self.brain.observe("world_snapshot_refreshed", {"platform": platform, "user_id": user_id})
        return {"runtime": profile, "world": self.world.snapshot()}

    def execute(self, text: str, *, platform: str = "api", user_id: str = "anonymous", on_event: Callable | None = None, **kwargs: Any) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            raise ValueError("text must not be empty")
        self.perceive(platform=platform, user_id=user_id)
        project = kwargs.get("project")
        prior_memory = self.memory.recall_for_goal(text, project=project, limit=8)
        self.brain.observe("memory_context_loaded", {"count": len(prior_memory)})
        self.world.emit("request_started", {"text": text[:500], "platform": platform, "user_id": user_id}, source="executive.loop")

        def emit(kind: str, data: dict[str, Any] | None = None) -> None:
            payload = dict(data or {})
            self.world.emit(kind, payload, source="runtime")
            if on_event:
                try:
                    on_event(kind, payload)
                except Exception:
                    pass

        result = execute_with_executive(text, brain=self.brain, platform=platform, user_id=user_id, on_event=emit, **kwargs)
        state = str(result.get("state") or result.get("status") or "unknown")
        verified = result.get("verified")
        goal_id = (result.get("executive") or {}).get("goal_id")
        self.world.emit("request_finished", {"state": state, "mission_id": result.get("mission_id"), "goal_id": goal_id, "verified": verified}, source="executive.loop")
        self.brain.observe("world_reconciled", {"state": state, "mission_id": result.get("mission_id")})

        if goal_id:
            try:
                self.memory.remember_outcome(goal=text, state=state, verified=verified, result=result, project=project)
                self.brain.observe("outcome_memorized", {"state": state})
            except Exception as exc:
                self.brain.observe("memory_write_failed", {"error": str(exc)[:300]})

        result = dict(result)
        result["world"] = {"state": state, "snapshot": self.world.snapshot()}
        result["executive_memory"] = {"recalled": len(prior_memory)}
        return result


executive_loop = ExecutiveLoop()

__all__ = ["ExecutiveLoop", "executive_loop"]
