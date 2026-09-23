"""Named-agent role vocabulary and in-process role handlers.

Rehomed from the retired ``core/agent_manager.py`` (roadmap step 3: the split-brain
registries are merged onto :class:`core.fleet.registry.FleetRegistry` and never
re-implemented next to it). This module is the single owner of:

* ``ROLES`` — the legacy role vocabulary (validated by the fleet facade);
* ``ROLE_HANDLERS`` — ``role -> handler(job_dict) -> result_dict`` for custom
  in-process role logic, which also feeds the canonical queue handler builders
  in :mod:`core.agent_handlers`.
"""

from __future__ import annotations

from typing import Any, Callable

ROLES = (
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

# role -> handler(job_dict) -> result_dict. Custom role logic may register here;
# it is also the source of the canonical queue handler builders.
ROLE_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def register_handler(role: str, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    """Register a custom in-process role handler (called by ``{role}_agent_handler``)."""
    ROLE_HANDLERS[role] = handler


__all__ = ["ROLES", "ROLE_HANDLERS", "register_handler"]
