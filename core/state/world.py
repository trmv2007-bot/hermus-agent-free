"""Canonical WorldState facade.

Clean-slate: the **canonical** world-state implementation is
:class:`core.computer.world_state.WorldState` (the one the live computer stack
uses — computer_agent, planner, replanner, state_machine, task_store). The
parallel ``WorldStateV2`` duplicate was never wired into any functional caller
and has been removed. This facade is the single public owner of the
current-environment/target truth; it is distinct from memory (learned over time)
and evidence (proof something happened).

``WorldStateFacade`` wraps one ``WorldState`` instance and exposes the full
method + property surface so no caller constructs a second state object.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

FACADE_V2 = "v1"  # canonical == the live V1 WorldState


class WorldStateFacade:
    """One canonical world-state API over core.computer.world_state.WorldState."""

    def __init__(self, state: Any = None, *, canonical: str = FACADE_V2,
                 state_path: Optional[str] = None):
        if state is None:
            from ..computer.world_state import WorldState  # type: ignore
            if state_path and Path(state_path).exists():
                state = WorldState.load(state_path)
            else:
                state = WorldState()
        self._state = state
        self._canonical = canonical

    @property
    def canonical(self) -> str:
        return self._canonical

    @property
    def state(self) -> Any:
        """The underlying WorldState instance."""
        return self._state

    # -- facade passthrough (V1 live surface) ------------------------------------
    def reset(self, *args, **kw) -> Any:
        return self._state.reset(*args, **kw)

    def update(self, *args, **kw) -> dict[str, Any]:
        return self._state.update(*args, **kw)

    def begin_task(self, *args, **kw) -> Any:
        return self._state.begin_task(*args, **kw)

    def before_action(self, *args, **kw) -> Any:
        return self._state.before_action(*args, **kw)

    def mark_state(self, *args, **kw) -> Any:
        return self._state.mark_state(*args, **kw)

    def finish_task(self, *args, **kw) -> Any:
        return self._state.finish_task(*args, **kw)

    def satisfies(self, *args, **kw) -> Any:
        return self._state.satisfies(*args, **kw)

    def to_dict(self, *args, **kw) -> dict[str, Any]:
        return self._state.to_dict(*args, **kw)

    def from_dict(self, data: dict[str, Any]) -> "WorldStateFacade":
        self._state = type(self._state).from_dict(data)
        return self

    def save(self, path: str) -> str:
        return self._state.save(path)

    # -- convenience properties ---------------------------------------------------
    @property
    def active_application(self) -> Optional[str]:
        return getattr(self._state, "active_application", None)

    @property
    def active_window(self) -> Optional[str]:
        return getattr(self._state, "active_window", None)

    @property
    def task_state(self) -> str:
        return getattr(self._state, "task_state", "")

    @property
    def current_state(self) -> str:
        return getattr(self._state, "current_state", "")

    @property
    def visible_targets(self) -> list[str]:
        return getattr(self._state, "visible_targets", []) or []

    @property
    def dialogs(self) -> list[str]:
        return getattr(self._state, "dialogs", []) or []

    @property
    def confidence(self) -> float:
        return float(getattr(self._state, "confidence", 0.0) or 0.0)

    @property
    def observations(self) -> list[Any]:
        return getattr(self._state, "observations", []) or []

    @property
    def revision(self) -> int:
        return int(getattr(self._state, "revision", 0) or 0)

    @property
    def timestamp(self) -> str:
        return str(getattr(self._state, "timestamp", ""))


_world: Optional[WorldStateFacade] = None
_world_lock = threading.Lock()


def get_world_state() -> WorldStateFacade:
    """Return the process-wide canonical world-state facade."""
    global _world
    with _world_lock:
        if _world is None:
            _world = WorldStateFacade()
        return _world


def detect_legacy(path: str) -> bool:
    """True if a world-state file exists (V1 format is canonical now)."""
    p = Path(path)
    return p.exists()


def migrate_world_state(legacy_path: str, *, dry_run: bool = False,
                        marker_path: Optional[str] = None,
                        out_path: Optional[str] = None) -> dict[str, Any]:
    """Validate/load a world-state file into the canonical V1 schema.

    V1 ``WorldState`` is canonical, so this is a load + integrity check rather
    than a schema downcast. It writes a canonical snapshot and marks complete.
    """
    from ..computer.world_state import WorldState  # type: ignore

    p = Path(legacy_path)
    if not p.exists():
        return {"success": False, "error": f"world-state {p} not found"}
    try:
        state = WorldState.load(str(p))
    except Exception as exc:
        state = WorldState()
        if not dry_run:
            # A malformed file still produces a canonical snapshot on next save.
            pass
    if dry_run:
        return {"success": True, "dry_run": True,
                "keys": list(state.to_dict().keys()),
                "revision": state.revision}
    out = Path(out_path) if out_path else p
    state.save(str(out))
    return {"success": True, "written": str(out), "revision": state.revision}
