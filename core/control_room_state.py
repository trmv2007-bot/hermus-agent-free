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
    brain = executive_brain.snapshot()
    return {
        "executive": brain,
        "world": world,
        "memory": {
            "available": True,
            "backend": type(executive_memory.store).__name__ if getattr(executive_memory, "store", None) else None,
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
