"""Unified perception coordinator for HERMUS.

Perception is deliberately read-only: connectors publish observations into the
WorldModel, while execution remains owned by MissionEngine and the safety
layer.  This gives the ExecutiveLoop one deterministic place to refresh the
known environment without teaching the executive about individual adapters.
"""

from __future__ import annotations

import os
from typing import Any

from .connectors import ConnectorRegistry, connector_registry, register_builtin_connectors
from .world_model import WorldModel, world_model


class PerceptionCoordinator:
    """Refresh enabled observation connectors and return a bounded snapshot."""

    def __init__(self, *, registry: ConnectorRegistry | None = None, world: WorldModel | None = None) -> None:
        self.world = world or world_model
        self.registry = registry or connector_registry

    def refresh(self, *, workspace_root=None, include_disabled: bool = False) -> dict[str, Any]:
        register_builtin_connectors(self.registry, workspace_root=workspace_root)
        if include_disabled:
            statuses = self.registry.statuses()
        else:
            statuses = [status for status in self.registry.statuses() if status.get("state") != "disabled"]

        refreshed: list[dict[str, Any]] = []
        for status in statuses:
            name = status.get("name")
            if not name:
                continue
            try:
                refreshed.extend(self.registry.refresh(name))
            except Exception as exc:
                self.world.emit("perception_connector_error", {"connector": name, "error": str(exc)[:300]}, source="perception")

        snapshot = self.world.snapshot()
        self.world.emit(
            "perception_refreshed",
            {"connectors": [item.get("connector") for item in refreshed], "fact_count": len(snapshot.get("facts", []))},
            source="perception",
        )
        # Runtime context is part of the perception contract so the executive
        # layer can reason about the machine that produced the snapshot.
        runtime = {
            "cpu_cores": max(1, int(os.cpu_count() or 1)),
            "platform": os.name,
            "workspace_root": str(workspace_root) if workspace_root else None,
        }
        return {"refreshed": refreshed, "statuses": self.registry.statuses(), "world": snapshot, "runtime": runtime}


perception = PerceptionCoordinator()

__all__ = ["PerceptionCoordinator", "perception"]
