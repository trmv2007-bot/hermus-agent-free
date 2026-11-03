"""Architecture gates: the read-model / always-on observer modules must stay
READ-ONLY — no subprocess, no os.system, no job execution, no direct
JobQueue writes.

Exemptions (by design):
- core/world_awareness.py — its whole job is read-only host observation via
  subprocess (psutil/ps fallbacks), never execution of user commands.
- core/sentinel.py — may enqueue via the canonical JobQueue and send
  notifications (notify-send/telegram); it never executes tools itself.
- core/harness/* — the harness deliberately executes tasks (that is its job).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

READ_MODEL_MODULES = [
    "core/presence_kernel.py",
    "core/ambient_loop.py",
    "core/world_model.py",
    "core/focus_os.py",
    "core/dashboard_state.py",
    "core/control_room_state.py",
]

FORBIDDEN_ATTRS = {"execute", "job_queue", "system", "Popen", "run", "call", "check_output", "check_call"}


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad: list[str] = []
    for node in ast.walk(tree):
        # imports of subprocess / job queue
        if isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names):
            bad.append(f"line {node.lineno}: import subprocess")
        if isinstance(node, ast.Attribute) and node.attr in {"execute", "submit", "enqueue"}:
            # job_queue.submit / anything.execute = execution, banned in read models
            bad.append(f"line {node.lineno}: .{node.attr}")
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "system"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            ):
                bad.append(f"line {node.lineno}: os.system(...) call")
            if isinstance(func, ast.Attribute) and func.attr in {"run", "call", "check_output", "check_call", "Popen"}:
                val = func.value
                if isinstance(val, ast.Name) and val.id == "subprocess":
                    bad.append(f"line {node.lineno}: subprocess.{func.attr}(...) call")
    return bad


@pytest.mark.parametrize("rel", READ_MODEL_MODULES)
def test_read_model_module_has_no_execution(rel: str) -> None:
    path = REPO / rel
    assert path.is_file(), f"{rel} missing"
    bad = _violations(path)
    assert not bad, f"{rel} must stay read-only; found: {bad}"


def test_sentinel_never_executes_tools_directly() -> None:
    """Sentinel may enqueue via JobQueue and notify, but must not subprocess-
    execute anything except the desktop notifier (notify-send)."""
    src = (REPO / "core/sentinel.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    subprocess_calls: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            val = node.func.value
            if (
                node.func.attr in {"run", "call", "check_output", "check_call", "Popen"}
                and isinstance(val, ast.Attribute)
                and val.attr == "subprocess"
            ) or (
                node.func.attr in {"run", "call", "check_output", "check_call"}
                and isinstance(val, ast.Name)
                and val.id == "subprocess"
            ):
                subprocess_calls.append(node.lineno)
    # every subprocess call must be inside the notify chain (notify-send push)
    notify_def = src.find("def _default_notify")
    assert notify_def >= 0, "sentinel must keep its _default_notify helper"
    offset = 0
    for i, line in enumerate(src.splitlines(keepends=True), 1):
        offset += len(line)
        if i in subprocess_calls:
            assert offset > notify_def, f"subprocess call at line {i} outside the notify chain"


def test_ambient_driver_is_event_only() -> None:
    """The ambient harness driver must only emit events — no job execution."""
    bad = _violations(REPO / "core/ambient_loop.py")
    assert not bad
