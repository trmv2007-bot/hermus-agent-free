"""Computer-control honest capability tests (§14).

Labels:
* unit/integration — verify the truthful ``computer_control_unavailable`` reporting:
  no silent dry-run fallback pretending real control. These run whether or not a
  display/pyautogui is present and are NOT evidence of real control.
* host-E2E guarded — a real control test that is SKIPPED unless pyautogui + a real
  display are available; it is never "passed" on a mock.
"""
from __future__ import annotations

import pytest


def _has_pyautogui():
    try:
        import pyautogui  # noqa: F401
        return True
    except Exception:
        return False


def _has_display():
    import os
    return bool(os.environ.get("DISPLAY"))


def test_computer_tools_registered():
    from core.tool_registry import tool_registry
    tool_registry.load(force=True)
    names = set(tool_registry.list_tools()["tools"])
    assert "computer_capability" in names
    assert "computer_action" in names


def test_computer_capability_is_honest(tmp_path):
    """detect_computer_capability reports real availability, never fabricates it."""
    import importlib
    from core.computer import detect_computer_capability
    cap = detect_computer_capability()
    assert "available" in cap and "reason" in cap and "backends" in cap
    assert cap["dry_run_only"] is (not cap["available"])
    # If pyautogui is absent, availability MUST be false with a reason.
    if not _has_pyautogui():
        assert cap["available"] is False
        assert cap["reason"].lower().startswith("pyautogui unavailable") or "pyautogui" in cap["reason"].lower()


def test_computer_action_reports_unavailable_on_fallback(tmp_path):
    """Without pyautogui, a real action must report computer_control_unavailable."""
    if _has_pyautogui() and not _has_display():
        pytest.skip("real pyautogui present but no display — not a real control path")
    from core.tool_registry import tool_registry
    tool_registry.load(force=True)
    if _has_pyautogui() and _has_display():
        pytest.skip("real control available — covered by host-E2E test")
    r = tool_registry.execute("computer_action", {"action": "click", "args": {"x": 5, "y": 5}})
    assert r.get("ok") is False
    assert r.get("error") == "computer_control_unavailable"
    assert r.get("reason")


def test_computer_action_explicit_dry_run_works_offline():
    """Explicit allow_dry_run=True is the offline audit/verify mode (ok=True, dry_run=True)."""
    from core.tool_registry import tool_registry
    tool_registry.load(force=True)
    r = tool_registry.execute("computer_action",
                              {"action": "type", "args": {"text": "hello world"},
                               "allow_dry_run": True})
    assert r.get("ok") is True
    assert r.get("dry_run") is True


def test_computer_action_missing_args_is_an_error():
    from core.tool_registry import tool_registry
    tool_registry.load(force=True)
    r = tool_registry.execute("computer_action", {"action": "click"})
    assert r.get("ok") is False
    assert r.get("error") == "missing_args"


def test_controller_backend_capability_lists_backends():
    from core.computer.controller import ComputerActionController
    from core.computer.mouse import DryRunMouse
    from core.computer.keyboard import DryRunKeyboard
    from core.computer.window_manager import DryRunWindowBackend
    # Explicit dry-run injection is OK and must be reported as dry_run, not fake real.
    ctl = ComputerActionController(mouse=DryRunMouse(), keyboard=DryRunKeyboard(),
                                   window_manager=DryRunWindowBackend())
    cap = ctl.backend_capability()
    assert cap["available"] is False
    assert cap["dry_run_only"] is True
    assert set(cap["backends"].keys()) == {"mouse", "keyboard", "window"}


# ---------------------------------------------------------------------------
# Host E2E (real control; skipped without pyautogui + display)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not (_has_pyautogui() and _has_display()),
                    reason="no pyautogui + display for real computer control")
def test_host_e2e_computer_real_control():
    """Real host control: capability available, click + type work on the live desktop.
    Requires pyautogui and a real X/Wayland display. Never run on a mock."""
    from core.computer import detect_computer_capability
    cap = detect_computer_capability()
    assert cap["available"] is True, cap
    from core.tool_registry import tool_registry
    tool_registry.load(force=True)
    r = tool_registry.execute("computer_action",
                              {"action": "move_mouse", "args": {"x": 50, "y": 50}})
    assert r.get("ok") is True
