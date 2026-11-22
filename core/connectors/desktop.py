"""Read-only desktop perception: what is on this PC right now, and nothing more.

Every field is a :class:`Reading` — either ``{"ok": true, "value": ...}`` or
``{"ok": false, "reason": "..."}``. A read that a platform or permission does not
allow degrades to an explicit reason; it never raises, and it never invents a
value. Windows is read through ``ctypes`` (no third-party package required);
everywhere else the provider is a null provider that reports *why* it cannot
see, which keeps CI's headless Linux green.

What is read, and what is deliberately not:

* **focused** window title, owning process and pid — the title is redacted and
  never written to disk;
* **windows** — visible top-level windows, de-duplicated by process, capped;
* **power** — on AC or battery, percent, seconds left;
* **idle** — seconds since the last keyboard or mouse input;
* **displays** — each monitor's geometry, work area, DPI scale and primacy;
* **clipboard** — its *kind* and a one-way hash so "the clipboard changed" is
  observable without any content being retained. Contents are never stored, never
  logged and never sent anywhere.

Privacy is applied inside the snapshot, so no caller can leak past it: excluded
apps disappear from every field, private windows (banking, password managers,
incognito) keep their process but lose their title, and credential-shaped strings
are redacted before a title reaches a caller.
"""

from __future__ import annotations

import ctypes
import hashlib
import platform
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..awareness.redact import redact_text
from .base import Connector, ConnectorStatus

WINDOW_LIMIT = 40
TITLE_LIMIT = 120
CLIPBOARD_HASH_BYTES = 4 * 1024 * 1024

# Titles and processes that are private by nature. Matched case-insensitively as
# substrings; a hit blanks the title and keeps only the process family.
PRIVATE_HINTS = (
    "bank",
    "banking",
    "hsbc",
    "barclays",
    "chase",
    "hdfc",
    "icici",
    "axis bank",
    "wells fargo",
    "citibank",
    "paypal",
    "venmo",
    "wise",
    "stripe dashboard",
    "1password",
    "lastpass",
    "bitwarden",
    "keeper",
    "dashlane",
    "keepass",
    "enpass",
    "nordpass",
    "proton pass",
    "password",
    "authenticator",
    "incognito",
    "inprivate",
    "private browsing",
    "private window",
    "privacy mode",
)

# Meeting / call applications. Presence of one of these windows is the only
# "in a call" signal available without an audio-session API; it is a heuristic
# and is reported as one.
CALL_HINTS = (
    "zoom meeting",
    "zoom webinar",
    "microsoft teams",
    "google meet",
    "meet - ",
    "webex",
    "discord call",
    "slack huddle",
    "whereby",
    "jitsi",
    "bluejeans",
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Reading:
    """One field of a desktop snapshot: a value, or the reason there is none."""

    ok: bool
    value: Any = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            payload["value"] = self.value
        else:
            payload["reason"] = self.reason or "unavailable"
        return payload


@dataclass
class PrivacyPolicy:
    """What may never leave this machine, even as a process name."""

    excluded_apps: tuple[str, ...] = ()
    clipboard: bool = True
    private_hints: tuple[str, ...] = field(default=PRIVATE_HINTS)

    def is_excluded(self, process: str | None, title: str | None) -> bool:
        haystack = f"{process or ''} {title or ''}".lower()
        return any(token and token.lower() in haystack for token in self.excluded_apps)

    def is_private(self, process: str | None, title: str | None) -> bool:
        haystack = f"{process or ''} {title or ''}".lower()
        return any(hint in haystack for hint in self.private_hints)


def _process_name(pid: int | None) -> str | None:
    if not pid:
        return None
    try:
        import psutil  # type: ignore

        return psutil.Process(int(pid)).name()
    except Exception:
        return None


class DesktopProvider:
    """The primitive reads a desktop snapshot is built from.

    Each method returns ``(value, reason)``: a value with ``reason None`` on
    success, or ``(None, reason)`` when the read is not possible here. Methods
    may raise; :func:`read_desktop` turns that into an unavailable reading too.
    """

    name = "null"

    def focused_window(self) -> tuple[dict[str, Any] | None, str | None]:
        return None, "no desktop session on this provider"

    def visible_windows(self, limit: int = WINDOW_LIMIT) -> tuple[list[dict[str, Any]] | None, str | None]:
        return None, "no desktop session on this provider"

    def power(self) -> tuple[dict[str, Any] | None, str | None]:
        return None, "power state is not readable here"

    def idle_seconds(self) -> tuple[float | None, str | None]:
        return None, "idle time is not readable here"

    def displays(self) -> tuple[list[dict[str, Any]] | None, str | None]:
        return None, "monitor geometry is not readable here"

    def clipboard(self) -> tuple[dict[str, Any] | None, str | None]:
        return None, "clipboard is not readable here"


class NullDesktopProvider(DesktopProvider):
    """Reports one honest reason for every field (headless CI, unsupported OS)."""

    name = "null"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def focused_window(self):
        return None, self.reason

    def visible_windows(self, limit: int = WINDOW_LIMIT):
        return None, self.reason

    def power(self):
        return None, self.reason

    def idle_seconds(self):
        return None, self.reason

    def displays(self):
        return None, self.reason

    def clipboard(self):
        return None, self.reason


class WindowsDesktopProvider(DesktopProvider):
    """``ctypes`` reads against user32/dwmapi. No window is ever touched."""

    name = "windows"

    _GW_OWNER = 4
    _GWL_EXSTYLE = -20
    _WS_EX_TOOLWINDOW = 0x00000080
    _DWMWA_CLOAKED = 14
    _MONITOR_DEFAULTTONEAREST = 2
    _MONITORINFOF_PRIMARY = 1

    def __init__(self) -> None:
        from ctypes import wintypes

        self._wintypes = wintypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            self._dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        except Exception:
            self._dwmapi = None
        try:
            self._shcore = ctypes.WinDLL("shcore", use_last_error=True)
        except Exception:
            self._shcore = None

        # Handles are pointer-sized; ctypes defaults to a 32-bit int return,
        # which truncates them and turns a clipboard read into an access
        # violation. Declare the prototypes this module actually calls.
        handle, u32, k32 = ctypes.c_void_p, self._user32, self._kernel32
        u32.GetForegroundWindow.restype = handle
        u32.GetWindowTextLengthW.argtypes = [handle]
        u32.GetWindowTextW.argtypes = [handle, ctypes.c_wchar_p, ctypes.c_int]
        u32.GetClassNameW.argtypes = [handle, ctypes.c_wchar_p, ctypes.c_int]
        u32.GetWindowRect.argtypes = [handle, ctypes.POINTER(wintypes.RECT)]
        u32.GetWindowLongW.argtypes = [handle, ctypes.c_int]
        u32.GetWindow.argtypes = [handle, ctypes.c_uint]
        u32.GetWindow.restype = handle
        u32.IsWindowVisible.argtypes = [handle]
        u32.MonitorFromWindow.argtypes = [handle, ctypes.c_ulong]
        u32.MonitorFromWindow.restype = handle
        u32.GetMonitorInfoW.argtypes = [handle, ctypes.c_void_p]
        u32.OpenClipboard.argtypes = [handle]
        u32.EnumClipboardFormats.argtypes = [ctypes.c_uint]
        u32.GetClipboardData.argtypes = [ctypes.c_uint]
        u32.GetClipboardData.restype = handle
        k32.GlobalLock.argtypes = [handle]
        k32.GlobalLock.restype = handle
        k32.GlobalUnlock.argtypes = [handle]
        k32.GlobalSize.argtypes = [handle]
        k32.GlobalSize.restype = ctypes.c_size_t
        k32.GetTickCount64.restype = ctypes.c_ulonglong

    # -- primitives ---------------------------------------------------------

    def _title(self, hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(512)
        self._user32.GetWindowTextW(hwnd, buf, 512)
        return buf.value or ""

    def _class_name(self, hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(256)
        self._user32.GetClassNameW(hwnd, buf, 256)
        return buf.value or ""

    def _pid(self, hwnd: int) -> int:
        pid = self._wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def _rect(self, hwnd: int) -> dict[str, int] | None:
        rect = self._wintypes.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom}

    def _monitor_info(self, hwnd: int) -> dict[str, Any] | None:
        wintypes = self._wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        monitor = self._user32.MonitorFromWindow(wintypes.HWND(hwnd), self._MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return None
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not self._user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        return {
            "monitor": int(monitor),
            "rc": {
                "left": info.rcMonitor.left,
                "top": info.rcMonitor.top,
                "right": info.rcMonitor.right,
                "bottom": info.rcMonitor.bottom,
            },
            "primary": bool(info.dwFlags & self._MONITORINFOF_PRIMARY),
        }

    def _cloaked(self, hwnd: int) -> bool:
        if self._dwmapi is None:
            return False
        cloaked = ctypes.c_int(0)
        result = self._dwmapi.DwmGetWindowAttribute(
            self._wintypes.HWND(hwnd), self._DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        )
        return result == 0 and bool(cloaked.value)

    def _is_fullscreen(self, hwnd: int, rect: dict[str, int] | None) -> bool:
        if not rect:
            return False
        info = self._monitor_info(hwnd)
        if not info:
            return False
        monitor = info["rc"]
        slack = 2
        return (
            rect["left"] <= monitor["left"] + slack
            and rect["top"] <= monitor["top"] + slack
            and rect["right"] >= monitor["right"] - slack
            and rect["bottom"] >= monitor["bottom"] - slack
        )

    # -- public reads -------------------------------------------------------

    def focused_window(self):
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return None, "no foreground window (session locked or no interactive desktop)"
        klass = self._class_name(hwnd)
        if klass in {"Progman", "WorkerW"}:
            return None, "the desktop shell has focus, not an application"
        title = self._title(hwnd)
        pid = self._pid(hwnd)
        rect = self._rect(hwnd)
        return (
            {
                "title": title,
                "process": _process_name(pid),
                "pid": pid,
                "rect": rect,
                "fullscreen": self._is_fullscreen(hwnd, rect),
            },
            None,
        )

    def visible_windows(self, limit: int = WINDOW_LIMIT):
        wintypes = self._wintypes
        found: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _callback(hwnd, _lparam):  # noqa: ANN001 - ctypes signature
            try:
                if len(found) >= limit:
                    return False
                if not self._user32.IsWindowVisible(hwnd):
                    return True
                if self._user32.GetWindowTextLengthW(hwnd) == 0:
                    return True
                if self._user32.GetWindow(hwnd, self._GW_OWNER):
                    return True  # owned dialogs belong to their parent window
                exstyle = self._user32.GetWindowLongW(hwnd, self._GWL_EXSTYLE)
                if exstyle & self._WS_EX_TOOLWINDOW:
                    return True
                if self._cloaked(hwnd):
                    return True
                title = self._title(hwnd)
                pid = self._pid(hwnd)
                process = _process_name(pid)
                key = f"{(process or '').lower()}|{title.lower()}"
                if key in seen:
                    return True
                seen.add(key)
                found.append({"title": title, "process": process, "pid": pid})
            except Exception:
                return True
            return True

        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_callback)
        ok = self._user32.EnumWindows(callback, 0)
        if not ok and not found:
            return None, "EnumWindows refused the enumeration"
        return found, None

    def power(self):
        try:
            import psutil  # type: ignore

            battery = psutil.sensors_battery()
            if battery is not None:
                seconds_left = battery.secsleft
                unlimited = getattr(psutil, "POWER_TIME_UNLIMITED", -1)
                unknown = getattr(psutil, "POWER_TIME_UNKNOWN", -2)
                return (
                    {
                        "on_ac": bool(battery.power_plugged),
                        "percent": int(round(battery.percent)) if battery.percent is not None else None,
                        "seconds_left": None if seconds_left in (unlimited, unknown) else int(seconds_left),
                        "source": "psutil",
                    },
                    None,
                )
        except Exception:
            pass
        return self._power_via_api()

    def _power_via_api(self):
        wintypes = self._wintypes

        class SystemPowerStatus(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", wintypes.BYTE),
                ("BatteryFlag", wintypes.BYTE),
                ("BatteryLifePercent", wintypes.BYTE),
                ("SystemStatusFlag", wintypes.BYTE),
                ("BatteryLifeTime", wintypes.DWORD),
                ("BatteryFullLifeTime", wintypes.DWORD),
            ]

        status = SystemPowerStatus()
        if not self._kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None, "GetSystemPowerStatus failed"
        if status.ACLineStatus == 255:
            return None, "power state is unknown to Windows (no battery and no AC line reported)"
        return (
            {
                "on_ac": status.ACLineStatus == 1,
                "percent": None if status.BatteryLifePercent == 255 else int(status.BatteryLifePercent),
                "seconds_left": None if status.BatteryLifeTime in (0xFFFFFFFF, 0) else int(status.BatteryLifeTime),
                "source": "GetSystemPowerStatus",
            },
            None,
        )

    def idle_seconds(self):
        wintypes = self._wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not self._user32.GetLastInputInfo(ctypes.byref(info)):
            return None, "GetLastInputInfo failed"
        tick = self._kernel32.GetTickCount64() & 0xFFFFFFFF
        return round(((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0, 1), None

    def _monitor_dpi(self, monitor: int) -> int | None:
        if self._shcore is None:
            return None
        try:
            dpi_x = ctypes.c_uint(96)
            dpi_y = ctypes.c_uint(96)
            result = self._shcore.GetDpiForMonitor(
                ctypes.c_void_p(monitor), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
            )
            return int(dpi_x.value) if result == 0 else None
        except Exception:
            return None

    def displays(self):
        wintypes = self._wintypes
        monitors: list[dict[str, Any]] = []

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        def _callback(hmonitor, _hdc, _rect, _lparam):  # noqa: ANN001 - ctypes signature
            try:
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if not self._user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                    return True
                dpi = self._monitor_dpi(hmonitor)
                monitors.append(
                    {
                        "width": int(info.rcMonitor.right - info.rcMonitor.left),
                        "height": int(info.rcMonitor.bottom - info.rcMonitor.top),
                        "work_width": int(info.rcWork.right - info.rcWork.left),
                        "work_height": int(info.rcWork.bottom - info.rcWork.top),
                        "primary": bool(info.dwFlags & self._MONITORINFOF_PRIMARY),
                        "scale": round((dpi or 96) / 96, 2),
                    }
                )
            except Exception:
                return True
            return True

        callback = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
        )(_callback)
        self._user32.EnumDisplayMonitors(None, None, callback, 0)
        if not monitors:
            return None, "EnumDisplayMonitors found no monitor (session without a display?)"
        return monitors, None

    def clipboard(self):
        user32 = self._user32
        kernel32 = self._kernel32
        wintypes = self._wintypes
        cf_text, cf_bitmap, cf_dib, cf_unicode, cf_hdrop = 1, 2, 8, 13, 15

        if not user32.OpenClipboard(None):
            return None, "clipboard is locked by another process"
        try:
            formats: set[int] = set()
            fmt = 0
            while True:
                fmt = user32.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                formats.add(int(fmt))
            if not formats:
                return {"kind": "empty", "hash": None, "length": 0}, None
            if cf_unicode in formats or cf_text in formats:
                handle = user32.GetClipboardData(cf_unicode if cf_unicode in formats else cf_text)
                if not handle:
                    return {"kind": "text", "hash": None, "length": 0}, None
                size = int(kernel32.GlobalSize(handle) or 0)
                # Hashing is one-way and the buffer is released immediately; text
                # above the cap is characterised by size only, never read.
                digest = hashlib.sha256(f"text:{size}".encode()).hexdigest()[:16]
                length = 0
                if 0 < size <= CLIPBOARD_HASH_BYTES:
                    pointer = kernel32.GlobalLock(handle)
                    if pointer:
                        try:
                            raw = ctypes.wstring_at(pointer, size // 2).split(chr(0), 1)[0]
                            digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]
                            length = len(raw)
                            del raw
                        finally:
                            kernel32.GlobalUnlock(handle)
                return {"kind": "text", "hash": digest, "length": length}, None
            if cf_dib in formats or cf_bitmap in formats:
                handle = user32.GetClipboardData(cf_dib if cf_dib in formats else cf_bitmap)
                size = int(kernel32.GlobalSize(handle) or 0) if handle else 0
                return {"kind": "image", "hash": hashlib.sha256(f"image:{size}".encode()).hexdigest()[:16], "length": size}, None
            if cf_hdrop in formats:
                return {"kind": "files", "hash": None, "length": len(formats)}, None
            return {"kind": "other", "hash": None, "length": len(formats)}, None
        finally:
            user32.CloseClipboard()


def default_desktop_provider() -> DesktopProvider:
    """The best provider this machine allows, or a null provider saying why not."""
    system = platform.system()
    if system != "Windows":
        return NullDesktopProvider(f"desktop awareness reads Windows today; this machine is {system or 'unknown'}")
    try:
        return WindowsDesktopProvider()
    except Exception as exc:  # noqa: BLE001 - a denied API must not raise out of perception
        return NullDesktopProvider(f"Windows desktop APIs are unavailable: {type(exc).__name__}: {exc}")


def _read(call, *args) -> tuple[Reading, float]:
    started = time.perf_counter()
    try:
        value, reason = call(*args)
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.perf_counter() - started) * 1000
        return Reading(False, reason=f"{type(exc).__name__}: {exc}"[:200]), elapsed
    elapsed = (time.perf_counter() - started) * 1000
    if value is None:
        return Reading(False, reason=(reason or "unavailable")[:200]), elapsed
    return Reading(True, value), elapsed


def _apply_privacy(snapshot: dict[str, Any], policy: PrivacyPolicy) -> None:
    focused = snapshot.get("focused") or {}
    if focused.get("ok"):
        value = focused.get("value") or {}
        process, title = value.get("process"), value.get("title")
        if policy.is_excluded(process, title):
            snapshot["focused"] = Reading(True, {"excluded": True, "fullscreen": False}).to_dict()
        elif policy.is_private(process, title):
            snapshot["focused"] = Reading(
                True, {**value, "title": "[private]", "private": True}
            ).to_dict()
        else:
            snapshot["focused"] = Reading(True, {**value, "title": redact_text(title, TITLE_LIMIT)}).to_dict()

    windows = snapshot.get("windows") or {}
    if windows.get("ok"):
        kept = []
        for window in windows.get("value") or []:
            process, title = window.get("process"), window.get("title")
            if policy.is_excluded(process, title):
                continue
            private = policy.is_private(process, title)
            kept.append(
                {
                    "title": "[private]" if private else redact_text(title, TITLE_LIMIT),
                    "process": process,
                    "private": private,
                }
            )
        snapshot["windows"] = Reading(True, kept).to_dict()
        snapshot["window_count"] = len(kept)

    if not policy.clipboard:
        snapshot["clipboard"] = Reading(False, reason="clipboard reading is turned off in awareness settings").to_dict()

    fullscreen = snapshot.get("focused", {}).get("value", {}) if snapshot.get("focused", {}).get("ok") else {}
    snapshot["fullscreen"] = (
        Reading(True, bool(fullscreen.get("fullscreen"))).to_dict()
        if snapshot.get("focused", {}).get("ok")
        else Reading(False, reason=(snapshot.get("focused") or {}).get("reason", "unavailable")).to_dict()
    )


def read_desktop(
    provider: DesktopProvider | None = None,
    *,
    policy: PrivacyPolicy | None = None,
    window_limit: int = WINDOW_LIMIT,
) -> dict[str, Any]:
    """One read-only snapshot; every field is a :class:`Reading` dict.

    Nothing is cached and nothing is written: the snapshot exists in memory for
    as long as its caller holds it.
    """
    provider = provider or default_desktop_provider()
    policy = policy or PrivacyPolicy()
    snapshot: dict[str, Any] = {
        "read_at": _now_iso(),
        "platform": platform.system(),
        "provider": provider.name,
        "timings_ms": {},
    }
    reads = {
        "focused": (provider.focused_window, ()),
        "windows": (provider.visible_windows, (window_limit,)),
        "power": (provider.power, ()),
        "idle": (provider.idle_seconds, ()),
        "displays": (provider.displays, ()),
        "clipboard": (provider.clipboard, ()),
    }
    for name, (call, args) in reads.items():
        reading, elapsed = _read(call, *args)
        snapshot[name] = reading.to_dict()
        snapshot["timings_ms"][name] = round(elapsed, 2)

    if snapshot["idle"].get("ok"):
        snapshot["idle"] = Reading(True, {"seconds": snapshot["idle"]["value"]}).to_dict()

    _apply_privacy(snapshot, policy)
    snapshot["fullscreen"] = snapshot.get("fullscreen") or Reading(False, reason="unavailable").to_dict()
    return snapshot


class DesktopConnector(Connector):
    """Registry adapter: aggregate facts only, so no title is ever persisted.

    ``observe()`` reports the process family, counts and machine state into the
    WorldModel. Titles stay in memory inside one snapshot and are never part of a
    fact, which keeps the durable world store free of what the person is reading.
    """

    name = "desktop"
    capabilities = ("desktop.read", "desktop.observe")

    def __init__(self, context=None, *, provider: DesktopProvider | None = None, policy: PrivacyPolicy | None = None):
        super().__init__(context)
        self.provider = provider
        self.policy = policy or PrivacyPolicy()

    def health(self) -> ConnectorStatus:
        snapshot = read_desktop(self.provider, policy=self.policy)
        readable = [key for key in ("focused", "windows", "power", "idle", "displays", "clipboard") if snapshot[key]["ok"]]
        if not self.enabled:
            return ConnectorStatus(self.name, "disabled", capabilities=list(self.capabilities))
        if not readable:
            reason = snapshot["focused"].get("reason") or snapshot["windows"].get("reason") or "no field is readable"
            return ConnectorStatus(self.name, "degraded", reason, capabilities=list(self.capabilities))
        return ConnectorStatus(self.name, "ready", f"readable: {', '.join(readable)}", capabilities=list(self.capabilities))

    def observe(self) -> list[dict[str, Any]]:
        snapshot = read_desktop(self.provider, policy=self.policy)
        facts: list[dict[str, Any]] = []
        focused = snapshot.get("focused") or {}
        process = None
        if focused.get("ok"):
            process = None if focused["value"].get("excluded") else focused["value"].get("process")
        power = snapshot["power"]
        idle = snapshot["idle"]
        windows = snapshot["windows"]
        displays = snapshot["displays"]
        facts.append({"subject": "desktop", "predicate": "focused_process", "value": process})
        facts.append({"subject": "desktop", "predicate": "window_count", "value": len(windows.get("value") or []) if windows.get("ok") else None})
        facts.append({"subject": "desktop", "predicate": "fullscreen", "value": snapshot["fullscreen"].get("value") if snapshot["fullscreen"].get("ok") else None})
        facts.append({"subject": "desktop", "predicate": "power", "value": power.get("value") if power.get("ok") else {"unavailable": power.get("reason")}})
        facts.append({"subject": "desktop", "predicate": "idle_seconds", "value": (idle.get("value") or {}).get("seconds") if idle.get("ok") else None})
        facts.append({"subject": "desktop", "predicate": "display_count", "value": len(displays.get("value") or []) if displays.get("ok") else None})
        for fact in facts:
            fact["permission_scope"] = "desktop.read"
        return facts

    def actions(self) -> dict[str, Any]:
        return {"snapshot": lambda: read_desktop(self.provider, policy=self.policy)}


__all__ = [
    "CALL_HINTS",
    "PRIVATE_HINTS",
    "DesktopConnector",
    "DesktopProvider",
    "NullDesktopProvider",
    "PrivacyPolicy",
    "Reading",
    "WindowsDesktopProvider",
    "default_desktop_provider",
    "read_desktop",
]
