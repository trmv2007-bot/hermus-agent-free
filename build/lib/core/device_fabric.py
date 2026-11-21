"""Unified device/environment projection for HERMUS.

Aggregates canonical desktop, remote-control, Android and browser/world state.
This module is read-only; control remains owned by the existing computer/android
subsystems and their safety gates.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .world_awareness import world_awareness


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class DeviceFabric:
    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {"version": 1, "generated_at": _now()}

        try:
            from .emergency_stop import get_emergency_stop

            out["safety"] = {"emergency_stop": get_emergency_stop().state().to_dict()}
        except Exception:
            out["safety"] = {"emergency_stop": {"active": False, "state": "unknown"}}

        try:
            from .computer import ComputerActionController, ControlCenter, emergency_stop

            control = ControlCenter(ComputerActionController()).status()
            out["computer"] = {
                "available": True,
                "control": control,
                "halted": bool(getattr(emergency_stop, "halted", False)),
            }
        except Exception as exc:
            out["computer"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}

        try:
            from .computer import remote_control

            out["remote"] = remote_control.snapshot()
        except Exception as exc:
            out["remote"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}

        try:
            from .android.tool import get_android_tool

            capability = get_android_tool().capability()
            out["android"] = {"available": True, **capability}
        except Exception as exc:
            out["android"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}

        try:
            world = world_awareness.world.snapshot()
            facts = {f"{f['subject']}.{f['predicate']}": f.get("value") for f in world.get("facts", []) if isinstance(f, dict)}
            browser = facts.get("browser.state") or {"active": False}
            out["browser"] = {"available": True, "state": browser}
        except Exception as exc:
            out["browser"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}

        return out


device_fabric = DeviceFabric()

__all__ = ["DeviceFabric", "device_fabric"]
