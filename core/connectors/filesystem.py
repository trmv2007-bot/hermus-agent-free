"""Approved-workspace filesystem connector."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Connector, ConnectorStatus


class FilesystemConnector(Connector):
    name = "filesystem"
    capabilities = ("filesystem.read", "filesystem.observe")

    def __init__(self, root: Path, context=None):
        super().__init__(context)
        self.root = Path(root).resolve()

    def health(self) -> ConnectorStatus:
        state = "ready" if self.enabled and self.root.is_dir() else "degraded"
        message = "" if self.root.is_dir() else f"workspace does not exist: {self.root}"
        return ConnectorStatus(self.name, state if self.enabled else "disabled", message, capabilities=list(self.capabilities))

    def observe(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        try:
            files = []
            for item in self.root.iterdir():
                if item.name.startswith(".") or item.name in {"__pycache__", "node_modules"}:
                    continue
                files.append({"name": item.name, "kind": "directory" if item.is_dir() else "file"})
            return [{
                "subject": "filesystem",
                "predicate": "workspace_entries",
                "value": {"root": str(self.root), "entries": files},
                "permission_scope": "filesystem.read",
            }]
        except OSError as exc:
            self.last_status = ConnectorStatus(self.name, "degraded", str(exc)[:200], capabilities=list(self.capabilities))
            return []

    def actions(self) -> dict[str, Any]:
        return {"list_root": self.list_root}

    def list_root(self) -> dict[str, Any]:
        return {"root": str(self.root), "entries": self.observe()[0]["value"]["entries"] if self.observe() else []}
