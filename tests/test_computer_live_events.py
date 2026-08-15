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


def test_tail_reads_only_new_events_from_cursor(tmp_path: Path):
    """Incremental tailing: a live stream must not re-read the whole journal."""
    bus = ComputerEventBus(str(tmp_path / "events.jsonl"))
    bus.publish("task_started", {"task_id": "t1"})
    events, cursor = bus.tail(0)
    assert [event["type"] for event in events] == ["task_started"]

    # Nothing new -> no events, cursor unchanged (this is the idle hot path).
    assert bus.tail(cursor) == ([], cursor)

    bus.publish("action_started", {"task_id": "t1"})
    events, cursor2 = bus.tail(cursor)
    assert [event["type"] for event in events] == ["action_started"]
    assert cursor2 > cursor
    assert bus.tail(cursor2) == ([], cursor2)


def test_tail_survives_rotation_and_partial_lines(tmp_path: Path):
    """A stale cursor after rotation must resync instead of losing the stream."""
    bus = ComputerEventBus(str(tmp_path / "events.jsonl"))
    for _ in range(5):
        bus.publish("screen_event", {"task_id": "t1"})
    _events, cursor = bus.tail(0)

    # Simulate rotation: the file shrinks, so the old cursor is past the end.
    bus.journal_path.write_text("", encoding="utf-8")
    bus.publish("task_completed", {"task_id": "t1"})
    events, _cursor = bus.tail(cursor)
    assert [event["type"] for event in events] == ["task_completed"]

    # A half-written trailing line is not consumed until it is complete.
    with bus.journal_path.open("a", encoding="utf-8") as handle:
        handle.write('{"id":"x","type":"action_started","ts":"now","data":{}')
    events, cursor_partial = bus.tail(_cursor)
    assert events == []
    with bus.journal_path.open("a", encoding="utf-8") as handle:
        handle.write("}\n")
    events, _ = bus.tail(cursor_partial)
    assert [event["type"] for event in events] == ["action_started"]
