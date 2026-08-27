"""Runtime/hardware connector backed by Hermus' existing telemetry."""
from __future__ import annotations

from typing import Any

from .base import Connector, ConnectorStatus


class RuntimeConnector(Connector):
    name = "runtime"
    capabilities = ("runtime.read", "runtime.refresh")

    def health(self) -> ConnectorStatus:
        try:
            from ..computer.resources import get_resource_monitor

            get_resource_monitor().sample()
            return ConnectorStatus(self.name, "ready" if self.enabled else "disabled", capabilities=list(self.capabilities))
        except Exception as exc:  # noqa: BLE001
            return ConnectorStatus(self.name, "degraded", str(exc)[:200], capabilities=list(self.capabilities))

    def refresh(self) -> dict[str, Any]:
        if not self.enabled:
            return {"success": False, "connector": self.name, "error": "connector is disabled"}
        profile = self.context.world.refresh_runtime(source=self.name, permission_scope="runtime.read")
        return {"success": True, "connector": self.name, "facts": len(profile), "profile": profile}

    def observe(self) -> list[dict[str, Any]]:
        # Runtime.refresh performs the atomic profile update directly so a
        # refresh cannot duplicate every fact in the journal.
        return []

    def actions(self) -> dict[str, Any]:
        return {"refresh": self.refresh}
