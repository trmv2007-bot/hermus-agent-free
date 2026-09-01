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
