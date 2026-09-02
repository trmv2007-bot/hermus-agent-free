"""Canonical WorldState facade.

Clean-slate: the **canonical** world-state implementation is
:class:`core.computer.world_state.WorldState` (the one the live computer stack
uses — computer_agent, planner, replanner, state_machine, task_store). The
parallel ``WorldStateV2`` duplicate was never wired into any functional caller
and has been removed. This facade is the single public owner of the
current-environment/target truth; it is distinct from memory (learned over time)
and evidence (proof something happened).

``WorldStateFacade`` wraps one ``WorldState`` instance and exposes the full
method + property surface, and ``create_world_state`` /
``world_state_from_dict`` / ``load_world_state`` are the only construction paths
— production modules (``ComputerAgent``, ``TaskStore``) call them instead of
instantiating ``WorldState`` directly, so no caller builds a second state object
behind this module's back. ``test_one_worldstate_owner`` enforces that.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

FACADE_V2 = "v1"  # canonical == the live V1 WorldState


def _backend() -> Any:
    """Import the canonical WorldState class lazily (avoids an import cycle)."""
    from ..computer.world_state import WorldState  # type: ignore

    return WorldState


def create_world_state(**kwargs: Any) -> Any:
    """The single construction path for a world-state object.

    Production code calls this instead of ``WorldState(...)`` directly so that
    ``core.state`` really is the one place world state gets created, which is what
    the architecture gate asserts.
    """
    return _backend()(**kwargs)


def world_state_from_dict(data: Optional[dict[str, Any]]) -> Any:
    """Rebuild a world state from a checkpoint snapshot."""
    return _backend().from_dict(data)


def load_world_state(path: str, *, strict: bool = False) -> Any:
    """Load a persisted snapshot. ``strict=True`` raises on a missing/corrupt file."""
    return _backend().load(path, strict=strict)


class WorldStateFacade:
    """One canonical world-state API over core.computer.world_state.WorldState."""

    def __init__(self, state: Any = None, *, canonical: str = FACADE_V2,
                 state_path: Optional[str] = None):
        if state is None:
            if state_path and Path(state_path).exists():
                state = load_world_state(state_path)
            else:
                state = create_world_state()
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

    def load(self, path: str, *, strict: bool = False) -> "WorldStateFacade":
        self._state = type(self._state).load(path, strict=strict)
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
    return Path(path).expanduser().is_file()


def migrate_world_state(legacy_path: str, *, dry_run: bool = False,
                        marker_path: Optional[str] = None,
                        out_path: Optional[str] = None,
                        allow_corrupt: bool = False) -> dict[str, Any]:
    """Validate/load a world-state file into the canonical V1 schema.

    V1 ``WorldState`` is canonical, so this is a load + integrity check rather
    than a schema downcast. It writes a canonical snapshot and marks complete.

    An unreadable file is reported as a failure and left **untouched**. The
    previous version called the forgiving ``WorldState.load`` (which returns an
    empty state on any parse error) and then wrote that empty snapshot over the
    input by default — so running a migration against a corrupt file silently
    destroyed the only copy of the data and still reported ``success: True``.
    Pass ``allow_corrupt=True`` to opt into replacing it; the original bytes are
    backed up to ``<name>.corrupt.<timestamp>.bak`` first.
    """
    p = Path(legacy_path).expanduser()
    if not p.is_file():
        return {"success": False, "error": f"world-state {p} not found"}

    corrupt_reason: Optional[str] = None
    try:
        state = load_world_state(str(p), strict=True)
    except Exception as exc:
        if not allow_corrupt:
            return {
                "success": False,
                "corrupt": True,
                "error": f"world-state {p} is unreadable: {exc}",
                "hint": ("re-run with allow_corrupt=True to write a canonical "
                         "snapshot (the original is backed up, not overwritten)"),
            }
        corrupt_reason = str(exc)
        state = create_world_state()

    if dry_run:
        return {"success": True, "dry_run": True, "corrupt": corrupt_reason is not None,
                "keys": list(state.to_dict().keys()),
                "revision": state.revision}

    out = Path(out_path).expanduser() if out_path else p
    backup: Optional[str] = None
    if corrupt_reason is not None and out.resolve() == p.resolve():
        stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
        backup_path = p.with_name(f"{p.name}.corrupt.{stamp}.bak")
        backup_path.write_bytes(p.read_bytes())
        backup = str(backup_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    state.save(str(out))

    result: dict[str, Any] = {
        "success": True,
        "written": str(out),
        "revision": state.revision,
        "corrupt": corrupt_reason is not None,
    }
    if backup:
        result["backup"] = backup
    if marker_path:
        marker = Path(marker_path).expanduser()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({
            "migrated_at": datetime.now().astimezone().isoformat(),
            "source": str(p),
            "written": str(out),
            "revision": state.revision,
            "corrupt": corrupt_reason is not None,
        }, indent=2), encoding="utf-8")
        result["marker"] = str(marker)
    return result
