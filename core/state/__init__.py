"""Canonical WorldState subsystem (Rebuild spec §5, §12).

World state describes what is believed to be true **now**; it is distinct from
memory (what HERMUS learned over time). The canonical implementation is
:class:`core.computer.world_state_v2.WorldStateV2`. This package exposes a single
facade, provides schema migration from the legacy :class:`core.computer.world_state.WorldState`
v1, and removes v1 from active imports.
"""

from .world import WorldStateFacade, get_world_state, migrate_world_state, detect_legacy

__all__ = ["WorldStateFacade", "get_world_state", "migrate_world_state", "detect_legacy"]
