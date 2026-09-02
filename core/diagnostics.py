"""Hermus install / health diagnostics (Phase D).

``hermus doctor`` runs this to verify the runtime is healthy: Python version,
optional dependencies, FFmpeg (for screen recording), the computer-control
backends, data-directory writability and whether a gateway token is set.  Each
check is independent and never raises, so a single broken piece is reported
without aborting the whole report.
"""
from __future__ import annotations

import importlib.util
import os
import platform
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PACKAGES = [
    ("pydantic", "core configuration"),
    ("requests", "LLM provider + HTTP calls"),
    ("PIL", "image handling / screen capture"),
]

RECOMMENDED_PACKAGES = [
    ("fastapi", "gateway / dashboard"),
    ("uvicorn", "gateway server"),
    ("psutil", "resource telemetry"),
    ("pyautogui", "real desktop mouse/keyboard control"),
    ("pygetwindow", "window management"),
    ("websockets", "live dashboard WebSocket"),
]


def _importable(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _check(name: str, ok: bool, detail: str, hint: str = "", level: str = "required") -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail, "hint": hint, "level": level}


def _ffmpeg_available() -> bool:
    try:
        from core.computer import VideoWriter

        return bool(VideoWriter.available().get("available"))
    except Exception:  # noqa: BLE001
        return False


def run_diagnostics() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    # Python version
    py_ok = sys.version_info >= (3, 9)
    checks.append(_check(
        "python",
        py_ok,
        f"Python {platform.python_version()} ({platform.system()})",
        "Python 3.9+ is required.",
    ))

    # Required packages
    for pkg, role in REQUIRED_PACKAGES:
        ok = _importable(pkg)
        checks.append(_check(pkg, ok, f"installed={ok}", f"pip install {pkg}  ({role})"))

    # Recommended packages
    for pkg, role in RECOMMENDED_PACKAGES:
        ok = _importable(pkg)
        checks.append(_check(pkg, ok, f"installed={ok}", f"pip install {pkg}  ({role})", level="recommended"))

    # FFmpeg
    ff = _ffmpeg_available()
    checks.append(_check("ffmpeg", ff, "video encoding available" if ff else "not detected",
                         "pip install imageio-ffmpeg (or provide ffmpeg) for MP4/WebM recording", level="recommended"))

    # Computer control backends
    pyautogui = _importable("pyautogui")
    pygetwindow = _importable("pygetwindow")
    control_ok = pyautogui and pygetwindow
    checks.append(_check(
        "desktop_control",
        control_ok,
        f"pyautogui={pyautogui}, pygetwindow={pygetwindow}",
        "pip install pyautogui pygetwindow for real pointer/keyboard control (dry-run otherwise).",
        level="recommended",
    ))

    # Data directory writability
    data_root = REPO_ROOT / "data"
    try:
        data_root.mkdir(parents=True, exist_ok=True)
        probe = data_root / ".doctor"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks.append(_check("data_dir", True, str(data_root), "ensure data/ is writable"))
    except OSError as exc:
        checks.append(_check("data_dir", False, str(data_root), f"data/ not writable: {exc}"))

    # Gateway token (recommended when exposing the control room / remote control)
    token = os.getenv("HERMUS_GATEWAY_TOKEN") or os.getenv("HERMUS_TOKEN")
    checks.append(_check(
        "gateway_auth",
        bool(token),
        "gateway token configured" if token else "no gateway token (open local access)",
        "Set HERMUS_GATEWAY_TOKEN before exposing /control (or /remote, /computer APIs) beyond localhost.",
        level="recommended",
    ))

    # Computer agent import (core system)
    try:
        import core.computer  # noqa: F401

        checks.append(_check("computer_agent", True, "core.computer imports OK", ""))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("computer_agent", False, f"import error: {exc}",
                             "Run: pip install -r requirements.txt"))

    checks.extend(_web_acquisition_checks())

    required_ok = all(c["ok"] for c in checks if c["level"] == "required")
    recommended_ok = all(c["ok"] for c in checks if c["level"] == "recommended")
    return {
        "generated": _now(),
        "platform": platform.platform(),
        "hostname": platform.node(),
        "overall": {
            "ok": required_ok,
            "required": required_ok,
            "recommended": recommended_ok,
            "passed": sum(1 for c in checks if c["ok"]),
            "total": len(checks),
        },
        "checks": checks,
    }


def _now() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


def _web_acquisition_checks() -> list[dict[str, Any]]:
    """Honest web-subsystem capability checks (spec §21).

    Never reports a capability as working merely because an import succeeds:
    each status comes from core.web.capabilities, which distinguishes
    available / unavailable / not_installed / not_verified. All checks are
    "recommended" level — a missing Scrapling degrades capabilities but never
    fails the install report.
    """
    from .web_status import web_status_checks

    return web_status_checks()


def print_diagnostics(report: dict[str, Any]) -> None:
    print("=" * 60)
    print("Hermus Doctor — health report")
    print("=" * 60)
    for check in report["checks"]:
        mark = "✅" if check["ok"] else ("⚠️" if check["level"] == "recommended" else "❌")
        print(f"  {mark} {check['name']:<18} {check['detail']}")
        if not check["ok"] and check["hint"]:
            print(f"        → {check['hint']}")
    o = report["overall"]
    print("=" * 60)
    print(f"  Required: {'OK' if o['required'] else 'FAIL'}   "
          f"Recommended: {'OK' if o['recommended'] else 'IMPROVE'}   "
          f"({o['passed']}/{o['total']} checks pass)")
    print("=" * 60)
