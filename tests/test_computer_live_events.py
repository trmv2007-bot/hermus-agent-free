"""Live computer execution telemetry tests."""
from pathlib import Path

from core.computer.events import ComputerEventBus
from core.computer.state_machine import VisualState, VisualStateMachine


def test_event_bus_accepts_live_contract_and_journals(tmp_path: Path):
    bus = ComputerEventBus(str(tmp_path / "events.jsonl"))
    expected = [
        "task_started", "plan_created", "state_changed", "screen_event",
        "action_started", "action_completed", "verification_started",
        "verification_completed", "repair_started", "repair_completed",
        "skill_recalled", "checkpoint_saved", "task_completed", "task_failed",
        "emergency_stop",
    ]
    for event_type in expected:
        assert bus.publish(event_type, {"task_id": "live-test"})["type"] == event_type
    assert [event["type"] for event in bus.read_journal()] == expected


def test_state_machine_emits_lifecycle_before_durable_result():
    telemetry = []
    durable = []
    machine = VisualStateMachine(
        recorder=None,
        execute=lambda _spec: {"ok": True, "action": "click", "ts": "now"},
        verify=lambda _before, _after, _expected: {"ok": True, "confidence": 0.99},
        on_telemetry=lambda event_type, data: telemetry.append((event_type, data)),
        on_event=durable.append,
    )
    report = machine.run([
        VisualState("INSTALLING", action={"kind": "click_target", "target": "Install"},
                    expected="installation started", on_success="SUCCESS"),
        VisualState("SUCCESS", terminal=True),
    ])

    assert report["success"] is True
    types = [event_type for event_type, _data in telemetry]
    assert types == [
        "screen_event", "action_started", "action_completed", "screen_event",
        "verification_started", "verification_completed",
    ]
    assert telemetry[1][1]["state"] == "INSTALLING"
    assert telemetry[4][1]["expected"] == "installation started"
    assert any(event.get("phase") == "original_action" for event in durable)
