"""Host detection and launch plumbing for the native presence shell.

The shell is a thin Electron window: transparent, frameless, always-on-top and
click-through everywhere except the character itself. It is *optional* — HERMUS
behaves exactly as it does today without it, and the renderer page works in any
browser tab on its own.

Why Electron and not a native Win32 layered window: the renderer is deliberately
a web page (themable, screenshot-verifiable, one implementation shared by the
browser and the shell), so the shell has to host a browser engine anyway. A
ctypes/pywin32 window would mean re-implementing the character in GDI+ or
importing a COM WebView2 host, which is more surface, more Windows-only code, and
nothing the person can see the difference of. Electron is verified here on
Windows 11; the macOS/Linux path is documented honestly in ``docs/presence.md``.

The Electron runtime is installed under the repository's own ``data/`` directory
(``data/presence_shell``), never into the checkout and never system-wide, so
uninstalling is deleting one folder.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config import config
from core.log import get_logger

logger = get_logger(__name__)

#: Verified pin. Electron 45 is the current stable series at the time of writing.
ELECTRON_VERSION = "45.4.1"
SHELL_DIR = Path(__file__).parent / "shell"
RUNTIME_DIR_NAME = "presence_shell"
GATEWAY_PID_FILE = "gateway.pid"
SHELL_PID_FILE = "shell.pid"


def runtime_dir() -> Path:
    path = config.resolve_path(f"data/{RUNTIME_DIR_NAME}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def electron_binary() -> Path | None:
    """Absolute path to the installed Electron executable, or None."""
    root = runtime_dir() / "node_modules" / "electron" / "dist"
    if platform.system() == "Windows":
        candidate = root / "electron.exe"
    elif platform.system() == "Darwin":
        candidate = root / "Electron.app" / "Contents" / "MacOS" / "Electron"
    else:
        candidate = root / "electron"
    return candidate if candidate.exists() else None


def npm_binary() -> str | None:
    for name in ("npm.cmd", "npm") if platform.system() == "Windows" else ("npm",):
        found = shutil.which(name)
        if found:
            return found
    return None


@dataclass(frozen=True)
class ShellStatus:
    """What the CLI can truthfully say about the native shell right now."""

    platform: str
    supported: bool
    electron: str | None
    npm: str | None
    runtime_dir: str
    app_dir: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "supported": self.supported,
            "electron": self.electron,
            "npm": self.npm,
            "runtime_dir": self.runtime_dir,
            "app_dir": self.app_dir,
            "reason": self.reason,
        }


def shell_status() -> ShellStatus:
    """Probe the native shell without installing or launching anything."""
    system = platform.system()
    electron = electron_binary()
    npm = npm_binary()
    supported = system in ("Windows", "Darwin", "Linux")
    reason = ""
    if not supported:
        reason = f"native shell is not built for {system}"
    elif electron is None and npm is None:
        reason = "needs Node.js/npm to install the Electron runtime (hermus presence install)"
    elif electron is None:
        reason = "Electron runtime not installed yet (hermus presence install)"
    return ShellStatus(
        platform=system,
        supported=supported,
        electron=str(electron) if electron else None,
        npm=npm,
        runtime_dir=str(runtime_dir()),
        app_dir=str(SHELL_DIR),
        reason=reason,
    )


def install_runtime(timeout: float = 900.0) -> dict[str, Any]:
    """Install the pinned Electron runtime under ``data/presence_shell``.

    Never touches the checkout and never runs system-wide: the result is one
    directory the person can delete.
    """
    status = shell_status()
    if not status.supported:
        return {"ok": False, "error": status.reason}
    if status.electron:
        return {"ok": True, "already": True, "electron": status.electron}
    npm = status.npm
    if not npm:
        return {
            "ok": False,
            "error": "npm not found; install Node.js 20+ (free) and re-run `hermus presence install`",
        }
    target = runtime_dir()
    package_json = target / "package.json"
    if not package_json.exists():
        package_json.write_text(
            json.dumps({"name": "hermus-presence-runtime", "private": True, "version": "0.0.0"}, indent=2),
            encoding="utf-8",
        )
    cmd = [npm, "install", f"electron@{ELECTRON_VERSION}", "--no-audit", "--no-fund", "--prefix", str(target)]
    logger.info(f"[presence] installing Electron {ELECTRON_VERSION} into {target}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(target), check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"npm install failed: {exc}"}
    binary = electron_binary()
    if binary is None and proc.returncode == 0:
        # npm can call the package "up to date" while the runtime binary was
        # never fetched (an interrupted first install leaves exactly that
        # state). The package ships its own installer for this case; run it.
        node = shutil.which("node")
        installer = target / "node_modules" / "electron" / "install.js"
        if node and installer.exists():
            logger.info("[presence] electron binary missing; running the package installer")
            try:
                proc = subprocess.run(
                    [node, str(installer)], capture_output=True, text=True, timeout=timeout, cwd=str(target), check=False
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return {"ok": False, "error": f"electron installer failed: {exc}"}
            binary = electron_binary()
    if proc.returncode != 0 or binary is None:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        return {"ok": False, "error": "electron download failed", "detail": "\n".join(tail)}
    return {"ok": True, "electron": str(binary), "version": ELECTRON_VERSION}


def _pid_file(name: str) -> Path:
    return runtime_dir() / name


def read_pid(name: str) -> int | None:
    path = _pid_file(name)
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_pid(name: str, pid: int) -> None:
    _pid_file(name).write_text(str(int(pid)), encoding="utf-8")


def clear_pid(name: str) -> None:
    try:
        _pid_file(name).unlink()
    except OSError:
        pass


def pid_alive(pid: int | None) -> bool:
    """True only when a process with ``pid`` exists (Windows-safe, no deps)."""
    if not pid or pid <= 0:
        return False
    if platform.system() == "Windows":
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return str(pid) in (proc.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def launch_shell(url: str, token: str | None = None, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    """Start the always-on-top shell pointed at ``url``. Returns its pid."""
    electron = electron_binary()
    if electron is None:
        return {"ok": False, "error": shell_status().reason or "Electron runtime not installed"}
    env = dict(os.environ)
    env["HERMUS_PRESENCE_URL"] = url
    if token:
        env["HERMUS_PRESENCE_TOKEN"] = token
    env.setdefault("ELECTRON_DISABLE_SECURITY_WARNINGS", "1")
    if extra_env:
        env.update(extra_env)
    cmd = [str(electron), str(SHELL_DIR)]
    logger.info(f"[presence] launching shell: {electron} {SHELL_DIR} -> {url}")
    try:
        kwargs: dict[str, Any] = {
            "env": env,
            "cwd": str(SHELL_DIR),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if platform.system() == "Windows":
            kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError as exc:
        return {"ok": False, "error": f"could not launch shell: {exc}"}
    write_pid(SHELL_PID_FILE, proc.pid)
    return {"ok": True, "pid": proc.pid, "url": url}


def stop_shell() -> dict[str, Any]:
    """Stop the shell this CLI started. Never touches any other process."""
    pid = read_pid(SHELL_PID_FILE)
    if pid is None:
        return {"ok": True, "stopped": False, "detail": "no shell pid recorded"}
    if not pid_alive(pid):
        clear_pid(SHELL_PID_FILE)
        return {"ok": True, "stopped": False, "detail": "shell already gone"}
    try:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20, check=False)
        else:
            os.kill(pid, 15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"could not stop shell {pid}: {exc}"}
    # Only forget the pid once the process is actually gone: clearing it while a
    # window is still up would leave the person with a character they cannot
    # dismiss and make the next `stop` say "nothing to stop".
    for _ in range(25):
        if not pid_alive(pid):
            clear_pid(SHELL_PID_FILE)
            return {"ok": True, "stopped": True, "pid": pid}
        time.sleep(0.2)
    return {"ok": False, "error": f"shell {pid} did not exit", "pid": pid}


def stop_gateway() -> dict[str, Any]:
    """Stop a gateway this CLI spawned (joined to the presence lifecycle only)."""
    pid = read_pid(GATEWAY_PID_FILE)
    if pid is None or not pid_alive(pid):
        clear_pid(GATEWAY_PID_FILE)
        return {"ok": True, "stopped": False}
    try:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20, check=False)
        else:
            os.kill(pid, 15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    clear_pid(GATEWAY_PID_FILE)
    return {"ok": True, "stopped": True, "pid": pid}


def spawn_gateway(port: int) -> dict[str, Any]:
    """Start ``hermus gateway start`` detached, remembering the pid we created."""
    repo_root = config.base_dir
    env = dict(os.environ)
    env.setdefault("HERMUS_PRESENCE_ONLY", "1")
    cmd = [sys.executable, "-m", "gateway.gateway", "start", "--port", str(port)]
    try:
        kwargs: dict[str, Any] = {
            "cwd": str(repo_root),
            "env": env,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if platform.system() == "Windows":
            kwargs["creationflags"] = 0x00000008 | 0x00000200
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError as exc:
        return {"ok": False, "error": f"could not start gateway: {exc}"}
    write_pid(GATEWAY_PID_FILE, proc.pid)
    return {"ok": True, "pid": proc.pid}


__all__ = [
    "ELECTRON_VERSION",
    "SHELL_DIR",
    "ShellStatus",
    "clear_pid",
    "electron_binary",
    "install_runtime",
    "launch_shell",
    "npm_binary",
    "pid_alive",
    "read_pid",
    "runtime_dir",
    "shell_status",
    "spawn_gateway",
    "stop_gateway",
    "stop_shell",
    "write_pid",
]
