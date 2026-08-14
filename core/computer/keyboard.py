"""Keyboard input backends for the computer action engine.

Mirrors :mod:`core.computer.mouse`: real key injection is optional
(``pyautogui``) and headless-safe, with a :class:`DryRunKeyboard` that emits
identical structured action records for offline testing and auditing.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now().astimezone().isoformat()


class KeyboardBackend:
    """Abstract keyboard backend."""

    name = "keyboard"

    def available(self) -> Dict[str, Any]:
        raise NotImplementedError

    def type_text(self, text: str, interval: float = 0.0) -> Dict[str, Any]:
        raise NotImplementedError

    def press(self, key: str) -> Dict[str, Any]:
        raise NotImplementedError

    def hotkey(self, *keys: str) -> Dict[str, Any]:
        raise NotImplementedError


class PyAutoGUIKeyboard(KeyboardBackend):
    """Real keyboard control through the optional ``pyautogui`` dependency."""

    name = "pyautogui"

    def __init__(self) -> None:
        self._gui = None
        self._error: Optional[str] = None
        try:
            import pyautogui  # type: ignore

            pyautogui.FAILSAFE = True
            self._gui = pyautogui
        except Exception as exc:  # noqa: BLE001
            self._error = f"pyautogui unavailable: {exc}"

    def available(self) -> Dict[str, Any]:
        return {"available": self._gui is not None, "error": self._error}

    def _require(self) -> None:
        if self._gui is None:
            raise RuntimeError(self._error or "pyautogui unavailable")

    def type_text(self, text: str, interval: float = 0.0) -> Dict[str, Any]:
        self._require()
        self._gui.write(str(text), interval=float(interval))
        return {"ok": True, "text": str(text), "interval": float(interval)}

    def press(self, key: str) -> Dict[str, Any]:
        self._require()
        self._gui.press(str(key))
        return {"ok": True, "key": str(key)}

    def hotkey(self, *keys: str) -> Dict[str, Any]:
        self._require()
        self._gui.hotkey(*keys)
        return {"ok": True, "keys": list(keys)}


class DryRunKeyboard(KeyboardBackend):
    """Headless/test keyboard: records keystrokes without injecting them."""

    name = "dry_run"

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def available(self) -> Dict[str, Any]:
        return {"available": True, "error": None, "note": "dry-run backend; no real key injection"}

    def _record(self, action: str, **kwargs: Any) -> Dict[str, Any]:
        record = {"action": action, "ts": _now(), "dry_run": True, **kwargs}
        self.calls.append(record)
        return record

    def type_text(self, text: str, interval: float = 0.0) -> Dict[str, Any]:
        return self._record("type_text", text=str(text), interval=float(interval))

    def press(self, key: str) -> Dict[str, Any]:
        return self._record("press", key=str(key))

    def hotkey(self, *keys: str) -> Dict[str, Any]:
        return self._record("hotkey", keys=list(keys))


def default_keyboard() -> KeyboardBackend:
    real = PyAutoGUIKeyboard()
    if real.available()["available"]:
        return real
    return DryRunKeyboard()
