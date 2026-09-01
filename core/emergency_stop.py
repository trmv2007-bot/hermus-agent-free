"""Global emergency-stop state for Red Line 1.

This is the small central brake that other subsystems can consult before doing
risky work. It is deliberately dependency-free and persists outside the repo in
Hermus workspace runtime state.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class EmergencyStopState:
    active: bool = False
    reason: str = ""
    set_by: str = "user"
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EmergencyStop:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def state(self) -> EmergencyStopState:
        if not self.path.exists():
            return EmergencyStopState(updated_at="")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return EmergencyStopState(
                active=bool(data.get("active", False)),
                reason=str(data.get("reason") or ""),
                set_by=str(data.get("set_by") or "user"),
                updated_at=str(data.get("updated_at") or ""),
            )
        except Exception:
            # Fail safe: unreadable stop-state means treat the brake as active.
            return EmergencyStopState(active=True, reason="emergency stop state unreadable", set_by="system", updated_at=_now())

    def active(self) -> bool:
        return self.state().active

    def activate(self, reason: str = "", *, set_by: str = "user") -> dict[str, Any]:
        state = EmergencyStopState(active=True, reason=reason or "manual emergency stop", set_by=set_by or "user", updated_at=_now())
        self._write(state)
        self._publish("emergency.stop.activated", state)
        return {"success": True, "state": state.to_dict()}

    def clear(self, reason: str = "", *, set_by: str = "user") -> dict[str, Any]:
        state = EmergencyStopState(active=False, reason=reason or "manual resume", set_by=set_by or "user", updated_at=_now())
        self._write(state)
        self._publish("emergency.stop.cleared", state)
        return {"success": True, "state": state.to_dict()}

    def _write(self, state: EmergencyStopState) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def _publish(self, command: str, state: EmergencyStopState) -> None:
        try:
            from .contracts import Actor, CommandSource, CommandStatus, EventEnvelope, EventType
            from .events import get_bus

            get_bus().publish(EventEnvelope(
                actor=Actor.SYSTEM.value,
                source=CommandSource.INTERNAL.value,
                type=EventType.STATE_CHANGED.value,
                command=command,
                target="emergency_stop",
                args_redacted=state.to_dict(),
                status=CommandStatus.SUCCEEDED.value,
            ))
        except Exception:
            pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_emergency_stop: Optional[EmergencyStop] = None


def get_emergency_stop(path: Optional[Path] = None) -> EmergencyStop:
    global _emergency_stop
    if path is not None:
        return EmergencyStop(path)
    if _emergency_stop is None:
        from .workspace import workspace

        _emergency_stop = EmergencyStop(workspace.dirs["root"] / "emergency_stop.json")
    return _emergency_stop


__all__ = ["EmergencyStop", "EmergencyStopState", "get_emergency_stop"]
