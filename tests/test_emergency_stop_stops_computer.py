"""The emergency stop stops — mid-task, and the journal records the abort.

Two brakes exist and both must halt computer control:

* the computer-control halt latch (``core.computer.permissions.emergency_stop``)
* the workspace-wide Red Line 1 brake (``core.emergency_stop``), which is what
  ``/emergency/stop`` and the control-room red button set

Before this, the second one stopped the rest of the product but not the mouse.
"""

from __future__ import annotations

import pytest

from core.computer.controller import ComputerActionController
from core.computer.keyboard import DryRunKeyboard
from core.computer.mouse import DryRunMouse
from core.computer.permissions import EmergencyStop as ComputerHalt
from core.computer.window_manager import DryRunWindowBackend
from core.emergency_stop import EmergencyStop as GlobalBrake
from core.undo import UndoJournal


@pytest.fixture
def journal(tmp_path):
    return UndoJournal(tmp_path / "journal")


@pytest.fixture
def controller(tmp_path, journal):
    return ComputerActionController(
        mouse=DryRunMouse(),
        keyboard=DryRunKeyboard(),
        window_manager=DryRunWindowBackend(),
        emergency=ComputerHalt(halt_path=str(tmp_path / "halt.json")),
        journal=journal,
    )


def test_global_brake_stops_a_multi_step_action_midway(controller, journal, tmp_path, monkeypatch):
    brake = GlobalBrake(tmp_path / "emergency_stop.json")
    monkeypatch.setattr("core.emergency_stop._emergency_stop", brake)

    first = controller.move_mouse(10, 10)
    second = controller.click(10, 10)
    assert first["ok"] is True and second["ok"] is True
    calls_before = len(controller.mouse.calls)

    # The person hits the brake.
    brake.activate("test brake", set_by="test")

    third = controller.type_text("must not be typed")
    fourth = controller.click(20, 20)

    assert third["ok"] is False
    assert fourth["ok"] is False
    assert "emergency stop" in third["error"]
    # Nothing further reached the backend: the two refused steps emitted nothing.
    assert len(controller.mouse.calls) == calls_before
    assert all(record.get("dry_run") for record in controller.mouse.calls)

    # The journal records the abort rather than pretending an action happened.
    aborted = [entry for entry in journal.list(10) if entry["state"] == "aborted"]
    assert len(aborted) == 2
    assert all("emergency stop" in entry["error"] for entry in aborted)
    aborted_ids = {entry["id"] for entry in aborted}
    assert {third.get("undo_id"), fourth.get("undo_id")} == aborted_ids
    assert journal.get(third["undo_id"]).state == "aborted"


def test_computer_halt_latch_stops_the_loop(controller, journal):
    assert controller.move_mouse(1, 1)["ok"] is True
    controller.emergency.halt("person pressed stop")
    refused = controller.click(2, 2)

    assert refused["ok"] is False
    assert "person pressed stop" in refused["error"]
    assert len(controller.mouse.calls) == 1
    aborted = [entry for entry in journal.list(10) if entry["state"] == "aborted"]
    assert aborted


def test_releasing_the_global_brake_lets_control_resume(controller, tmp_path, monkeypatch):
    brake = GlobalBrake(tmp_path / "emergency_stop.json")
    monkeypatch.setattr("core.emergency_stop._emergency_stop", brake)
    brake.activate("test brake")
    assert controller.move_mouse(1, 1)["ok"] is False

    brake.clear("released")
    assert controller.move_mouse(1, 1)["ok"] is True


def test_unreadable_brake_state_halts_control(controller, tmp_path, monkeypatch):
    brake = GlobalBrake(tmp_path / "emergency_stop.json")
    (tmp_path / "emergency_stop.json").write_text("not json", encoding="utf-8")
    monkeypatch.setattr("core.emergency_stop._emergency_stop", brake)

    assert controller.move_mouse(1, 1)["ok"] is False
