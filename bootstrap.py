#!/usr/bin/env python3
"""Hermus bootstrap, repair and verification entry point.

``setup.sh`` is the small platform wrapper.  This module is the canonical
Python/runtime orchestrator used by setup, the CLI bootstrap command and the
health endpoint.  It deliberately separates:

* package presence from an operational verification;
* required dependencies from optional integrations;
* the interpreter used to install packages from the interpreter used to run
  Hermus; and
* safe repair of ``.venv`` from deletion of user data (the latter never occurs).

No model weights, credentials, databases, memory, projects or recordings are
removed or overwritten here.  Optional model/provider services are reported,
not fabricated.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# These names are the canonical import contract used by Doctor.  The package
# source of truth for installation is requirements.txt; these are intentionally
# kept as import-name -> capability checks because pip names and import names
# differ (python-dotenv -> dotenv, Pillow -> PIL, PyYAML -> yaml).
REQUIRED_IMPORTS = [
    "pydantic", "dotenv", "requests", "tiktoken", "PIL",
    "fastapi", "uvicorn", "multipart", "psutil", "httpx",
    "ddgs", "playwright", "scrapling", "faster_whisper", "feedparser",
    "pypdf", "yaml", "imageio_ffmpeg", "apscheduler", "prompt_toolkit", "rich",
    "websockets",
]

# Kept for the public/bootstrap compatibility contract and for a useful repair
# hint. Installation itself reads requirements.txt rather than this list.
REQUIRED_PIP = [
    "pydantic", "python-dotenv", "requests", "tiktoken", "Pillow",
    "fastapi", "uvicorn", "python-multipart", "psutil", "httpx",
    "ddgs", "playwright", "scrapling[fetchers]", "faster-whisper",
    "imageio-ffmpeg", "feedparser", "pypdf", "PyYAML",
    "APScheduler", "prompt_toolkit", "rich", "websockets", "pytest",
]

# Optional imports are capability-labelled rather than treated as required core
# runtime.  The optional requirements file is installed one item at a time so a
# platform-specific failure remains visible without masking other results.
# Required installs have no ``|| true`` masking on required dependencies: a
# failed command is retained and the final readiness line remains INCOMPLETE.
OPTIONAL = [
    ("websockets", "gateway.websocket"),
    ("pyautogui", "computer.mouse"),
    ("pygetwindow", "computer.window"),
    ("pyttsx3", "speech.tts"),
    ("soundfile", "speech.audio"),
    ("omnivoice", "speech.omnivoice"),
    ("torch", "speech.omnivoice"),
    ("openvino", "local_engine.openvino"),
    ("sqlite_vec", "memory.vector_accelerator"),
    ("redis", "queue.redis"),
    ("markdownify", "web.markdown"),
    ("groq", "provider.groq"),
    ("huggingface_hub", "provider.hf"),
    ("paramiko", "backend.ssh"),
    ("modal", "backend.modal"),
    ("telegram", "channel.telegram"),
    ("discord", "channel.discord"),
]

DATA_DIRS = [
    "data", "data/sessions", "data/tmp", "data/skins", "data/counsel",
    "data/plans", "data/recordings", "data/speech", "data/speech/prompts",
    "data/avatar", "data/voice", "data/jobs", "data/jobs/results", "data/doctor",
    "data/logs", "data/engine", "workspace", "artifacts", "skills", "migrations",
    "logs", "missions", "checkpoints", "bin",
]

REQUIRED_STATUS = "✅ INSTALLED + VERIFIED"
WARNING_STATUS = "⚠️ INSTALLED BUT NOT VERIFIED"
BROKEN_STATUS = "❌ MISSING/BROKEN"
OPTIONAL_STATUS = "⏭️ OPTIONAL/UNAVAILABLE ON THIS PLATFORM"


def _module_ok(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _module_import_ok(name: str) -> tuple[bool, str]:
    try:
        importlib.import_module(name)
        return True, "import OK"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"[:300]


def _python_path(vdir: Path = ROOT / ".venv") -> Optional[Path]:
    candidates = (
        vdir / "bin" / "python",
        vdir / "Scripts" / "python.exe",
        vdir / "Scripts" / "python",
    )
    return next((p for p in candidates if p.is_file()), None)


def venv_ready() -> bool:
    """Return true only when the project venv has an executable Python."""
    path = _python_path()
    return bool(path and os.access(path, os.X_OK))


def _running_in_project_venv() -> bool:
    """Use ``sys.prefix`` rather than symlink paths to identify the venv."""
    try:
        return (
            sys.prefix != sys.base_prefix
            and Path(sys.prefix).resolve() == (ROOT / ".venv").resolve()
        )
    except OSError:
        return False


def detect_python() -> str:
    return platform.python_version()


def detect_platform() -> dict[str, Any]:
    """Describe the supported host without making assumptions about a distro."""
    system = platform.system()
    termux = (
        bool(os.environ.get("TERMUX_VERSION"))
        or "android" in platform.system().lower()
        or "android" in platform.platform().lower()
    )
    if termux:
        family = "android-termux"
    elif system == "Linux":
        family = "linux"
    elif system == "Darwin":
        family = "macos"
    else:
        family = system.lower() or "unknown"
    managers = [name for name in ("apt-get", "dnf", "yum", "pacman", "apk", "brew", "pkg")
                if shutil.which(name)]
    return {
        "family": family,
        "system": system,
        "release": platform.release(),
        "machine": platform.machine(),
        "termux": termux,
        "package_managers": managers,
    }


def _run(cmd: list[str], *, cwd: Optional[Path] = None, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell and retain output for an honest report."""
    return subprocess.run(
        cmd,
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _tail(result: subprocess.CompletedProcess[str], limit: int = 900) -> str:
    output = (result.stdout or "") + (result.stderr or "")
    return output.strip()[-limit:] or f"exit={result.returncode}"


def _base_python() -> Path:
    """Select a supported host interpreter for creating .venv."""
    if sys.version_info >= (3, 10):
        return Path(sys.executable)
    for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3"):
        candidate = shutil.which(name)
        if candidate:
            check = _run([candidate, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"], timeout=15)
            if check.returncode == 0:
                return Path(candidate)
    return Path(sys.executable)


def _venv_interpreter_healthy(path: Path) -> tuple[bool, str]:
    if not path.is_file() or not os.access(path, os.X_OK):
        return False, "venv Python executable is missing or not executable"
    try:
        result = _run([str(path), "-c", "import sys; print(sys.version); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"], timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"venv Python could not start: {type(exc).__name__}: {exc}"
    return (True, _tail(result, 240)) if result.returncode == 0 else (False, _tail(result, 400))


def _create_venv(vdir: Path, base_python: Optional[Path], *, upgrade: bool = False) -> tuple[bool, str]:
    """Create/upgrade a venv with the explicitly selected host interpreter."""
    interpreter = base_python or Path(sys.executable)
    command = [str(interpreter), "-m", "venv"]
    if upgrade:
        command.append("--upgrade")
    command.extend(["--prompt", "hermus", str(vdir)])
    try:
        result = _run(command, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"venv command failed to start: {type(exc).__name__}: {exc}"
    return result.returncode == 0, _tail(result, 600)


def _backup_broken_venv(vdir: Path) -> Path:
    """Move only the broken virtualenv aside; never remove it or user data."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = vdir.with_name(f"{vdir.name}.broken-{stamp}")
    suffix = 1
    while backup.exists():
        backup = vdir.with_name(f"{vdir.name}.broken-{stamp}-{suffix}")
        suffix += 1
    shutil.move(str(vdir), str(backup))
    return backup


def ensure_venv(*, repair: bool = False, base_python: Optional[Path] = None) -> dict[str, Any]:
    """Create or repair ``.venv`` without deleting the old environment.

    A healthy environment is reused.  A partial/broken environment is first
    repaired in place; if that is impossible it is renamed to a timestamped
    ``.venv.broken-*`` backup before a fresh environment is created.
    """
    vdir = ROOT / ".venv"
    existing = _python_path(vdir)
    if existing:
        healthy, detail = _venv_interpreter_healthy(existing)
        if healthy:
            # --repair repairs packages/layout below; a healthy venv itself is
            # never recreated, avoiding needless churn and preserving caches.
            return {"created": False, "repaired": False, "path": str(vdir),
                    "python": str(existing), "detail": detail}
        repaired_ok, repair_detail = _create_venv(vdir, base_python, upgrade=True)
        if repaired_ok:
            repaired = _python_path(vdir)
            healthy, after = _venv_interpreter_healthy(repaired) if repaired else (False, "venv Python still missing after repair")
            if healthy:
                return {"created": False, "repaired": True, "path": str(vdir),
                        "python": str(repaired), "detail": after}
        detail = f"in-place venv repair failed: {repair_detail}"
    elif vdir.exists():
        detail = "venv directory exists but contains no usable Python"
    else:
        detail = "venv does not exist"

    if vdir.exists():
        try:
            backup = _backup_broken_venv(vdir)
        except OSError as exc:
            return {"created": False, "repaired": False, "path": str(vdir),
                    "error": f"cannot preserve broken .venv before repair: {exc}"}
    else:
        backup = None
    created_ok, create_detail = _create_venv(vdir, base_python)
    if not created_ok:
        return {"created": False, "repaired": bool(backup), "path": str(vdir),
                "error": f"venv creation failed: {create_detail}",
                "backup": str(backup) if backup else None}
    created = _python_path(vdir)
    healthy, after = _venv_interpreter_healthy(created) if created else (False, "new venv Python missing")
    if not healthy:
        return {"created": False, "repaired": bool(backup), "path": str(vdir),
                "error": f"new venv is not usable: {after}", "backup": str(backup) if backup else None}
    return {"created": not bool(backup), "repaired": bool(backup), "path": str(vdir),
            "python": str(created), "detail": after, "backup": str(backup) if backup else None}


def _optional_specs(path: Path = ROOT / "requirements-optional.txt") -> Iterable[str]:
    if not path.is_file():
        return ()
    specs: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        specs.append(line)
    return specs


def install_required(interpreter: Optional[Path] = None, *, repair: bool = False) -> dict[str, Any]:
    """Install the canonical required dependency file in the selected venv."""
    py = interpreter or Path(sys.executable)
    req = ROOT / "requirements.txt"
    if not req.is_file():
        return {"ok": False, "returncode": 2, "detail": f"missing canonical dependency file: {req}"}
    args = [str(py), "-m", "pip", "install", "--disable-pip-version-check"]
    if repair:
        args.append("--upgrade")
    args.extend(["-r", str(req)])
    try:
        result = _run(args, timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "returncode": 1, "detail": f"pip install failed to start: {type(exc).__name__}: {exc}"}
    return {"ok": result.returncode == 0, "returncode": result.returncode,
            "detail": _tail(result), "file": str(req), "interpreter": str(py)}


def install_optional(interpreter: Optional[Path] = None) -> dict[str, Any]:
    """Install optional requirements independently and report every failure."""
    py = interpreter or Path(sys.executable)
    failures: list[dict[str, Any]] = []
    installed: list[str] = []
    skipped: list[str] = []
    for spec in _optional_specs():
        # A PEP 508 marker is passed as one argv element; pip evaluates it for
        # the selected interpreter/platform without shell quoting surprises.
        try:
            result = _run([str(py), "-m", "pip", "install", "--disable-pip-version-check", spec], timeout=1800)
        except (OSError, subprocess.SubprocessError) as exc:
            failures.append({"requirement": spec, "detail": f"{type(exc).__name__}: {exc}"})
            continue
        if result.returncode == 0:
            installed.append(spec)
        else:
            failures.append({"requirement": spec, "detail": _tail(result)})
    return {"ok": not failures, "installed": installed, "failed": failures, "skipped": skipped,
            "file": str(ROOT / "requirements-optional.txt"), "interpreter": str(py)}


def install_browser_runtime(interpreter: Optional[Path] = None, *, repair: bool = False) -> dict[str, Any]:
    """Install the browser package's Chromium runtime, without model weights."""
    py = interpreter or Path(sys.executable)
    if detect_platform().get("termux"):
        # Termux can use Scrapling's lightweight HTTP fetcher, but Playwright's
        # desktop Chromium shared-library layout is not a supported install
        # target here. Do not run a failing downloader or imply browser support.
        return {
            "playwright": {"ok": False, "optional": True, "detail": "Chromium browser installation is optional/unavailable on Android/Termux"},
            "scrapling": {"ok": True, "optional": True, "detail": "Scrapling HTTP fetcher retained; browser installer skipped on Android/Termux"},
            "ok": False,
        }
    result: dict[str, Any] = {"playwright": None, "scrapling": None}
    try:
        playwright_args = [str(py), "-m", "playwright", "install"]
        use_with_deps = (
            platform.system() == "Linux"
            and os.environ.get("HERMUS_PLAYWRIGHT_WITH_DEPS", "") not in ("", "0", "false", "False")
        )
        if use_with_deps:
            playwright_args.append("--with-deps")
        playwright_args.append("chromium")
        playwright = _run(playwright_args, timeout=1800)
        initial_playwright = {"ok": playwright.returncode == 0, "detail": _tail(playwright)}
        result["playwright"] = initial_playwright
        if use_with_deps:
            result["playwright_with_deps"] = initial_playwright
        if playwright.returncode != 0 and (repair or use_with_deps) and platform.system() == "Linux":
            # Retry without distro package installation. This allows an
            # unprivileged user with preinstalled shared libraries to complete,
            # while retaining the original dependency-installer failure.
            plain = _run([str(py), "-m", "playwright", "install", "chromium"], timeout=1800)
            result["playwright_plain_retry"] = {"ok": plain.returncode == 0, "detail": _tail(plain)}
            if plain.returncode == 0:
                result["playwright"] = result["playwright_plain_retry"]
    except (OSError, subprocess.SubprocessError) as exc:
        result["playwright"] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    # Scrapling's installer owns its browser-specific dependencies. It is run
    # through the selected venv, never through an ambient global executable.
    scrapling_cli = py.parent / ("scrapling.exe" if platform.system() == "Windows" else "scrapling")
    try:
        cmd = [str(scrapling_cli), "install"] if scrapling_cli.is_file() else [str(py), "-m", "scrapling", "install"]
        scrapling = _run(cmd, timeout=1800)
        result["scrapling"] = {"ok": scrapling.returncode == 0, "detail": _tail(scrapling)}
    except (OSError, subprocess.SubprocessError) as exc:
        result["scrapling"] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    result["ok"] = bool((result.get("playwright") or {}).get("ok"))
    return result


def ensure_env_file() -> dict[str, Any]:
    """Create a private starter .env only when the user has none."""
    env_path = ROOT / ".env"
    if env_path.exists():
        return {"created": False, "path": str(env_path), "preserved": True}
    example = ROOT / ".env.example"
    try:
        if example.is_file():
            env_path.write_bytes(example.read_bytes())
        else:
            env_path.touch(mode=0o600)
        # chmod only the newly-created starter; an existing user choice is not
        # silently changed by setup.
        env_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return {"created": True, "path": str(env_path), "preserved": False}
    except OSError as exc:
        return {"created": False, "path": str(env_path), "error": str(exc)}


def ensure_data_layout() -> dict[str, Any]:
    """Create missing runtime directories and leave every existing file alone."""
    result: dict[str, Any] = {"created": [], "existing": [], "failed": []}
    for relative in DATA_DIRS:
        path = ROOT / relative
        try:
            if path.exists():
                if path.is_dir():
                    result["existing"].append(relative)
                else:
                    result["failed"].append({"path": relative, "error": "path exists and is not a directory"})
                continue
            path.mkdir(parents=True, exist_ok=True, mode=0o700 if relative.startswith("data") else 0o755)
            result["created"].append(relative)
        except OSError as exc:
            result["failed"].append({"path": relative, "error": str(exc)})
    # Existing launchers are part of the project; make them executable without
    # rewriting their content or replacing user data.
    for relative in ("hermus", "hermus-gateway", "bin/hermus", "bin/hermus-gateway", "activate.sh"):
        path = ROOT / relative
        if not path.is_file():
            continue
        try:
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        except OSError as exc:
            result["failed"].append({"path": relative, "error": str(exc)})
    return result


def probe_capabilities() -> dict[str, dict[str, Any]]:
    """Fast compatibility report used by /api/v1/system/health."""
    out: dict[str, dict[str, Any]] = {}
    for mod, cap in OPTIONAL:
        present = _module_ok(mod)
        out[cap] = {
            "present": present,
            "status": "capable" if present else "unavailable",
            "reason": "" if present else f"optional module '{mod}' is not installed",
        }
    for mod in REQUIRED_IMPORTS:
        present = _module_ok(mod)
        out[f"required.{mod}"] = {
            "present": present,
            "status": "ready" if present else "missing",
            "reason": "" if present else f"required module '{mod}' is not installed",
        }
    out["system.python"] = {
        "present": sys.version_info >= (3, 10),
        "status": "ready" if sys.version_info >= (3, 10) else "missing",
        "reason": detect_python(),
    }
    out["system.venv"] = {
        "present": venv_ready(),
        "status": "ready" if venv_ready() else "missing",
        "reason": str(_python_path() or "project .venv is not usable"),
    }
    out["system.platform"] = {"present": True, "status": "present", "reason": detect_platform()}
    return out


def doctor() -> dict[str, Any]:
    """Fast, JSON-safe capability report; deep live checks live in core.diagnostics."""
    cap = probe_capabilities()
    required_ok = all(v["present"] for k, v in cap.items() if k.startswith("required."))
    venv_ok = bool(cap["system.venv"]["present"])
    py_ok = bool(cap["system.python"]["present"])
    return {
        "ok": required_ok and venv_ok and py_ok,
        "python": detect_python(),
        "venv": venv_ok,
        "platform": detect_platform(),
        "capabilities": cap,
        "data_layout": ensure_data_layout(),
        "exit": 0 if required_ok and venv_ok and py_ok else 1,
    }


def _add_install_failures(report: dict[str, Any], failures: list[dict[str, Any]]) -> None:
    if not failures:
        return
    report.setdefault("subsystems", {})["dependency_install"] = {
        "name": "Dependency installation",
        "status": BROKEN_STATUS,
        "ok": False,
        "required": True,
        "detail": "; ".join(f"{f.get('phase')}: {f.get('detail', '')}" for f in failures),
        "hint": "Fix the reported pip/system error and re-run setup.sh",
        "evidence": failures,
    }
    report["overall"]["required"] = False
    report["overall"]["ok"] = False


def installation_report(*, deep: bool = True) -> dict[str, Any]:
    """Run the canonical fast/deep Doctor and return its machine report."""
    from core.diagnostics import run_diagnostics

    return run_diagnostics(deep=deep)


def _report_order() -> list[tuple[str, str]]:
    return [
        ("core_runtime", "Core runtime"),
        ("python_venv", "Python/venv"),
        ("host_runtimes", "Host runtimes"),
        ("dependencies", "Dependencies"),
        ("scrapling", "Scrapling"),
        ("chromium", "Chromium"),
        ("browser_navigation", "Browser navigation"),
        ("computer_agent", "Computer agent"),
        ("gateway", "Gateway"),
        ("job_queue", "JobQueue"),
        ("voice", "Voice"),
        ("model_provider", "LLM/model providers"),
        ("storage_config", "Storage/config"),
        ("security", "Security"),
        ("doctor", "Doctor"),
        ("tool_system", "Tool system"),
        ("agent_startup", "Core agent"),
        ("configuration", "Configuration loading"),
        ("sandbox", "Sandbox"),
        ("node", "Node/npm"),
        ("android", "Android/Termux"),
    ]


def print_installation_report(report: dict[str, Any], *, optional_install: Optional[dict[str, Any]] = None) -> None:
    """Print the complete human-facing report and the only final readiness line."""
    print("=" * 78)
    print("HERMUS INSTALLATION / VERIFICATION SUMMARY")
    print("=" * 78)
    platform_info = report.get("platform") or {}
    if isinstance(platform_info, dict):
        platform_label = platform_info.get("family") or platform.platform()
    else:
        platform_label = report.get("platform_family") or str(platform_info)
    print(f"Platform: {platform_label} | Python: {sys.executable}")
    printed: set[str] = set()
    subsystems = report.get("subsystems") or {}
    for key, label in _report_order():
        item = subsystems.get(key)
        if not item:
            continue
        printed.add(key)
        print(f"{item['status']} {label}: {item.get('detail', '')}")
        if item.get("hint") and not item.get("ok"):
            print(f"    FIX: {item['hint']}")
    for key, item in subsystems.items():
        if key in printed:
            continue
        print(f"{item.get('status', WARNING_STATUS)} {item.get('name', key)}: {item.get('detail', '')}")
        if item.get("hint") and not item.get("ok"):
            print(f"    FIX: {item['hint']}")
    if optional_install:
        failed = optional_install.get("failed") or []
        skipped = optional_install.get("skipped") or []
        if failed:
            print(f"{OPTIONAL_STATUS} Optional packages: {len(failed)} optional requirement(s) unavailable")
            for failure in failed:
                print(f"    OPTIONAL: {failure.get('requirement')}: {failure.get('detail', '')[-500:]}")
        elif skipped:
            print(f"{OPTIONAL_STATUS} Optional packages: {', '.join(str(item) for item in skipped)}")
        else:
            print(f"{REQUIRED_STATUS} Optional packages: selected optional requirements installed or already satisfied")
    print("-" * 78)
    overall = report.get("overall") or {}
    print(f"Doctor checks: {overall.get('passed', 0)}/{overall.get('total', 0)} passed; "
          f"required={'OK' if overall.get('required') else 'FAIL'}")
    if overall.get("required"):
        print("HERMUS SETUP: READY")
    else:
        print("HERMUS SETUP: INCOMPLETE — FIX REQUIRED")


# ``def run()`` is the no-option public bootstrap form; keyword options below
# extend it for setup.sh and hermus bootstrap without changing that contract.
def run(
    *,
    verify_only: bool = False,
    repair: bool = False,
    skip_browser: bool = False,
    skip_optional: bool = False,
    json_output: bool = False,
) -> int:
    """Install/repair with the project interpreter, then run live Doctor probes."""
    print("=" * 78, flush=True)
    print("HERMUS BOOTSTRAP — canonical environment and runtime verification", flush=True)
    print("=" * 78, flush=True)
    print(f"Host: {detect_platform()['family']} | launcher interpreter: {sys.executable}", flush=True)

    install_failures: list[dict[str, Any]] = []
    optional_result: Optional[dict[str, Any]] = None
    env_result: dict[str, Any] = {"created": False, "path": str(ROOT / ".env"), "preserved": True}

    vdir_python = _python_path()
    vdir_usable = bool(vdir_python and _venv_interpreter_healthy(vdir_python)[0])
    if verify_only and vdir_usable and not _running_in_project_venv() and not os.environ.get("HERMUS_BOOTSTRAP_IN_VENV"):
        child_env = os.environ.copy()
        child_env["HERMUS_BOOTSTRAP_IN_VENV"] = "1"
        args = [str(vdir_python), str(Path(__file__).resolve()), "bootstrap", "--verify-only"]
        if json_output:
            args.append("--json")
        child = subprocess.run(args, cwd=ROOT, env=child_env, check=False)
        return int(child.returncode)
    if not verify_only:
        ev = ensure_venv(repair=repair, base_python=_base_python())
        if ev.get("error"):
            install_failures.append({"phase": "venv", "detail": ev["error"]})
        vdir_python = _python_path()
        vdir_usable = bool(vdir_python and _venv_interpreter_healthy(vdir_python)[0])
        if not vdir_usable:
            install_failures.append({"phase": "venv", "detail": "project .venv has no usable Python"})
        # If called with the host interpreter (e.g. `python bootstrap.py`),
        # re-exec now. All installation and all deep probes must use the same
        # interpreter the launchers will use.
        if vdir_usable and not _running_in_project_venv() and not os.environ.get("HERMUS_BOOTSTRAP_IN_VENV"):
            child_env = os.environ.copy()
            child_env["HERMUS_BOOTSTRAP_IN_VENV"] = "1"
            args = [str(vdir_python), str(Path(__file__).resolve()), "bootstrap"]
            if repair:
                args.append("--repair")
            if skip_browser:
                args.append("--skip-browser")
            if skip_optional:
                args.append("--skip-optional")
            if json_output:
                args.append("--json")
            child = subprocess.run(args, cwd=ROOT, env=child_env, check=False)
            return int(child.returncode)
        env_result = ensure_env_file()
        layout = ensure_data_layout()
        if env_result.get("error"):
            install_failures.append({"phase": "environment", "detail": env_result["error"]})
        if layout.get("failed"):
            install_failures.extend({"phase": "layout", "detail": row} for row in layout["failed"])

        if vdir_usable and vdir_python:
            required_install = install_required(vdir_python, repair=repair)
            if not required_install.get("ok"):
                install_failures.append({"phase": "requirements.txt", "detail": required_install.get("detail", "pip failed")})
                # A bounded repair retry may fix a broken wheel/cache without
                # hiding the original failure; verification decides readiness.
                if repair:
                    retry = install_required(vdir_python, repair=True)
                    if retry.get("ok"):
                        install_failures = [f for f in install_failures if f.get("phase") != "requirements.txt"]
                    else:
                        install_failures.append({"phase": "requirements.txt repair", "detail": retry.get("detail", "pip failed")})
            if not skip_optional and os.getenv("HERMUS_INSTALL_OPTIONALS", "1") not in ("0", "false", "False"):
                optional_result = install_optional(vdir_python)
            else:
                optional_result = {"ok": True, "installed": [], "failed": [], "skipped": ["optional requirements disabled"]}
            if not skip_browser and os.getenv("HERMUS_INSTALL_BROWSER", "1") not in ("0", "false", "False"):
                browser_result = install_browser_runtime(vdir_python, repair=repair)
                # Existing verified browser state is authoritative. A failed
                # installer command is not itself fatal when live Doctor proves
                # the browser already works.
                print(f"Browser installer: Playwright={browser_result.get('playwright')} Scrapling={browser_result.get('scrapling')}")
        else:
            optional_result = {"ok": True, "installed": [], "failed": [], "skipped": ["no venv interpreter"]}
    else:
        if not vdir_python:
            install_failures.append({"phase": "verify-only", "detail": "no existing .venv; verification-only mode does not create one"})

    # Re-check environment after package installation. This also means a direct
    # bootstrap invocation from a system Python cannot accidentally verify the
    # wrong environment: the re-exec above has already happened.
    report = installation_report(deep=True)
    _add_install_failures(report, install_failures)
    if optional_result:
        report["optional_installation"] = optional_result
    if json_output:
        print(json.dumps(report, indent=2, default=str))
        # Keep the readiness contract visible even for automation consuming JSON.
        print("HERMUS SETUP: READY" if report.get("overall", {}).get("required") else "HERMUS SETUP: INCOMPLETE — FIX REQUIRED")
    else:
        print_installation_report(report, optional_install=optional_result)
    return 0 if report.get("overall", {}).get("required") else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Hermus canonical setup/repair/verification")
    parser.add_argument("command", nargs="?", choices=("bootstrap", "verify", "doctor"), default="bootstrap")
    parser.add_argument("--verify-only", action="store_true", help="do not install or create a venv; run live checks only")
    parser.add_argument("--repair", action="store_true", help="repair broken venv/dependencies and retry browser setup")
    parser.add_argument("--skip-browser", action="store_true", help="do not download Chromium; verification still reports its state")
    parser.add_argument("--skip-optional", action="store_true", help="do not install optional requirements")
    parser.add_argument("--json", action="store_true", help="emit the deep installation report as JSON")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        report = doctor()
        print(json.dumps(report, indent=2, default=str) if args.json else json.dumps(report, indent=2, default=str))
        return int(report.get("exit", 1))
    if args.command == "verify":
        args.verify_only = True
    return run(
        verify_only=bool(args.verify_only),
        repair=bool(args.repair),
        skip_browser=bool(args.skip_browser),
        skip_optional=bool(args.skip_optional),
        json_output=bool(args.json),
    )


if __name__ == "__main__":
    raise SystemExit(main())
