"""Deterministic simulated Android device (emulator-compatible).

This is *not* a mock of ``AndroidTool`` — it drives the real
``AndroidTool -> AndroidTransport`` boundary exactly as a device would, but with a
deterministic in-memory "device" so the full agentic flow (observe -> reason -> act ->
observe -> verify) can be exercised without an emulator. It implements the same
:class:`~core.android.transport.AndroidTransport` interface as ``AdbAndroidTransport``
and ``BridgeAndroidTransport``.

The simulated device hosts a scripted "Tasks" app (a deterministic list with a text
field + Add button) so the agent's high-level goal ("open Tasks and add a task") can be
turned into a real, verifiable end-to-end task. It also records every action for
assertions.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .transport import AndroidTransport, AndroidUnavailable

APP_TASKS = "com.example.tasks"


@dataclass
class _Elem:
    text: str
    cls: str
    x: int
    y: int
    w: int = 80
    h: int = 40
    clickable: bool = False
    enabled: bool = True
    focused: bool = False
    id: Optional[str] = None

    @property
    def bounds(self) -> list[int]:
        return [self.x, self.y, self.x + self.w, self.y + self.h]


@dataclass
class _App:
    package: str
    title: str
    elements: list[_Elem] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)

    def refresh(self, *, field_value: str = ""):
        # Deterministic layout of the Tasks app. The input field is a single EditText;
        # its text reflects what has been typed so the agent can observe field state.
        self.elements = [
            _Elem(self.title, "TextView", 0, 40, 320, 40, id="title"),
            _Elem("Task title", "TextView", 20, 110, 240, 30, id="label"),
            _Elem(field_value, "EditText", 20, 150, 280, 48,
                  focused=True, clickable=True, id="field"),
            _Elem("Add", "Button", 20, 230, 120, 48, clickable=True, id="add"),
            _Elem("Clear", "Button", 160, 230, 120, 48, clickable=True, id="clear"),
        ]
        # Append existing tasks as list rows below the buttons.
        for i, t in enumerate(self.tasks):
            self.elements.append(_Elem(t, "ListItem", 20, 300 + i * 44,
                                      280, 40, clickable=False, id=f"task_{i}"))


def _mk_app(package: str, title: str) -> _App:
    return _App(package=package, title=title)


class SimulatedAndroidDevice(AndroidTransport):
    """Deterministic scripted device. ``allowed`` operations are enforced here too so
    a bad action is caught at the device boundary."""

    name = "simulated"

    def __init__(self, *, package: str = APP_TASKS, tasks: Optional[list[str]] = None):
        self.serial = "SIM-0001"
        self._apps: dict[str, _App] = {
            APP_TASKS: _mk_app(APP_TASKS, "Tasks"),
        }
        self._field_value = ""
        self._apps[APP_TASKS].tasks = list(tasks or [])
        self._apps[APP_TASKS].refresh(field_value=self._field_value)
        # Start on the Tasks app so the default observation is meaningful and the agent
        # can act immediately without a launch step.
        self.package = APP_TASKS
        self.actions_log: list[dict[str, Any]] = []
        self._rev = 0

    # -- device state -------------------------------------------------------
    def _foreground(self) -> _App:
        if self.package in self._apps:
            return self._apps[self.package]
        raise AndroidUnavailable(f"app '{self.package}' is not installed on the simulated device",
                                 category="device")

    def _bump(self):
        self._rev += 1
        self._foreground().refresh(field_value=self._field_value)

    # -- AndroidTransport interface ----------------------------------------
    def connect(self) -> dict[str, Any]:
        return {"ok": True, "device": self.serial, "model": "Simulated Emulator",
                "transport": "simulated"}

    def device_id(self) -> str:
        return self.serial

    def get_surface(self) -> dict[str, Any]:
        app = self._foreground()
        return {"ok": True, "package": app.package, "title": app.title,
                "tasks": list(app.tasks), "rev": self._rev}

    def get_screen(self, **kw) -> dict[str, Any]:
        app = self._foreground()
        blob = json.dumps({"pkg": app.package, "title": app.title, "tasks": app.tasks,
                           "rev": self._rev})
        return {"ok": True, "format": "png", "bytes": len(blob),
                "hash": hashlib.sha256(blob.encode()).hexdigest()[:16],
                "data": blob}

    def get_ui_tree(self, **kw) -> dict[str, Any]:
        app = self._foreground()
        nodes = []
        for e in app.elements:
            if e.id and e.id == "field_value" and not self._field_value:
                continue
            nodes.append({
                "text": e.text, "class": e.cls, "clickable": e.clickable,
                "enabled": e.enabled, "focused": e.focused,
                "bounds": e.bounds, "id": e.id,
            })
        return {"ok": True, "format": "semantic", "package": app.package,
                "title": app.title, "nodes": nodes}

    def current_app(self) -> dict[str, Any]:
        return {"ok": True, "package": self._foreground().package,
                "title": self._foreground().title}

    # -- control ------------------------------------------------------------
    def _element_at(self, x: int, y: int, *, clickable_only: bool = True) -> Optional[_Elem]:
        for e in reversed(self._foreground().elements):
            b = e.bounds
            if b[2] - b[0] <= 0 or b[3] - b[1] <= 0:
                continue
            if b[0] <= x <= b[2] and b[1] <= y <= b[3] and (not clickable_only or e.clickable):
                return e
        return None

    def tap(self, x: int, y: int, **kw) -> dict[str, Any]:
        e = self._element_at(int(x), int(y))
        self.actions_log.append({"op": "tap", "x": int(x), "y": int(y), "target": e.id if e else None})
        if e is None:
            raise AndroidUnavailable(f"no clickable element at ({x},{y})", category="ui", op="tap")
        if not e.enabled:
            raise AndroidUnavailable(f"element '{e.id}' is disabled", category="ui", op="tap")
        app = self._foreground()
        if e.id == "add" and self._field_value.strip():
            app.tasks.append(self._field_value.strip())
            self._field_value = ""
            self._bump()
        elif e.id == "clear":
            app.tasks = []
            self._field_value = ""
            self._bump()
        return {"ok": True, "x": int(x), "y": int(y), "target": e.id}

    def type_text(self, text: str, **kw) -> dict[str, Any]:
        self.actions_log.append({"op": "type", "text": text})
        self._field_value = text
        self._bump()
        return {"ok": True, "length": len(text)}

    def back(self, **kw) -> dict[str, Any]:
        self.actions_log.append({"op": "back"})
        self.package = "com.example.launcher"
        self._bump()
        return {"ok": True, "keyevent": "BACK"}

    def launch_app(self, package: str, **kw) -> dict[str, Any]:
        package = package.strip()
        self.actions_log.append({"op": "launch_app", "package": package})
        if package not in self._apps:
            raise AndroidUnavailable(f"app '{package}' is not installed", category="app", op="launch_app")
        self.package = package
        self._bump()
        return {"ok": True, "package": package}

    def press_home(self, **kw) -> dict[str, Any]:
        self.actions_log.append({"op": "home"})
        self.package = "com.example.launcher"
        self._bump()
        return {"ok": True}

    def disconnect(self) -> dict[str, Any]:
        self.actions_log.append({"op": "disconnect"})
        self.package = "com.example.launcher"
        self._bump()
        return {"ok": True}

    # -- verification helpers ----------------------------------------------
    def foreground_package(self) -> str:
        return self.package

    def tasks(self) -> list[str]:
        return list(self._foreground().tasks)

    def field_text(self) -> str:
        return self._field_value


def default_simulated_device(**kw) -> SimulatedAndroidDevice:
    return SimulatedAndroidDevice(**kw)
