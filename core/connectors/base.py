"""Base contracts for Hermus integrations."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..world_model import WorldModel, world_model


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ConnectorStatus:
    name: str
    state: str = "disabled"  # disabled | ready | degraded | error
    message: str = ""
    checked_at: str = field(default_factory=_now)
    capabilities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "message": self.message,
            "checked_at": self.checked_at,
            "capabilities": list(self.capabilities),
        }


@dataclass
class ConnectorContext:
    """Dependencies supplied by the registry; connectors stay easy to test."""

    world: WorldModel = field(default_factory=lambda: world_model)
    config: dict[str, Any] = field(default_factory=dict)


class Connector:
    """Minimal interface implemented by every external-system adapter."""

    name = "unnamed"
    capabilities: tuple[str, ...] = ()

    def __init__(self, context: Optional[ConnectorContext] = None):
        self.context = context or ConnectorContext()
        self.enabled = False
        self.last_status = ConnectorStatus(self.name, capabilities=list(self.capabilities))

    def enable(self) -> ConnectorStatus:
        self.enabled = True
        self.last_status = self.health()
        return self.last_status

    def disable(self) -> ConnectorStatus:
        self.enabled = False
        self.last_status = ConnectorStatus(self.name, "disabled", "disabled by configuration", capabilities=list(self.capabilities))
        return self.last_status

    def health(self) -> ConnectorStatus:
        return ConnectorStatus(self.name, "ready" if self.enabled else "disabled", capabilities=list(self.capabilities))

    def observe(self) -> list[dict[str, Any]]:
        """Return facts to ingest; connectors must not mutate unrelated state."""
        return []

    def actions(self) -> dict[str, Any]:
        """Return action name -> callable. Actions are never exposed implicitly."""
        return {}

    def refresh(self) -> dict[str, Any]:
        if not self.enabled:
            return {"success": False, "connector": self.name, "error": "connector is disabled"}
        facts = self.observe()
        count = self.context.world.ingest(self.name, facts)
        self.context.world.emit("connector_refreshed", {"connector": self.name, "facts": count}, source=self.name)
        return {"success": True, "connector": self.name, "facts": count}
