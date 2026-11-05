"""Memory integration for the HERMUS executive loop.

Turns mission outcomes into durable procedural/episodic memory while keeping
secrets and raw runtime payloads out of the memory store. This module is an
adapter: the canonical MemoryFacade remains the only writable memory API.
"""

from __future__ import annotations

from typing import Any

from .memory.store import MemoryFacade, get_memory

_SENSITIVE = ("password", "token", "secret", "api_key", "credential", "private_key")


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if any(s in str(k).lower() for s in _SENSITIVE) else _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value[:50]]
    if isinstance(value, str):
        return value[:4000]
    return value


class ExecutiveMemory:
    """Small policy layer connecting executive events to canonical memory."""

    def __init__(self, memory: MemoryFacade | None = None) -> None:
        self.memory = memory or get_memory()

    def recall_for_goal(self, goal: str, *, project: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        return self.memory.hybrid_recall(
            goal, project=project, kinds=["episodic", "semantic", "procedural", "project"], limit=limit
        )

    def remember_outcome(
        self,
        *,
        goal: str,
        state: str,
        verified: bool | None = None,
        result: dict[str, Any] | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        payload = _clean(result or {})
        status = "verified" if verified else state
        content = f"Mission outcome: {goal}\nState: {status}\nEvidence: {payload}"
        return self.memory.remember(
            "episodic", content, project=project, metadata={"source": "executive", "state": state, "verified": verified}
        )

    def remember_lesson(self, *, goal: str, lesson: str, project: str | None = None) -> dict[str, Any]:
        return self.memory.remember(
            "procedural",
            f"Lesson from mission '{goal}': {str(lesson)[:4000]}",
            project=project,
            metadata={"source": "executive", "kind": "lesson"},
        )


executive_memory = ExecutiveMemory()

__all__ = ["ExecutiveMemory", "executive_memory"]
