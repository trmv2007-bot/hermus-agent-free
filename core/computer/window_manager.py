"""Window / application control backends.

Opening, focusing and closing applications is delegated to platform tooling
(``pygetwindow`` when available, otherwise ``open``/``xdg-open``/``start``),
with a dry-run fallback for headless operation.
"""
from __future__ import annotations

import platform
import subprocess
from datetime import datetime
from typing import Any, Optional


def _now() -> str:
    return datetime.now().astimezone().isoformat()


class WindowBackend:
    """Abstract window/application backend."""

    name = "window"

    def available(self) -> dict[str, Any]:
        raise NotImplementedError

    def open_application(self, name: str) -> dict[str, Any]:
        raise NotImplementedError

    def close_application(self, name: str) -> dict[str, Any]:
        raise NotImplementedError

    def focus_window(self, name: str) -> dict[str, Any]:
        raise NotImplementedError

    def list_windows(self) -> dict[str, Any]:
        raise NotImplementedError


class PyGetWindowBackend(WindowBackend):
    """Window control via the optional ``pygetwindow`` package."""

    name = "pygetwindow"

    def __init__(self) -> None:
        self._gw = None
        self._error: Optional[str] = None
        try:
            import pygetwindow  # type: ignore

            self._gw = pygetwindow
        except Exception as exc:  # noqa: BLE001
            self._error = f"pygetwindow unavailable: {exc}"

    def available(self) -> dict[str, Any]:
        return {"available": self._gw is not None, "error": self._error}

    def _find(self, name: str):
        needle = str(name).lower()
        for window in self._gw.getAllWindows():
            title = (window.title or "").lower()
            if needle in title:
                return window
        return None

    def open_application(self, name: str) -> dict[str, Any]:
        # pygetwindow can't launch apps; fall back to the platform opener.
        return _platform_open(name)

    def close_application(self, name: str) -> dict[str, Any]:
        window = self._find(name)
        if window is None:
            return {"ok": False, "error": f"no window matching '{name}'", "dry_run": False}
        try:
            window.close()
            return {"ok": True, "name": name, "dry_run": False}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "dry_run": False}

    def focus_window(self, name: str) -> dict[str, Any]:
        window = self._find(name)
        if window is None:
            return {"ok": False, "error": f"no window matching '{name}'", "dry_run": False}
        try:
            window.activate()
            return {"ok": True, "name": name, "dry_run": False}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "dry_run": False}

    def list_windows(self) -> dict[str, Any]:
        titles = [w.title for w in self._gw.getAllWindows() if w.title]
        return {"ok": True, "windows": titles, "dry_run": False}


def _platform_open(name: str) -> dict[str, Any]:
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", "-a", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Windows":
            subprocess.Popen(["start", "", name], shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["gtk-launch", name] if _has("gtk-launch") else ["xdg-open", name],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "name": name, "dry_run": False, "method": system.lower()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "name": name, "dry_run": False}


def _has(binary: str) -> bool:
    try:
        subprocess.run(["which", binary], capture_output=True, check=True)
        return True
    except Exception:
        return False


class DryRunWindowBackend(WindowBackend):
    """Headless/test backend: records window actions without performing them."""

    name = "dry_run"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def available(self) -> dict[str, Any]:
        return {"available": True, "error": None, "note": "dry-run backend; no real window control"}

    def _record(self, action: str, **kwargs: Any) -> dict[str, Any]:
        record = {"action": action, "ts": _now(), "dry_run": True, **kwargs}
        self.calls.append(record)
        return {**record, "ok": True}

    def open_application(self, name: str) -> dict[str, Any]:
        return self._record("open_application", name=str(name))

    def close_application(self, name: str) -> dict[str, Any]:
        return self._record("close_application", name=str(name))

    def focus_window(self, name: str) -> dict[str, Any]:
        return self._record("focus_window", name=str(name))

    def list_windows(self) -> dict[str, Any]:
        return self._record("list_windows", windows=[])


def default_window_manager() -> WindowBackend:
    real = PyGetWindowBackend()
    if real.available()["available"]:
        return real
    return DryRunWindowBackend()
