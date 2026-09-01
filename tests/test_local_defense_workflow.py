from pathlib import Path


def test_local_defense_workflow_defines_gated_mission_lifecycle():
    src = Path("core/local_defense_workflow.py").read_text(encoding="utf-8")
    assert "start_local_scan_mission" in src
    assert "run_local_scan_mission" in src
    assert "MissionState.BLOCKED" in src
    assert "local_folder_defensive_scan" in src
    assert "save_report=True" in src
    assert "MISSION BLOCKED: scoped local-folder scan approval required" in src


def test_gateway_exposes_local_defense_mission_routes_and_bundle_resume_bridge():
    src = Path("gateway/routes_subsystems.py").read_text(encoding="utf-8")
    assert '@router.get("/local-defense/reports")' in src
    assert '@router.get("/local-defense/reports/{name}")' in src
    assert '@router.post("/local-defense/missions")' in src
    assert '@router.post("/local-defense/missions/{mission_id}/run")' in src
    assert "run_local_scan_mission" in src
    assert 'str(report.domain) == "local_defense"' in src


def test_cli_exposes_local_defense_scan_mission_commands():
    src = Path("hermus.py").read_text(encoding="utf-8")
    assert 'safety_sub.add_parser("scan-mission"' in src
    assert 'safety_sub.add_parser("scan-mission-run"' in src
    assert "start_local_scan_mission" in src
    assert "run_local_scan_mission" in src


def test_control_room_exposes_local_scan_mission_button():
    src = Path("gateway/control.html").read_text(encoding="utf-8")
    assert "Start Downloads scan mission" in src
    assert "/local-defense/missions" in src
    assert "function startDownloadsScanMission" in src
