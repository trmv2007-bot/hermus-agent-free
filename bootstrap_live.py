#!/usr/bin/env python3
"""Live-output bootstrap launcher.

The canonical bootstrap deliberately captures subprocess output for API/reporting.
For interactive setup we wrap only download/install subprocesses so their output
is streamed to the terminal while still returning a normal CompletedProcess to
bootstrap.py.
"""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
import time
from typing import Any

_original_run = subprocess.run


def _is_install_command(cmd: Any) -> bool:
    if not isinstance(cmd, (list, tuple)):
        return False
    parts = [str(x).lower() for x in cmd]
    joined = " ".join(parts)
    return (
        " -m pip " in f" {joined} "
        or "playwright" in joined
        and " install" in joined
        or "scrapling" in joined
        and " install" in joined
    )


def _live_run(*popen_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    cmd = popen_args[0] if popen_args else kwargs.get("args")
    if not _is_install_command(cmd) or kwargs.get("input") is not None:
        return _original_run(*popen_args, **kwargs)

    cwd = kwargs.pop("cwd", None)
    timeout = kwargs.pop("timeout", None)
    check = kwargs.pop("check", False)
    env = dict(kwargs.pop("env", None) or os.environ)
    env.setdefault("PIP_PROGRESS_BAR", "on")
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")

    # bootstrap.py requests captured text output. We stream it and retain it so
    # its existing error reporting remains unchanged.
    print("\n▶ HERMUS download/install in progress", flush=True)
    print("  Package download progress is live below; sizes/ETA appear when pip or the installer provides them.", flush=True)
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            print(f"  {line.rstrip()}", flush=True)
            lines.append(line)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        output = "".join(lines)
        raise
    output = "".join(lines)
    elapsed = time.monotonic() - started
    state = "DONE" if proc.returncode == 0 else "FAILED"
    print(f"◀ HERMUS download/install {state} ({elapsed:.1f}s)", flush=True)
    result = subprocess.CompletedProcess(cmd, proc.returncode, output, "")
    if check and proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=output, stderr="")
    return result


subprocess.run = _live_run  # type: ignore[assignment]
sys.argv = [str(__import__("pathlib").Path(__file__).resolve().parent / "bootstrap.py"), *sys.argv[1:]]
runpy.run_path(sys.argv[0], run_name="__main__")
