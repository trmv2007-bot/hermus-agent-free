"""Canonical WorldState subsystem (Rebuild spec §5, §12; clean-slate §9).

World state describes what is believed to be true **now**; it is distinct from
memory (what HERMUS learned over time) and evidence (proof something happened).

The canonical implementation is :class:`core.computer.world_state.WorldState` —
the one the live computer stack uses (computer_agent, planner, replanner,
state_machine, task_store). The parallel ``WorldStateV2`` duplicate was never
wired into any functional caller and has been removed. This package exposes a
single facade (``WorldStateFacade``) that is the one writable world-state path.
"""

from .world import WorldStateFacade, get_world_state, migrate_world_state, detect_legacy

__all__ = ["WorldStateFacade", "get_world_state", "migrate_world_state", "detect_legacy"]
