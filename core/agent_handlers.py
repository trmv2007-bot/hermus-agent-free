"""Agent job handlers for the canonical Job queue.

This module contains the handler builders for agent roles that are registered
on the canonical Job queue. These were previously in core/agent_manager.py.
"""

from __future__ import annotations

from typing import Any

from core.config import config
from core.workspace import workspace


def make_agent_general_handler():
    """Canonical ``agent.general`` Job handler — runs the universal runtime."""

    def handle(ctx) -> dict[str, Any]:
        payload = dict(ctx.payload)
        agent_name = str(payload.get("agent") or payload.get("name") or "generic")
        from core.workspace import workspace as _workspace

        try:
            cfg = _workspace.read_json(_workspace.dirs["agents"] / agent_name / "agent.json")
        except Exception:
            cfg = {"name": agent_name, "role": "generic", "model": None}
        # Allow an explicit role-specific in-process handler override.
        from core.agent_roles import ROLE_HANDLERS

        role = str(payload.get("role") or cfg.get("role") or "generic")
        handler = ROLE_HANDLERS.get(role)
        if handler is not None:
            return {"ok": True, "result": handler(payload)}
        return _general_agent_handler(cfg)(payload)

    return handle


def _general_agent_handler(config: dict[str, Any]):
    """Build the handler that runs a general background task through the universal runtime."""

    def handle(job: dict[str, Any]) -> dict[str, Any]:
        from core.agent import HermusAgent
        from core.runtime import execute as runtime_execute

        task = str(job.get("task") or job.get("goal") or "")
        if not task:
            return {"ok": False, "error": "job has no task"}
        agent = HermusAgent(
            model=config.get("model"),
            session_id=f"background_{config.get('name', 'agent')}",
        )
        result = runtime_execute(task, agent=agent, prefer=str(job.get("prefer") or "auto"))
        ok = bool(result.get("success", True)) if isinstance(result, dict) else bool(result)
        if isinstance(result, dict):
            result = {"ok": ok, "result": result}
        else:
            result = {"ok": ok, "result": str(result)}
        return result

    return handle


def make_agent_computer_handler():
    """Canonical ``agent.computer`` Job handler — desktop control."""

    def handle(ctx) -> dict[str, Any]:
        return _computer_agent_handler(dict(ctx.payload))

    return handle


def _computer_agent_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle a computer task via the computer agent."""
    from core.agent import HermusAgent
    from core.runtime import execute as runtime_execute

    task = str(payload.get("task") or "")
    if not task:
        return {"ok": False, "error": "computer job has no task"}
    agent = HermusAgent(
        model=payload.get("model") or config.model,
        session_id=f"computer_{payload.get('agent', 'agent')}",
        mode="computer",
    )
    result = runtime_execute(task, agent=agent, prefer="auto")
    return {"ok": bool(result.get("success", True)), "computer_task": result}


def register_agent_handlers(queue, *, overwrite: bool = False) -> list[str]:
    """Register the agent-role Job kinds on the canonical queue. Idempotent.

    By default it does **not** clobber a handler that is already registered, so
    tests/callers may inject a custom ``agent.general``/``agent.computer`` handler.
    """
    for kind, build in (("agent.general", make_agent_general_handler), ("agent.computer", make_agent_computer_handler)):
        if kind in getattr(queue, "handlers", {}) and not overwrite:
            continue
        queue.register(kind, build(), overwrite=overwrite)
    return ["agent.general", "agent.computer"]


# Legacy re-export surface: single owner is ``core.agent_roles``.
from core.agent_roles import ROLES, ROLE_HANDLERS, register_handler  # noqa: E402, F401

__all__ = [
    "make_agent_general_handler",
    "make_agent_computer_handler",
    "register_agent_handlers",
    "ROLES",
    "ROLE_HANDLERS",
    "register_handler",
]