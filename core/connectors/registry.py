"""Connector discovery, lifecycle, health, and explicit action dispatch."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .base import Connector, ConnectorContext
from .filesystem import FilesystemConnector
from .runtime import RuntimeConnector


class ConnectorRegistry:
    def __init__(self, context: Optional[ConnectorContext] = None):
        self.context = context or ConnectorContext()
        self._connectors: dict[str, Connector] = {}

    def register(self, connector: Connector, *, enabled: bool = False) -> Connector:
        if not connector.name or connector.name in self._connectors:
            raise ValueError(f"connector name must be unique: {connector.name!r}")
        connector.context = self.context
        self._connectors[connector.name] = connector
        if enabled:
            connector.enable()
        return connector

    def get(self, name: str) -> Optional[Connector]:
        return self._connectors.get(name)

    def enable(self, name: str) -> dict[str, Any]:
        connector = self._required(name)
        return connector.enable().to_dict()

    def disable(self, name: str) -> dict[str, Any]:
        connector = self._required(name)
        return connector.disable().to_dict()

    def refresh(self, name: Optional[str] = None) -> list[dict[str, Any]]:
        connectors = [self._required(name)] if name else list(self._connectors.values())
        return [connector.refresh() for connector in connectors if connector.enabled]

    def statuses(self) -> list[dict[str, Any]]:
        return [connector.last_status.to_dict() for connector in self._connectors.values()]

    def capabilities(self) -> dict[str, list[str]]:
        return {name: list(connector.capabilities) for name, connector in self._connectors.items()}

    def execute(self, connector_name: str, action: str, **arguments: Any) -> Any:
        connector = self._required(connector_name)
        if not connector.enabled:
            raise PermissionError(f"connector '{connector_name}' is disabled")
        handler = connector.actions().get(action)
        if handler is None:
            raise ValueError(f"unknown action '{action}' for connector '{connector_name}'")
        return handler(**arguments)

    def _required(self, name: Optional[str]) -> Connector:
        if not name or name not in self._connectors:
            raise KeyError(f"unknown connector: {name!r}")
        return self._connectors[name]


def register_builtin_connectors(registry: Optional[ConnectorRegistry] = None, *, workspace_root: Optional[Path] = None) -> ConnectorRegistry:
    """Register local, no-login connectors; nothing is enabled implicitly."""
    target = registry or connector_registry
    if target.get("runtime") is None:
        target.register(RuntimeConnector(target.context))
    if target.get("filesystem") is None:
        root = workspace_root or Path.cwd()
        target.register(FilesystemConnector(root, target.context))
    return target


connector_registry = ConnectorRegistry()
