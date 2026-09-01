from pathlib import Path


def test_mission_report_serializes_preflight_metadata_statically():
    src = Path("core/mission.py").read_text(encoding="utf-8")
    assert "preflight: Optional[dict[str, Any]] = None" in src
    assert "create_prompts_action: Optional[dict[str, Any]] = None" in src
    assert '"preflight": self.preflight' in src
    assert '"create_prompts_action": self.create_prompts_action' in src
    assert "preflight=data.get(\"preflight\")" in src
    assert "create_prompts_action=data.get(\"create_prompts_action\")" in src
    assert src.count('"preflight": self.preflight') >= 2


def test_mission_engine_start_has_preflight_gate():
    src = Path("core/mission.py").read_text(encoding="utf-8")
    assert "preflight: bool = True" in src
    assert "allow_preflight_planning: bool = False" in src
    assert "preflight_goal(goal)" in src
    assert "BLOCKED_BY_RED_LINE" in src and "EMERGENCY_STOP_ACTIVE" in src
    assert "_preflight_blocked_report" in src
    assert "persist=bool(allow_preflight_planning)" in src
    assert "persist=False" in src
    assert "Create approval prompts for this blocked mission" in src
    assert '"endpoint": f"/missions/{mission_id}/preflight/approvals"' in src


def test_gateway_mission_start_accepts_preflight_controls():
    src = Path("gateway/realtime.py").read_text(encoding="utf-8")
    assert "preflight=str(payload.get(\"preflight\", True)).lower()" in src
    assert "allow_preflight_planning=str(payload.get(\"allow_preflight_planning\", False)).lower()" in src
    assert '@router.post("/missions/{mission_id}/preflight/approvals")' in src
    assert "create_preflight_approval_requests" in src


def test_cli_mission_start_exposes_preflight_controls():
    src = Path("hermus.py").read_text(encoding="utf-8")
    assert "--skip-preflight" in src
    assert "--allow-planning-blocked" in src
    assert "preflight=not args.skip_preflight" in src


def test_control_room_mission_launcher_uses_preflight():
    src = Path("gateway/control.html").read_text(encoding="utf-8")
    assert "Pre-flight mission" in src
    assert "Start mission if ready" in src
    assert "Record planning-mode blocker" in src
    assert "create prompts" in src
    assert "function missionPreflight" in src
    assert "function startMission" in src
    assert "function createMissionPrompts" in src
    assert "allow_preflight_planning" in src
