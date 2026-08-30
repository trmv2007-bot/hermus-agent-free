"""Canonical WorldState facade.

The canonical implementation is :class:`core.computer.world_state_v2.WorldStateV2`.
The legacy v1 :class:`core.computer.world_state.WorldState` is retained only as a
read-only migration source; it is never the writable path in production.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

FACADE_V2 = "v2"
FACADE_LEGACY = "legacy"


class WorldStateFacade:
    """One canonical world-state API over WorldStateV2."""

    def __init__(self, state: Any = None, *, canonical: str = FACADE_V2,
                 state_path: Optional[str] = None):
        if state is None:
            from ..computer.world_state_v2 import WorldStateV2  # type: ignore
            if state_path and Path(state_path).exists():
                state = WorldStateV2.load(state_path)
            else:
                state = WorldStateV2()
        self._state = state
        self._canonical = canonical

    @property
    def canonical(self) -> str:
        return self._canonical

    # -- facade passthrough ------------------------------------------------------
    def observe(self, *args, **kw) -> dict[str, Any]:
        return self._state.observe(*args, **kw)

    def add_target(self, *args, **kw) -> Any:
        return self._state.add_target(*args, **kw)

    def find_target(self, *args, **kw) -> Any:
        return self._state.find_target(*args, **kw)

    def reset(self, *args, **kw) -> Any:
        return self._state.reset(*args, **kw)

    def begin_task(self, *args, **kw) -> Any:
        return self._state.begin_task(*args, **kw)

    def finish_task(self, *args, **kw) -> Any:
        return self._state.finish_task(*args, **kw)

    def satisfies_condition(self, *args, **kw) -> Any:
        return self._state.satisfies_condition(*args, **kw)

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
    def visible_targets(self) -> list[str]:
        return getattr(self._state, "visible_targets", []) or []

    @property
    def dialogs(self) -> list[str]:
        return getattr(self._state, "dialogs", []) or []

    @property
    def targets(self) -> list[Any]:
        return getattr(self._state, "targets", []) or []

    @property
    def revision(self) -> int:
        return getattr(self._state, "revision", 0)

    @property
    def observations(self) -> list[Any]:
        return getattr(self._state, "observations", []) or []


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
    """True if a legacy v1 world-state file exists (schema detection)."""
    p = Path(path)
    if not p.exists():
        return False
    try:
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
        return not any(k in data for k in ("context", "task_ctx", "revision"))
    except Exception:
        return True


def migrate_world_state(legacy_path: str, *, dry_run: bool = False,
                        marker_path: Optional[str] = None,
                        out_path: Optional[str] = None) -> dict[str, Any]:
    """Migrate a legacy v1 world-state dict into the canonical v2 schema once."""
    from ..computer.world_state_v2 import WorldStateV2  # type: ignore

    p = Path(legacy_path)
    if not p.exists():
        return {"success": False, "error": f"legacy world-state {p} not found"}
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    canonical = WorldStateV2.from_legacy(data)
    if dry_run:
        return {"success": True, "dry_run": True, "v2_keys": list(canonical.to_dict().keys())}
    out = Path(out_path) if out_path else p.with_suffix(".v2.json")
    canonical.save(str(out))
    return {"success": True, "written": str(out), "revision": canonical.revision}
