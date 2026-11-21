"""Canonical WorldState subsystem (Rebuild spec §5, §12; clean-slate §9).

World state describes what is believed to be true **now**; it is distinct from
memory (what HERMUS learned over time) and evidence (proof something happened).

The state object itself is :class:`core.computer.world_state.WorldState` — the
implementation the live computer stack drives (computer_agent, planner,
replanner, state_machine, task_store). The parallel ``WorldStateV2`` duplicate
was never wired into any functional caller and has been removed.

This package is the public boundary for world state:

* ``create_world_state`` / ``world_state_from_dict`` / ``load_world_state`` — the
  only construction paths. Production modules call these instead of instantiating
  ``WorldState`` directly, so there is one place where world state is created.
* ``WorldStateFacade`` / ``get_world_state`` — a wrapper exposing the full method
  and property surface, plus the process-wide accessor for code that wants the
  shared snapshot rather than a task-scoped one.
* ``migrate_world_state`` / ``detect_legacy`` — load + integrity check, never a
  destructive rewrite of an unreadable file.
"""

from .world import (
    WorldStateFacade,
    create_world_state,
    detect_legacy,
    get_world_state,
    load_world_state,
    migrate_world_state,
    world_state_from_dict,
)

__all__ = [
    "WorldStateFacade",
    "create_world_state",
    "world_state_from_dict",
    "load_world_state",
    "get_world_state",
    "migrate_world_state",
    "detect_legacy",
]
