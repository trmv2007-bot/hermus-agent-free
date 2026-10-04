"""Bounded HERMUS context fabric.

A read-only aggregator for the handful of facts an interaction usually needs.
It delegates to canonical owners: Workspace, Presence/Executive, WorldModel,
MemoryFacade, ModelGateway and the existing attention projection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .executive import executive_brain
from .memory import memory
from .models import get_model_gateway
from .presence_kernel import presence_kernel
from .workspace import workspace
from .world_awareness import world_awareness


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


class ContextFabric:
    def build(
        self,
        query: str = "",
        *,
        user_id: str = "default",
        project: str | None = None,
        memory_limit: int = 6,
    ) -> dict[str, Any]:
        active_project = str(project or workspace.current_project() or workspace.active_project() or "default")
        packet: dict[str, Any] = {
            "version": 1,
            "generated_at": _now(),
            "query": str(query or "")[:1000],
            "user_id": str(user_id or "default"),
            "project": active_project,
        }

        try:
            packet["world"] = world_awareness.status()
        except Exception:
            packet["world"] = {"fresh": False}

        try:
            packet["presence"] = presence_kernel.snapshot(
                user_id=str(user_id or "default"),
                include_events=False,
            )
        except Exception:
            try:
                packet["presence"] = self._minimal_presence(user_id)
            except Exception:
                packet["presence"] = {"presence": {"state": "unknown"}}

        try:
            packet["goals"] = _safe_list(executive_brain.active_goals(limit=8))
        except Exception:
            packet["goals"] = []

        if query.strip():
            try:
                packet["memory"] = memory.recall(
                    query,
                    limit=max(1, min(int(memory_limit), 12)),
                    kinds=["episodic", "semantic", "procedural", "project"],
                    project=active_project,
                )
            except Exception:
                packet["memory"] = []
        else:
            packet["memory"] = []

        try:
            selected = get_model_gateway().selected_models()
            packet["models"] = selected if isinstance(selected, dict) else {}
        except Exception:
            packet["models"] = {}

        # Reduce the presence projection into the interaction-critical slice.
        presence_data = packet.get("presence") or {}
        packet["attention"] = _safe_list(presence_data.get("attention"))[:8]
        packet["runtime"] = dict(presence_data.get("runtime") or {})
        packet["runtime"].pop("queue", None)
        packet["summary"] = dict(presence_data.get("summary") or {})

        return packet

    @staticmethod
    def _minimal_presence(user_id: str) -> dict[str, Any]:
        state = executive_brain.snapshot()
        return {
            "presence": {
                "state": str(state.get("state") or "idle"),
                "user_id": user_id,
            },
            "summary": {"state": str(state.get("state") or "idle")},
            "attention": [],
            "runtime": {},
        }


context_fabric = ContextFabric()

__all__ = ["ContextFabric", "context_fabric"]
