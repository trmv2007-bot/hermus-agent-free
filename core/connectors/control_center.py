"""Integration Control Center: one place to operate Hermus connectors."""
from __future__ import annotations

import threading
from typing import Any, Optional

from .base import ConnectorContext
from .filesystem import FilesystemConnector
from .registry import ConnectorRegistry, register_builtin_connectors
from .services import SERVICE_CONNECTORS


class IntegrationControlCenter:
    """Lifecycle and periodic refresh manager for all configured integrations."""

    def __init__(self, registry: Optional[ConnectorRegistry] = None, context: Optional[ConnectorContext] = None):
        self.registry = registry or ConnectorRegistry(context)
        self._timer: Optional[threading.Timer] = None
        self._interval = 0.0

    def install_defaults(self, workspace_root=None) -> ConnectorRegistry:
        register_builtin_connectors(self.registry, workspace_root=workspace_root)
        for connector_type in SERVICE_CONNECTORS:
            if self.registry.get(connector_type.name) is None:
                self.registry.register(connector_type(self.registry.context))
        return self.registry

    def overview(self) -> dict[str, Any]:
        statuses = self.registry.statuses()
        return {
            "connectors": statuses,
            "connected": sum(item["state"] == "ready" for item in statuses),
            "degraded": sum(item["state"] == "degraded" for item in statuses),
            "disabled": sum(item["state"] == "disabled" for item in statuses),
            "capabilities": self.registry.capabilities(),
        }

    def refresh(self, name: Optional[str] = None) -> list[dict[str, Any]]:
        return self.registry.refresh(name)

    def start_refresh_loop(self, interval_seconds: float = 60.0) -> None:
        self.stop_refresh_loop()
        self._interval = max(1.0, float(interval_seconds))
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._interval <= 0:
            return
        self._timer = threading.Timer(self._interval, self._refresh_tick)
        self._timer.daemon = True
        self._timer.start()

    def _refresh_tick(self) -> None:
        try:
            self.refresh()
        finally:
            self._schedule_refresh()

    def stop_refresh_loop(self) -> None:
        self._interval = 0.0
        if self._timer:
            self._timer.cancel()
            self._timer = None


integration_center = IntegrationControlCenter()
