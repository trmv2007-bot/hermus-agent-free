from pathlib import Path

from core.contracts import CommandStatus, EventEnvelope, EventType
from core.events import configure_bus, get_bus
from core.safety_report import generate_safety_report, is_safety_event, write_safety_report


def test_safety_event_filter_accepts_control_plane_events():
    assert is_safety_event({"command": "permission.approval.requested"})
    assert is_safety_event({"command": "capability.discovered"})
    assert is_safety_event({"command": "emergency.stop.activated"})
    assert is_safety_event({"error_code": "APPROVAL_REQUIRED"})
    assert not is_safety_event({"command": "ordinary.chat"})


def test_safety_report_markdown_contains_core_sections(tmp_path):
    configure_bus(reset=True)
    get_bus().publish(EventEnvelope(
        type=EventType.STATE_CHANGED.value,
        command="permission.approval.requested",
        args_redacted={"id": "approval_test", "tool": "shell_execute"},
        status=CommandStatus.PENDING.value,
    ))
    report = generate_safety_report()
    md = report.to_markdown()
    assert "Hermus Autonomy Safety Report" in md
    assert "Pending approvals" in md
    assert "Active scoped grants" in md
    assert "Blocked missions" in md
    assert "Capability discoveries" in md
    assert "Local defense reports" in md
    assert "Recent safety events" in md
    assert "permission.approval.requested" in md


def test_safety_report_can_be_written(tmp_path):
    configure_bus(reset=True)
    path = tmp_path / "report.md"
    result = write_safety_report(generate_safety_report(), output=path)
    assert result["success"] is True
    assert path.exists()
    assert "Autonomy Safety Report" in path.read_text(encoding="utf-8")


def test_gateway_exposes_safety_report_endpoint():
    src = Path("gateway/routes_subsystems.py").read_text(encoding="utf-8")
    assert '@router.get("/safety/report")' in src
    assert "generate_safety_report" in src
    assert "write_safety_report" in src


def test_cli_exposes_safety_report_command():
    src = Path("hermus.py").read_text(encoding="utf-8")
    assert 'subparsers.add_parser("safety"' in src
    assert 'safety_report_p = safety_sub.add_parser("report"' in src
    assert "write_safety_report" in src


def test_control_room_can_generate_safety_report():
    src = Path("gateway/control.html").read_text(encoding="utf-8")
    assert "Generate safety report" in src
    assert "/safety/report?format=markdown" in src
    assert "refreshSafetyReport" in src
