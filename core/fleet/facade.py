"""AgentManager compatibility facade over the Fleet Registry (roadmap step 3).

Legacy ``core/agent_manager.py`` (named-agent registry + delegation) is retired
onto :class:`core.fleet.registry.FleetRegistry`. This module keeps the **legacy
public surface** importable and call-compatible while routing every operation to
the canonical registry:

* ``create(name, role, model, persona)`` → ``FleetRegistry.spawn(...)``
* ``list()`` / ``status(name)`` → live roster state (never a fabricated flag)
* ``start(name)`` → ``FleetRegistry.resume`` when PAUSED, else honest refusal
  (there is no process to start); ``stop(name)`` → ``FleetRegistry.pause``
* ``submit_job(name, job)`` → ``FleetRegistry.assign(agent_id, task,
  idempotency_key)`` — **synchronous**, not queued
* ``job_status`` / ``wait_job`` → honest refusal (``assign`` executes inline;
  there are no job ids to poll)
* ``watchdog_tick(...)`` → honest report of registry state (no fabricated
  restarts)

Honesty rule: response **shapes** stay compatible where cheap (``success``,
``name``, ``status``, ...), but the facade never fabricates success. Where the
old semantics cannot be honored it returns
``{"success": False, "error": "moved to FleetRegistry: <op>"}``.

Known compat gaps (documented, not hidden):

* roles: ``LiveAgent`` has no role field, so ``role`` is validated and echoed by
  :meth:`AgentManagerFacade.create` but not persisted by the registry;
* ``submit_job`` used to enqueue a canonical Job and return a pollable
  ``job_id``; the registry executes synchronously, so ``queued`` is always
  ``False`` and ``job_status``/``wait_job`` honestly refuse.

Tests inject a stub ``chat_fn`` — this module never calls the network itself.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from core.log import get_logger

from .registry import (
    DESTROYED,
    PAUSED,
    FleetRegistry,
    IllegalTransition,
    RegistryError,
)

logger = get_logger(__name__)

# Legacy ROLES, kept only as an import fallback for `_legacy_roles`.
_FALLBACK_ROLES = (
    "researcher",
    "coder",
    "system-monitor",
    "scheduler",
    "memory-manager",
    "watchdog",
    "computer-operator",
    "coordinator",
    "generic",
)


def _moved(op: str, **extra: Any) -> dict[str, Any]:
    """The honest refusal shape used whenever legacy semantics cannot be honored."""
    return {"success": False, "error": f"moved to FleetRegistry: {op}", **extra}


def _legacy_roles() -> tuple[str, ...]:
    """The legacy role vocabulary (single owner: ``core.agent_roles``)."""
    from core.agent_roles import ROLES

    return tuple(ROLES)


class AgentManagerFacade:
    """Legacy ``AgentManager`` surface routed onto :class:`FleetRegistry`.

    ``registry`` may be injected (tests); the default is the production
    ``FleetRegistry(get_bus())``, created lazily on first use so importing this
    module never touches the filesystem.
    """

    def __init__(self, registry: FleetRegistry | None = None) -> None:
        self._registry = registry

    # ------------------------------------------------------------------ #
    # plumbing
    # ------------------------------------------------------------------ #
    def _reg(self) -> FleetRegistry:
        if self._registry is None:
            from .bus import get_bus  # lazy: no import-time layering cycle

            self._registry = FleetRegistry(get_bus())
        return self._registry

    def _resolve(self, name: str):
        """Look a roster agent up by legacy name (registry keys by ``agent_id``)."""
        wanted = str(name or "")
        for agent in self._reg().list():
            if agent.name == wanted:
                return agent
        folded = wanted.casefold()
        for agent in self._reg().list():
            if agent.name.casefold() == folded:
                return agent
        return None

    @staticmethod
    def _view(agent) -> dict[str, Any]:
        """One legacy-shaped status dict carrying the *live* registry state."""
        return {
            "success": True,
            "name": agent.name,
            "agent_id": agent.agent_id,
            "persona": agent.persona,
            "model": agent.model,
            "state": agent.state,
            "status": agent.state,
            "alive": agent.state != DESTROYED,
            "registry": "fleet-registry",
            "queue": None,
        }

    # ------------------------------------------------------------------ #
    # registry (legacy: identity + lifecycle flags)
    # ------------------------------------------------------------------ #
    def create(
        self,
        name: str,
        role: str = "generic",
        model: str | None = None,
        persona: str | None = None,
    ) -> dict[str, Any]:
        """Legacy identity create → ``FleetRegistry.spawn`` (a real, durable spawn)."""
        roles = _legacy_roles()
        if role not in roles:
            return {"success": False, "error": f"unknown role '{role}' (choose {roles})"}
        if self._resolve(name) is not None:
            return {"success": False, "error": f"agent '{name}' already exists"}
        try:
            agent = self._reg().spawn({"name": name, "persona": persona or "", "model": model or ""})
        except (RegistryError, IllegalTransition) as exc:
            return {"success": False, "error": str(exc)}
        # Compat note: LiveAgent has no role field — role is validated + echoed,
        # not persisted (the registry is the identity owner now).
        return {
            "success": True,
            "name": agent.name,
            "role": role,
            "agent_id": agent.agent_id,
            "state": agent.state,
        }

    def list(self) -> list[dict[str, Any]]:
        """Every roster agent as a legacy-shaped status dict (live states)."""
        return [self._view(agent) for agent in self._reg().list()]

    def start(self, name: str, handler=None, daemon: bool = True) -> dict[str, Any]:
        """Legacy "start" → ``FleetRegistry.resume`` (PAUSED → IDLE).

        There is no process to start in the fleet model; anything other than a
        paused agent is refused honestly rather than reported "ready".
        ``handler``/``daemon`` are accepted for signature compatibility only.
        """
        agent = self._resolve(name)
        if agent is None:
            return {"success": False, "error": f"agent '{name}' not found (create it first)"}
        if agent.state != PAUSED:
            return _moved("start", name=agent.name, status=agent.state)
        try:
            resumed = self._reg().resume(agent.agent_id)
        except (RegistryError, IllegalTransition) as exc:
            return {"success": False, "name": agent.name, "error": str(exc)}
        return {"success": True, "name": resumed.name, "status": resumed.state, "registry": "fleet-registry"}

    def stop(self, name: str) -> dict[str, Any]:
        """Legacy "stop" → ``FleetRegistry.pause`` (cursor freeze, no signal sent)."""
        agent = self._resolve(name)
        if agent is None:
            return {"success": False, "error": f"agent '{name}' not found"}
        try:
            paused = self._reg().pause(agent.agent_id)
        except (RegistryError, IllegalTransition) as exc:
            return {"success": False, "name": agent.name, "error": str(exc)}
        return {"success": True, "name": paused.name, "status": paused.state, "registry": "fleet-registry"}

    def status(self, name: str) -> dict[str, Any]:
        """Identity + live registry state (never a fabricated "registered")."""
        agent = self._resolve(name)
        if agent is None:
            return {"success": False, "error": f"agent '{name}' not found"}
        return self._view(agent)

# __APPEND_MARKER__

    @staticmethod
    def _idempotency_key(name: str, job: dict[str, Any]) -> str:
        """Stable dedup key: explicit ``job['id']``/``job['job_id']`` else a
        content hash of ``(agent, task)`` so a re-submitted job is deduped."""
        explicit = str(job.get("id") or job.get("job_id") or "").strip()
        if explicit:
            return explicit
        task = str(job.get("task") or job.get("goal") or "")
        digest = hashlib.sha256(f"{name}\x00{task}".encode("utf-8")).hexdigest()
        return f"legacy-{digest[:16]}"
    # ------------------------------------------------------------------ #
    # delegation (legacy: canonical Job queue → fleet: synchronous assign)
    # ------------------------------------------------------------------ #
    def submit_job(self, name: str, job: dict[str, Any]) -> dict[str, Any]:
        """Legacy submit → ``FleetRegistry.assign`` (synchronous, idempotent).

        The job dict's ``task``/``goal`` is executed inline through the
        registry's injected ``chat_fn``; the response keeps the legacy keys
        (``success``/``name``/``job_id``/``status``) with ``queued`` honestly
        ``False`` because nothing is enqueued.
        """
        agent = self._resolve(name)
        if agent is None:
            return {"success": False, "error": f"agent '{name}' not found"}
        job = dict(job or {})
        task = str(job.get("task") or job.get("goal") or "").strip()
        if not task:
            return {"success": False, "error": "job has no task", "name": agent.name}
        try:
            result = self._reg().assign(
                agent.agent_id,
                task,
                idempotency_key=self._idempotency_key(agent.name, job),
            )
        except (RegistryError, IllegalTransition) as exc:
            return {"success": False, "name": agent.name, "error": str(exc)}
        except Exception as exc:  # chat_fn failure already recorded by the registry
            return {"success": False, "name": agent.name, "error": f"task failed: {exc}"}
        if result.get("deduplicated"):
            return {
                "success": True,
                "name": agent.name,
                "job_id": result.get("task_id"),
                "queued": False,
                "executed": False,
                "status": "deduplicated",
            }
        if result.get("blocked"):
            return {
                "success": True,
                "name": agent.name,
                "job_id": result.get("task_id"),
                "queued": False,
                "executed": False,
                "status": "blocked",
                "needs_approval": result.get("needs_approval"),
            }
        return {
            "success": True,
            "name": agent.name,
            "job_id": result.get("task_id"),
            "queued": False,
            "executed": True,
            "status": "succeeded",
            "result": result.get("content"),
            "tokens": result.get("tokens"),
        }

    def job_status(self, name: str, job_id: str) -> dict[str, Any]:
        """Honest refusal: ``assign`` executes inline; there is nothing to poll."""
        agent = self._resolve(name)
        return _moved(
            "job_status (tasks execute synchronously via assign; no job ids to poll)",
            name=agent.name if agent else name,
            job_id=job_id,
            status="unknown",
        )

    def wait_job(
        self,
        name: str,
        job_id: str,
        timeout: float = 120.0,
        interval: float = 0.2,
    ) -> dict[str, Any]:
        """Same honest refusal as :meth:`job_status` (nothing to wait on)."""
        return self.job_status(name, job_id)

    # ------------------------------------------------------------------ #
    # watchdog (legacy: queue reconciliation → fleet: honest state report)
    # ------------------------------------------------------------------ #
    def watchdog_tick(self, stale_seconds: float = 30.0, restart: bool = True) -> dict[str, Any]:
        """Report the registry state honestly — no fabricated restarts.

        The Fleet Registry detects stalls via its own BLOCKED/lease semantics;
        this tick only surfaces the roster. ``restart`` is accepted for
        signature compatibility and deliberately ignored.
        """
        agents = self._reg().list()
        return {
            "stale": [],
            "revived": [],
            "errors": [],
            "recovery_owner": "fleet-registry",
            "agents": [agent.name for agent in agents],
            "states": {agent.name: agent.state for agent in agents},
            "restart_requested": bool(restart),
            "tick": datetime.now().isoformat(),
        }


#: Drop-in replacement for the legacy ``core.agent_manager.agent_manager``.
fleet_facade = AgentManagerFacade()

#: Legacy import name. ``from core.agent_manager import agent_manager`` moved to
#: ``from core.fleet.facade import agent_manager`` — one line, same call surface.
agent_manager = fleet_facade

# Legacy vocabulary re-exports (single owner: ``core.agent_roles``).
from core.agent_roles import ROLES, ROLE_HANDLERS, register_handler  # noqa: E402

__all__ = ["AgentManagerFacade", "fleet_facade", "agent_manager", "ROLES", "ROLE_HANDLERS", "register_handler"]
