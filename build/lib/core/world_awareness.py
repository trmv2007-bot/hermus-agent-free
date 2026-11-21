"""Phase 10 live world-awareness coordinator.

This layer reconciles read-only observations into the canonical WorldModel.
It adds freshness, provenance, change detection and a bounded host/workspace
view without creating a second execution engine or granting new permissions.
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .connectors import ConnectorRegistry, connector_registry, register_builtin_connectors
from .world_model import WorldModel, world_model


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8", "replace")).hexdigest()[:16]


class WorldAwareness:
    """Read-only reconciliation coordinator for HERMUS situational awareness."""

    def __init__(self, *, world: WorldModel | None = None, registry: ConnectorRegistry | None = None):
        self.world = world or world_model
        self.registry = registry or connector_registry
        self._last_digest: str | None = None

    def refresh(self, *, workspace_root: str | Path | None = None, include_processes: bool = True) -> dict[str, Any]:
        root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        register_builtin_connectors(self.registry, workspace_root=root)

        connector_results = []
        for status in self.registry.statuses():
            if status.get("state") == "disabled":
                continue
            name = status.get("name")
            if not name:
                continue
            try:
                connector_results.extend(self.registry.refresh(name))
            except Exception as exc:
                self.world.emit("world_observation_error", {"source": name, "error": str(exc)[:300]}, source="world-awareness")

        facts: list[dict[str, Any]] = []
        facts.extend(self._git_facts(root))
        facts.extend(self._browser_facts())
        if include_processes:
            facts.extend(self._process_facts())

        self.world.ingest("world-awareness", facts, permission_scope="system.read")

        snapshot = self.world.snapshot()
        # Change detection is based on the fresh observations, not the durable
        # world store (which intentionally retains historical facts/events).
        digest = _digest(facts)
        changed = self._last_digest is not None and digest != self._last_digest
        self._last_digest = digest

        self.world.observe(
            "world", "last_refresh_at", _now(), source="world-awareness", confidence=1.0, permission_scope="system.read"
        )
        self.world.observe(
            "world", "observation_digest", digest, source="world-awareness", confidence=1.0, permission_scope="system.read"
        )
        self.world.emit(
            "world_reconciled",
            {
                "changed": changed,
                "fact_count": len(snapshot["facts"]),
                "connector_count": len(connector_results),
                "source": "world-awareness",
            },
            source="world-awareness",
        )
        snapshot = self.world.snapshot()
        snapshot["awareness"] = {
            "refreshed_at": _now(),
            "changed": changed,
            "observation_digest": digest,
            "connectors": self.registry.statuses(),
        }
        return snapshot

    def status(self, *, max_age_seconds: float = 60.0) -> dict[str, Any]:
        snapshot = self.world.snapshot()
        refresh = self.world.get("world", "last_refresh_at")
        age_seconds = None
        fresh = False
        if refresh:
            try:
                observed = datetime.fromisoformat(refresh.value)
                age_seconds = max(0.0, (datetime.now(timezone.utc) - observed).total_seconds())
                fresh = age_seconds <= max(0.0, float(max_age_seconds))
            except (TypeError, ValueError):
                pass
        return {
            "fresh": fresh,
            "age_seconds": age_seconds,
            "fact_count": len(snapshot["facts"]),
            "event_count": len(snapshot["recent_events"]),
            "observation_digest": (
                self.world.get("world", "observation_digest").value
                if self.world.get("world", "observation_digest") is not None
                else None
            ),
            "connectors": self.registry.statuses(),
        }

    @staticmethod
    def _browser_facts() -> list[dict[str, Any]]:
        try:
            from tools import browser as browser_module
        except Exception:
            return []
        page = getattr(browser_module, "_page", None)
        if page is None:
            return [
                {
                    "subject": "browser",
                    "predicate": "state",
                    "value": {"active": False, "reason": "no_active_session"},
                    "confidence": 1.0,
                }
            ]
        try:
            return [
                {
                    "subject": "browser",
                    "predicate": "state",
                    "value": {"active": True, "url": str(page.url), "title": str(page.title())[:500]},
                    "confidence": 0.95,
                }
            ]
        except Exception as exc:
            return [
                {
                    "subject": "browser",
                    "predicate": "state",
                    "value": {"active": True, "state_error": str(exc)[:200]},
                    "confidence": 0.5,
                }
            ]

    @staticmethod
    def _git_facts(root: Path) -> list[dict[str, Any]]:
        if not (root / ".git").exists():
            return []
        facts: list[dict[str, Any]] = []
        commands = {
            "commit": ["git", "rev-parse", "HEAD"],
            "branch": ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            "status": ["git", "status", "--porcelain"],
        }
        for predicate, command in commands.items():
            try:
                result = subprocess.run(command, cwd=str(root), capture_output=True, text=True, timeout=5, check=False)
                if result.returncode != 0:
                    continue
                value = result.stdout
                if predicate == "status":
                    # Preserve porcelain's two-character status prefix; callers
                    # use it to distinguish staged vs worktree changes.
                    lines = value.splitlines()
                    value = {"clean": not bool(value.strip()), "changed_paths": lines[:100]}
                else:
                    value = value.strip()
                facts.append({"subject": "workspace.git", "predicate": predicate, "value": value, "confidence": 1.0})
            except (OSError, subprocess.SubprocessError):
                continue
        return facts

    @staticmethod
    def _process_facts() -> list[dict[str, Any]]:
        try:
            import psutil  # type: ignore
        except Exception:
            return []
        rows = []
        try:
            for proc in psutil.process_iter(["pid", "name", "status"]):
                info = proc.info
                name = str(info.get("name") or "")
                if name:
                    rows.append({"pid": info.get("pid"), "name": name, "status": info.get("status")})
        except Exception:
            return []
        rows.sort(key=lambda item: (str(item["name"]).lower(), int(item["pid"] or 0)))
        return [{"subject": "runtime", "predicate": "processes", "value": rows[:200], "confidence": 0.95}]


world_awareness = WorldAwareness()

__all__ = ["WorldAwareness", "world_awareness"]
