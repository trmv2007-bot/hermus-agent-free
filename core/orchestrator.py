"""Compatibility entry point for HERMUS interaction orchestration.

The product-facing architecture now lives in ``core.nexus``. This module stays
as a small compatibility facade so existing imports and integrations continue
to work while the backend migrates to the Nexus model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.nexus.models import NexusCommand
from core.nexus.service import nexus


@dataclass(frozen=True)
class CommandResult:
    accepted: bool
    run_id: str | None = None
    job_id: str | None = None
    mission_id: str | None = None
    status: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "run_id": self.run_id,
            "job_id": self.job_id,
            "mission_id": self.mission_id,
            "status": self.status,
            "error": self.error,
        }


class HERMUSOrchestrator:
    """Stable facade over the canonical Nexus interaction architecture."""

    def state(self, *, user_id: str = "default") -> dict[str, Any]:
        nexus.sync_runs()
        return nexus.state(user_id=user_id)

    def submit(
        self,
        text: str,
        *,
        user_id: str = "default",
        session_id: str | None = None,
        platform: str = "nexus",
        mode: str = "chat",
        prefer: str | None = None,
        priority: int = 0,
    ) -> CommandResult:
        result = nexus.submit(
            NexusCommand(
                text=text,
                user_id=user_id,
                session_id=session_id,
                channel=platform,
                mode=mode,
                priority=priority,
                metadata={"prefer": prefer} if prefer else {},
            )
        )
        return CommandResult(
            accepted=bool(result.get("accepted")),
            run_id=result.get("run_id"),
            job_id=result.get("job_id"),
            mission_id=result.get("mission_id"),
            status=str(result.get("status") or ""),
            error=str(result.get("error") or ""),
        )

    def cancel(self, run_id: str) -> bool:
        return nexus.cancel(run_id)

    def steer(self, run_id: str, text: str) -> bool:
        return nexus.steer(run_id, text)

    def mission(self, mission_id: str) -> dict[str, Any] | None:
        return nexus.mission(mission_id)


orchestrator = HERMUSOrchestrator()
