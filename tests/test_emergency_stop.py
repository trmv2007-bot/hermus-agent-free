from __future__ import annotations

from core.emergency_stop import EmergencyStop


def test_emergency_stop_state_persists(tmp_path):
    brake = EmergencyStop(tmp_path / "emergency_stop.json")
    assert brake.state().active is False
    activated = brake.activate("test stop", set_by="test")
    assert activated["state"]["active"] is True
    assert EmergencyStop(tmp_path / "emergency_stop.json").state().active is True
    cleared = brake.clear("test resume", set_by="test")
    assert cleared["state"]["active"] is False


def test_unreadable_emergency_stop_fails_active(tmp_path):
    path = tmp_path / "emergency_stop.json"
    path.write_text("not json", encoding="utf-8")
    state = EmergencyStop(path).state()
    assert state.active is True
    assert "unreadable" in state.reason


def test_global_stop_halts_the_computer_controller(tmp_path, monkeypatch):
    """The big red lever must stop desktop control, not just the workspace loop.

    Regression: `POST /emergency/stop` and `hermes stop` write the workspace
    brake; the computer controller only ever read its own separate latch, so
    after activating the global brake the controller still answered
    allowed=True and kept clicking.
    """
    import core.computer.permissions as computer_permissions
    from core.computer.controller import ComputerActionController
    from core.emergency_stop import EmergencyStop as GlobalStop

    global_brake = GlobalStop(tmp_path / "emergency_stop.json")
    latch = computer_permissions.EmergencyStop(str(tmp_path / "computer-stop.json"))
    # Point the computer layer at the same global brake the CLI/CLI routes write.
    monkeypatch.setattr(
        "core.computer.permissions.EmergencyStop._global_halt_state",
        lambda self: (bool(global_brake.state().active), global_brake.state().reason or None),
    )

    controller = ComputerActionController(emergency=latch)

    gate = controller._gate("click", {"x": 10, "y": 10})
    assert gate.get("allowed") is True, f"precondition failed, gate={gate}"

    global_brake.activate("test: controller must halt", set_by="test")
    try:
        halted = controller._gate("click", {"x": 10, "y": 10})
        assert halted.get("allowed") is False, (
            f"global emergency stop did NOT halt the computer controller: {halted}"
        )
        assert halted.get("decision") == "deny"
    finally:
        global_brake.clear("test done", set_by="test")

    assert controller._gate("click", {"x": 10, "y": 10}).get("allowed") is True, (
        "clearing the global brake must let the computer layer run again"
    )


def test_releasing_the_computer_latch_does_not_release_the_global_brake(tmp_path, monkeypatch):
    """Two brakes, one pull each: they must not alias into a single switch."""
    import core.computer.permissions as computer_permissions
    from core.emergency_stop import EmergencyStop as GlobalStop

    global_brake = GlobalStop(tmp_path / "emergency_stop.json")
    monkeypatch.setattr(
        "core.computer.permissions.EmergencyStop._global_halt_state",
        lambda self: (bool(global_brake.state().active), global_brake.state().reason or None),
    )
    latch = computer_permissions.EmergencyStop(str(tmp_path / "computer-stop.json"))

    global_brake.activate("global", set_by="test")
    try:
        latch.release()
        assert latch.halted is True, "releasing the computer latch cleared the global brake too"
    finally:
        global_brake.clear("done", set_by="test")


def test_multiplexed_computer_action_resolves_to_its_action_policy():
    """The only registered desktop tool must be judged by the desktop rules.

    Regression: the policy table defines computer_click / computer_type_text /
    computer_press_key as gui+deny, but the registry only ever exposes the
    dispatcher `computer_action`, and `key in tool_name` never matched. Every
    real click therefore classified as read/ask while the deny-line read as a
    safety guarantee in the source.
    """
    from core.permissions import PermissionManager, expanded_policy_name

    m = PermissionManager()
    assert expanded_policy_name("computer_action", {"action": "click"}) == "computer_click"

    for action in ("click", "double_click", "type_text", "press_key", "hotkey",
                   "scroll", "move_mouse", "open_application", "close_application"):
        info = m.classify("computer_action", {"action": action})
        assert info["risk"] == "gui", f"{action} classified as {info['risk']}, expected gui"
        assert info["default"] == "deny", f"{action} defaulted to {info['default']}, expected deny"
        assert "gui" in info["capabilities"]

    # Read-only observation stays read-only - a deny-everything screen is not a screen.
    read_only = m.classify("computer_action", {"action": "find_on_screen"})
    assert read_only["risk"] == "read"
    assert read_only["default"] == "allow"

    # Unknown / missing actions must not be granted by accident.
    assert expanded_policy_name("computer_action", {}) == ""
    assert m.classify("computer_action", {})["risk"] in ("read", "gui")


def test_expanded_policy_name_normalises_action_spellings():
    from core.permissions import expanded_policy_name

    assert expanded_policy_name("computer_action", {"action": "TYPE-TEXT"}) == "computer_type_text"
    assert expanded_policy_name("computer_action", {"action": "Type Text"}) == "computer_type_text"
    assert expanded_policy_name("computer_action", {"op": "click"}) == "computer_click"
    assert expanded_policy_name("some_other_tool", {"action": "click"}) == ""
