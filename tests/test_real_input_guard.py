"""A real-input guard: pyautogui cannot reach the host from a non-product process.

RIS-12 was a test that typed "hello world" into whichever window had focus on a
developer's desktop. The guard makes that impossible by default and makes the
refusal explicit instead of a silent no-op.
"""

from __future__ import annotations

import pytest

from core.computer import input_guard


@pytest.fixture(autouse=True)
def _clean_guard():
    input_guard.release_interactive()
    yield
    input_guard.release_interactive()


def test_default_backends_are_fake_with_an_explicit_reason():
    from core.computer.keyboard import default_keyboard
    from core.computer.mouse import default_mouse

    mouse = default_mouse()
    keyboard = default_keyboard()
    assert mouse.name == "dry_run"
    assert keyboard.name == "dry_run"
    assert "real input disabled" in (mouse.available()["fallback_reason"] or "")
    assert "real input disabled" in (keyboard.available()["fallback_reason"] or "")


def test_real_backend_raises_real_input_disabled_instead_of_typing():
    from core.computer.keyboard import PyAutoGUIKeyboard
    from core.computer.mouse import PyAutoGUIMouse

    with pytest.raises(input_guard.RealInputDisabledError) as caught:
        PyAutoGUIMouse().move(10, 10)
    assert caught.value.code == "real_input_disabled"

    with pytest.raises(input_guard.RealInputDisabledError):
        PyAutoGUIKeyboard().type_text("this must never reach the host")


def test_a_test_runner_never_counts_as_the_interactive_product():
    assert input_guard.declare_interactive("unit-test") is False
    assert input_guard.real_input_enabled() is False
    assert "test runner" in input_guard.disabled_reason()


def test_the_environment_flag_can_forbid_real_input(monkeypatch):
    monkeypatch.setenv(input_guard.ENV_FLAG, "off")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setitem(__import__("sys").modules, "pytest", pytest)
    assert input_guard.real_input_enabled() is False
    assert input_guard.ENV_FLAG in input_guard.disabled_reason()


def test_guard_state_is_reportable():
    state = input_guard.guard_state()
    assert state["real_input_enabled"] is False
    assert state["reason"]


def test_computer_action_dry_run_never_touches_the_host():
    """The RIS-12 regression pin: an offline dry run stays offline."""
    from core.tool_registry import tool_registry

    tool_registry.load(force=True)
    result = tool_registry.execute("computer_action", {"action": "type", "args": {"text": "hello world"}, "allow_dry_run": True})

    assert result.get("ok") is True
    assert result.get("dry_run") is True
    assert result.get("backend") == "dry_run"
    assert input_guard.real_input_enabled() is False


def test_computer_action_without_dry_run_reports_the_guard_reason():
    from core.tool_registry import tool_registry

    tool_registry.load(force=True)
    result = tool_registry.execute("computer_action", {"action": "click", "args": {"x": 5, "y": 5}})

    assert result.get("ok") is False
    assert result.get("error") == "computer_control_unavailable"
    assert "real input disabled" in result.get("reason", "")


def test_capability_report_carries_the_guard_state():
    from core.computer import detect_computer_capability

    cap = detect_computer_capability()
    assert cap["dry_run_only"] is True
    assert cap["input_guard"]["real_input_enabled"] is False
