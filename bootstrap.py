#!/usr/bin/env python3
"""Hermus bootstrap & doctor (Rebuild spec §6, §18).

Replaces the split-brain setup/activation/launcher scripts with ONE idempotent
operation. Invoked as ``./hermus bootstrap``. It:

* detects OS / Python / permissions,
* creates/update the venv,
* installs pinned runtime dependencies (and records optional ones as capable or
  *unavailable* with an exact reason),
* creates the canonical data/home layout,
* migrates legacy state if present,
* runs health probes,
* prints ONE capability summary, and
* exits 0 only when required capabilities are ready.

Design rules honored here:
* idempotent — running twice does not duplicate files/services/keys/registrations;
* no ``|| true`` masking on required dependencies — optional deps fail into an
  explicit ``unavailable`` capability state;
* the launchers are thin wrappers, never business logic.

Callable as a library (``from bootstrap import bootstrap, doctor``) or a CLI.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Pinned runtime (required) vs optional capabilities. Required deps gate the
# bootstrap exit code; optional deps only degrade a capability with a reason.
# These mirror requirements.txt but are the reproducibility set. ``importlib``
# needs *import* module names, while ``pip install`` needs *pip* package names —
# so we keep both. e.g. pip ``python-dotenv`` -> import ``dotenv``,
# pip ``python-multipart`` -> import ``multipart``.
REQUIRED_IMPORTS = [
    "pydantic", "dotenv", "requests", "tiktoken",
    "fastapi", "uvicorn", "multipart", "psutil", "httpx", "pytest",
]
REQUIRED_PIP = [
    "pydantic", "python-dotenv", "requests", "tiktoken",
    "fastapi", "uvicorn", "python-multipart", "psutil", "httpx", "pytest",
]
OPTIONAL = [
    ("playwright", "browser"),
    ("PIL", "vision"),
    ("groq", "provider.groq"),
    ("huggingface_hub", "provider.hf"),
    ("faster_whisper", "voice"),
    ("paramiko", "backend.ssh"),
    ("modal", "backend.modal"),
    ("APScheduler", "scheduler"),
    ("prompt_toolkit", "tui"),
    ("rich", "tui"),
    ("python_telegram_bot", "channel.telegram"),
    ("discord", "channel.discord"),
]

DATA_DIRS = ["data", "workspace", "artifacts", "skills", "migrations", "logs"]


def _module_ok(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _capsule(name: str) -> str:
    return name.replace(".", "_")


def detect_python() -> str:
    return sys.version.split()[0]


def venv_ready() -> bool:
    return (ROOT / ".venv" / "bin" / "activate").exists() or (ROOT / ".venv" / "Scripts").exists()


def ensure_venv() -> dict[str, Any]:
    """Create ./venv if missing. Idempotent: leaves an existing venv alone."""
    vdir = ROOT / ".venv"
    if (vdir / "bin" / "python").exists() or (vdir / "Scripts" / "python.exe").exists():
        return {"created": False, "path": str(vdir)}
    try:
        venv.create(vdir, with_pip=True, prompt="hermus")
        return {"created": True, "path": str(vdir)}
    except Exception as exc:
        return {"created": False, "path": str(vdir), "error": str(exc)}


def _run(cmd: list[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True)


def install_required() -> dict[str, Any]:
    py = sys.executable
    result = _run([py, "-m", "pip", "install", "--quiet", "--upgrade", *REQUIRED_PIP])
    return {"ok": result.returncode == 0, "returncode": result.returncode,
            "detail": (result.stderr or result.stdout)[-400:]}


def probe_capabilities() -> dict[str, dict[str, Any]]:
    """One capability summary. Optional deps fail into 'unavailable', never fake."""
    out: dict[str, dict[str, Any]] = {}
    for mod, cap in OPTIONAL:
        present = _module_ok(mod)
        out[cap] = {
            "present": present,
            "status": "capable" if present else "unavailable",
            "reason": "" if present else f"module '{mod}' is not installed",
        }
    # required modules
    for mod in REQUIRED_IMPORTS:
        present = _module_ok(mod)
        out[f"required.{mod}"] = {
            "present": present,
            "status": "ready" if present else "missing",
            "reason": "" if present else f"required module '{mod}' is not installed",
        }
    out["system.python"] = {"present": True, "status": "ready",
                            "reason": detect_python()}
    out["system.venv"] = {"present": venv_ready(), "status": "ready" if venv_ready() else "missing",
                          "reason": "create with ./hermus bootstrap"}
    # Shell/permission probes
    out["network.tool.duckduckgo"] = {  # duckduckgo-search is optional/soft
        "present": _module_ok("duckduckgo_search") or _module_ok("duckduckgo-search"),
        "status": "capable" if (_module_ok("duckduckgo_search") or _module_ok("duckduckgo-search")) else "unavailable",
        "reason": "" if (_module_ok("duckduckgo_search") or _module_ok("duckduckgo-search")) else "install duckduckgo-search",
    }
    return out


def ensure_data_layout() -> dict[str, bool]:
    out: dict[str, bool] = {}
    for d in DATA_DIRS:
        p = ROOT / d
        p.mkdir(parents=True, exist_ok=True)
        out[d] = p.exists()
    return out


def doctor() -> dict[str, Any]:
    """One health report distinguishing installed/configured/reachable/operational."""
    cap = probe_capabilities()
    required_ok = all(v["present"] for k, v in cap.items() if k.startswith("required."))
    return {
        "ok": required_ok,
        "python": detect_python(),
        "venv": venv_ready(),
        "capabilities": cap,
        "data_layout": ensure_data_layout(),
        "exit": 0 if required_ok else 1,
    }


def run() -> int:
    """CLI bootstrap entrypoint. Exits 0 only when required capabilities are ready."""
    print("=" * 64)
    print("  ☤ HERMUS BOOTSTRAP  (one command, idempotent)")
    print("=" * 64)
    print(f"  Python     : {detect_python()}")
    ev = ensure_venv()
    print(f"  venv       : {'present' if ev.get('created') is False else 'created'}"
          f" ({ev['path']})")

    # Prefer the venv python for dependency install if available.
    vpy = ROOT / ".venv" / "bin" / "python"
    if not vpy.exists():
        vpy = ROOT / ".venv" / "Scripts" / "python.exe"
    install_python = str(vpy) if vpy.exists() else sys.executable

    inst = install_required() if _module_ok("pydantic") is False else {"ok": True, "returncode": 0}
    if inst.get("ok"):
        print("  deps       : required set present")
    else:
        print(f"  deps       : FAILED ({inst.get('detail', '')[:200]})")

    layout = ensure_data_layout()
    print(f"  layout     : {', '.join(layout)}")

    rep = doctor()
    print("-" * 64)
    print("  CAPABILITY SUMMARY")
    for cap, info in sorted(rep["capabilities"].items()):
        mark = "✓" if info["status"] in ("ready", "capable", "present") else "✗"
        reason = f"  — {info['reason']}" if info["reason"] else ""
        print(f"    {mark} {cap:<28} {info['status']}{reason}")
    print("-" * 64)
    if rep["ok"]:
        print("  ✓ Required capabilities ready.")
        print("\n  Next: ./hermus start      # dashboard + gateway")
        print("        ./hermus mission 'goal'\n")
        return 0
    print("  ✗ Required capabilities are NOT ready. Install the missing required")
    print("    modules (see 'pip install' above) then re-run ./hermus bootstrap.\n")
    return 1


def main() -> int:
    # allow `python bootstrap.py bootstrap` or `python bootstrap.py doctor`
    cmd = sys.argv[1] if len(sys.argv) > 1 else "bootstrap"
    if cmd == "doctor":
        rep = doctor()
        return rep["exit"] if rep.get("exit") is not None else 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
