"""Executive lifecycle coordinator for HERMUS."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .agent_delegation import agent_delegator
from .executive import executive_brain
from .executive_memory import executive_memory
from .executive_runtime import execute_with_executive
from .perception import perception
from .world_model import world_model
from .world_awareness import world_awareness


class ExecutiveLoop:
    """Coordinate perception, planning, delegation, execution, verification and learning."""

    def __init__(self, *, brain=None, world=None, memory=None, delegator=None, perception_layer=None, awareness_layer=None) -> None:
        self.brain = brain or executive_brain
        self.world = world or world_model
        self.memory = memory or executive_memory
        self.delegator = delegator or agent_delegator
        self.perception = perception_layer or perception
        self.awareness = awareness_layer or world_awareness

    def perceive(self, *, platform: str = "api", user_id: str = "anonymous", workspace_root=None) -> dict[str, Any]:
        observed = self.perception.refresh(workspace_root=workspace_root)
        try:
            observed["world_awareness"] = self.awareness.refresh(workspace_root=workspace_root)
        except Exception as exc:
            self.world.emit("world_awareness_error", {"error": str(exc)[:300]}, source="executive.perception")
        # Keep the perception contract explicit: callers can tell which runtime
        # produced the snapshot without importing the executive implementation.
        observed["runtime"] = {
            "platform": platform,
            "user_id": user_id,
            "workspace_root": str(workspace_root) if workspace_root else None,
            "source": "executive.perception",
        }
        self.world.observe(
            "session",
            "identity",
            {"platform": platform, "user_id": user_id},
            source="executive.perception",
            permission_scope="session.read",
        )
        self.brain.observe("world_snapshot_refreshed", {"platform": platform, "user_id": user_id})
        return observed

    def execute(self, text: str, *, platform: str = "api", user_id: str = "anonymous", on_event: Callable | None = None, **kwargs: Any) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            raise ValueError("text must not be empty")
        self.perceive(platform=platform, user_id=user_id, workspace_root=kwargs.get("workspace_root"))
        project = kwargs.get("project")
        prior_memory = self.memory.recall_for_goal(text, project=project, limit=8)
        self.brain.observe("memory_context_loaded", {"count": len(prior_memory)})

        delegation = self.delegator.build_plan(text)
        self.brain.observe("specialist_team_selected", {"roles": delegation.selected_roles})
        self.world.emit("delegation_planned", {"roles": delegation.selected_roles, "dag": delegation.dag.to_dict()}, source="executive.delegation")
        self.world.emit("request_started", {"text": text[:500], "platform": platform, "user_id": user_id}, source="executive.loop")

        def emit(kind: str, data: dict[str, Any] | None = None) -> None:
            payload = dict(data or {})
            self.world.emit(kind, payload, source="runtime")
            if on_event:
                try:
                    on_event(kind, payload)
                except Exception:
                    pass

        result = execute_with_executive(
            text,
            brain=self.brain,
            platform=platform,
            user_id=user_id,
            on_event=emit,
            delegation=delegation.to_dict(),
            **kwargs,
        )
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
        result["delegation"] = delegation.to_dict()
        result["world"] = {"state": state, "snapshot": self.world.snapshot()}
        result["executive_memory"] = {"recalled": len(prior_memory)}
        return result


executive_loop = ExecutiveLoop()

__all__ = ["ExecutiveLoop", "executive_loop"]
