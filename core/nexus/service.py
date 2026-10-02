"""Legacy compatibility adapter for the old Nexus service name.

Nexus is no longer a backend architecture. The canonical interaction model is
``core.hermus_engine``. This adapter exists only so older imports continue to
work while callers migrate to the engine.
"""
from __future__ import annotations

from typing import Any

from core.hermus_engine import Intent, engine


class NexusService:
    """Compatibility adapter; delegates all execution to ``HermusEngine``."""

    @property
    def _missions(self) -> dict[str, Any]:
        """Legacy read-only view; mission truth lives in the engine/run bus."""
        return {}

    def submit(self, command: Any) -> dict[str, Any]:
        return engine.submit(
            Intent(
                text=str(command.text),
                user_id=str(command.user_id or "default"),
                session_id=command.session_id,
                channel=str(command.channel or "unknown"),
                mode=str(command.mode or "chat"),
                priority=int(command.priority or 0),
                prefer=(str(command.metadata.get("prefer")) if command.metadata.get("prefer") else None),
            )
        )

    def state(self, *, user_id: str = "default") -> dict[str, Any]:
        return engine.snapshot(user_id=user_id)

    def mission(self, mission_id: str) -> dict[str, Any] | None:
        return engine.run(mission_id)

    def cancel(self, run_id: str) -> bool:
        return engine.cancel(run_id)

    def steer(self, run_id: str, text: str) -> bool:
        return engine.steer(run_id, text)

    def sync_runs(self) -> None:
        return None


nexus = NexusService()
