from pathlib import Path


def test_safety_events_endpoint_filters_canonical_audit_events():
    src = Path("gateway/routes_subsystems.py").read_text(encoding="utf-8")
    core = Path("core/safety_report.py").read_text(encoding="utf-8")
    assert '@router.get("/safety/events")' in src
    assert "get_bus().recent" in src
    assert "is_safety_event" in src
    assert "permission." in core and "capability." in core and "emergency.stop" in core
    assert "APPROVAL_REQUIRED" in core and "POLICY_DENIED" in core


def test_control_room_renders_safety_event_timeline():
    src = Path("gateway/control.html").read_text(encoding="utf-8")
    assert "Safety Event Timeline" in src
    assert "#safetyEvents" in src
    assert "/safety/events?limit=80" in src
    assert "refreshSafetyEvents" in src


def test_safety_core_header_is_live_projection_not_static_copy():
    src = Path("gateway/control.html").read_text(encoding="utf-8")
    assert "pendingCount" in src
    assert "blockedMissionCount" in src
    assert "updateSafetyCore" in src
    assert "GET /permissions/pending" not in src  # JS uses relative fetch through getJSON below, not hardcoded copy text.
    assert "setPill(\"#pendingCount\"" in src
    assert "setPill(\"#blockedMissionCount\"" in src
