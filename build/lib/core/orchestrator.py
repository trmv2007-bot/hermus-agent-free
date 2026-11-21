"""Compatibility entry point for HERMUS interaction orchestration.

The canonical backend boundary is ``core.hermus_engine``. This facade keeps
existing integrations stable while they migrate away from UI-specific Nexus
concepts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.hermus_engine import Intent, engine


@dataclass(frozen=True)
class CommandResult:
    accepted: bool
    run_id: str | None = None
    job_id: str | None = None
    status: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "run_id": self.run_id,
            "job_id": self.job_id,
            "status": self.status,
            "error": self.error,
        }


class HERMUSOrchestrator:
    """Compatibility facade over the canonical HERMUS engine."""

    def state(self, *, user_id: str = "default") -> dict[str, Any]:
        snapshot = engine.snapshot(user_id=user_id)
        execution = snapshot.get("execution", {})
        # Keep the historical shape for integrations that have not migrated
        # yet. The source of truth is still the engine snapshot above.
        return {
            **snapshot,
            "runs": execution.get("runs", []),
            "queue": execution.get("queue", {}),
            "active_jobs": execution.get("active", []),
            "recent_jobs": execution.get("recent", []),
        }

    def submit(
        self,
        text: str,
        *,
        user_id: str = "default",
        session_id: str | None = None,
        platform: str = "unknown",
        mode: str = "chat",
        prefer: str | None = None,
        priority: int = 0,
    ) -> CommandResult:
        result = engine.submit(
            Intent(
                text=text,
                user_id=user_id,
                session_id=session_id,
                channel=platform,
                mode=mode,
                priority=priority,
                prefer=prefer,
            )
        )
        return CommandResult(
            accepted=bool(result.get("accepted")),
            run_id=result.get("run_id"),
            job_id=result.get("job_id"),
            status=str(result.get("status") or ""),
            error=str(result.get("error") or ""),
        )

    def cancel(self, run_id: str) -> bool:
        return engine.cancel(run_id)

    def steer(self, run_id: str, text: str) -> bool:
        return engine.steer(run_id, text)

    def run(self, run_id: str) -> dict[str, Any] | None:
        return engine.run(run_id)

    def capabilities(self) -> dict[str, Any]:
        return engine.capabilities()


orchestrator = HERMUSOrchestrator()
