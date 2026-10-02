"""Executive lifecycle coordinator for HERMUS.

This module composes the already-existing ExecutiveBrain, WorldModel and
universal runtime bridge.  It is intentionally an orchestration layer, not a
new autonomy engine: actual execution remains in ``core.runtime``.

Lifecycle:
    perceive -> plan -> handoff -> execute -> observe -> reconcile

The coordinator gives HERMUS one place to maintain situational state around a
mission without allowing the executive layer to bypass runtime safety gates.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .executive import ExecutiveBrain, executive_brain
from .executive_runtime import execute_with_executive
from .world_model import WorldModel, world_model


class ExecutiveLoop:
    """Coordinate perception, executive planning, runtime execution and learning."""

    def __init__(
        self,
        *,
        brain: ExecutiveBrain | None = None,
        world: WorldModel | None = None,
    ) -> None:
        self.brain = brain or executive_brain
        self.world = world or world_model

    def perceive(self, *, platform: str = "api", user_id: str = "anonymous") -> dict[str, Any]:
        """Refresh safe local runtime facts and record the observation."""
        profile = self.world.refresh_runtime(source="executive.perception", permission_scope="system.read")
        self.world.observe(
            "session",
            "identity",
            {"platform": platform, "user_id": user_id},
            source="executive.perception",
            permission_scope="session.read",
        )
        self.brain.observe("world_snapshot_refreshed", {"platform": platform, "user_id": user_id})
        return {"runtime": profile, "world": self.world.snapshot()}

    def execute(
        self,
        text: str,
        *,
        platform: str = "api",
        user_id: str = "anonymous",
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run one request through the executive control plane."""
        text = str(text or "").strip()
        if not text:
            raise ValueError("text must not be empty")

        self.perceive(platform=platform, user_id=user_id)
        self.world.emit(
            "request_started",
            {"text": text[:500], "platform": platform, "user_id": user_id},
            source="executive.loop",
        )

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
            **kwargs,
        )

        state = str(result.get("state") or result.get("status") or "unknown")
        self.world.emit(
            "request_finished",
            {
                "state": state,
                "mission_id": result.get("mission_id"),
                "goal_id": (result.get("executive") or {}).get("goal_id"),
                "verified": result.get("verified"),
            },
            source="executive.loop",
        )
        self.brain.observe(
            "world_reconciled",
            {"state": state, "mission_id": result.get("mission_id")},
        )
        result = dict(result)
        result["world"] = {
            "state": state,
            "snapshot": self.world.snapshot(),
        }
        return result


executive_loop = ExecutiveLoop()

__all__ = ["ExecutiveLoop", "executive_loop"]
