"""Read-only executive state projection for the Control Room and diagnostics."""

from __future__ import annotations

from typing import Any

from .agent_delegation import agent_delegator
from .executive import executive_brain
from .executive_memory import executive_memory
from .perception import perception
from .world_model import world_model


def snapshot() -> dict[str, Any]:
    """Return explainable, bounded state; never invent operational facts."""
    world = world_model.snapshot()
    return {
        "executive": {
            "active_goals": executive_brain.active_goals(limit=20),
            "recent_events": executive_brain.recent_events(limit=50),
        },
        "world": world,
        "memory": {
            "available": bool(getattr(executive_memory, "memory", None)),
            "backend": type(executive_memory.memory).__name__,
        },
        "perception": perception.refresh(include_disabled=True),
        "specialists": {
            "max_agents": agent_delegator.max_agents,
            "capabilities": {
                role: list(profile.capabilities)
                for role, profile in agent_delegator.specialists.items()
            },
        },
    }


__all__ = ["snapshot"]
