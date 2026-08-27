"""Mouse input backends for the computer action engine.

Real pointer control is optional and headless-safe: if ``pyautogui`` is not
installed (or there is no display), a :class:`DryRunMouse` still produces the
same structured action records so the whole plan → act → verify loop remains
testable and auditable offline.  Agents should never assume a backend is
"real"; every action record carries a ``backend`` and ``dry_run`` flag.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


def _now() -> str:
    return datetime.now().astimezone().isoformat()


class MouseBackend:
    """Abstract mouse backend."""

    name = "mouse"

    def available(self) -> dict[str, Any]:
        raise NotImplementedError

    def move(self, x: float, y: float) -> dict[str, Any]:
        raise NotImplementedError

    def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        raise NotImplementedError

    def scroll(self, amount: float, x: Optional[float] = None, y: Optional[float] = None) -> dict[str, Any]:
        raise NotImplementedError

    def position(self) -> dict[str, Any]:
        raise NotImplementedError


class PyAutoGUIMouse(MouseBackend):
    """Real mouse control through the optional ``pyautogui`` dependency."""

    name = "pyautogui"

    def __init__(self) -> None:
        self._gui = None
        self._error: Optional[str] = None
        try:
            import pyautogui  # type: ignore

            pyautogui.FAILSAFE = True  # slam pointer to a corner = emergency stop
            self._gui = pyautogui
        except Exception as exc:  # noqa: BLE001 - any import/display failure
            self._error = f"pyautogui unavailable: {exc}"

    def available(self) -> dict[str, Any]:
        return {"available": self._gui is not None, "error": self._error}

    def _require(self) -> None:
        if self._gui is None:
            raise RuntimeError(self._error or "pyautogui unavailable")

    def move(self, x: float, y: float) -> dict[str, Any]:
        self._require()
        self._gui.moveTo(float(x), float(y))
        return {"ok": True, "x": float(x), "y": float(y)}

    def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        self._require()
        self._gui.click(float(x), float(y), button=button, clicks=int(clicks))
        return {"ok": True, "x": float(x), "y": float(y), "button": button, "clicks": int(clicks)}

    def scroll(self, amount: float, x: Optional[float] = None, y: Optional[float] = None) -> dict[str, Any]:
        self._require()
        if x is not None and y is not None:
            self._gui.scroll(int(amount), float(x), float(y))
        else:
            self._gui.scroll(int(amount))
        return {"ok": True, "amount": float(amount), "x": x, "y": y}

    def position(self) -> dict[str, Any]:
        self._require()
        pos = self._gui.position()
        return {"ok": True, "x": float(pos[0]), "y": float(pos[1])}


class DryRunMouse(MouseBackend):
    """Headless/test mouse: records calls without touching the pointer."""

    name = "dry_run"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def available(self) -> dict[str, Any]:
        return {"available": True, "error": None, "note": "dry-run backend; no real pointer control"}

    def _record(self, action: str, **kwargs: Any) -> dict[str, Any]:
        record = {"action": action, "ts": _now(), "dry_run": True, **kwargs}
        self.calls.append(record)
        return record

    def move(self, x: float, y: float) -> dict[str, Any]:
        return self._record("move", x=float(x), y=float(y))

    def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        return self._record("click", x=float(x), y=float(y), button=button, clicks=int(clicks))

    def scroll(self, amount: float, x: Optional[float] = None, y: Optional[float] = None) -> dict[str, Any]:
        return self._record("scroll", amount=float(amount), x=x, y=y)

    def position(self) -> dict[str, Any]:
        return self._record("position", x=0.0, y=0.0)


def default_mouse() -> MouseBackend:
    """Return a real backend when available, otherwise a safe dry-run one."""
    real = PyAutoGUIMouse()
    if real.available()["available"]:
        return real
    return DryRunMouse()
