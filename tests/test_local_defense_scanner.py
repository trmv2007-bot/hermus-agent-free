from pathlib import Path

from core.local_defense_scanner import list_scan_reports, read_scan_report, save_scan_report, scan_folder


def test_local_folder_defensive_scan_reports_indicators_without_contents(tmp_path):
    sample = tmp_path / "Downloads"
    sample.mkdir()
    (sample / "setup.exe").write_bytes(b"MZfake")
    (sample / "run.ps1").write_text("powershell -EncodedCommand AAAABBBBCCCC", encoding="utf-8")
    (sample / "notes.txt").write_text("hello safe world", encoding="utf-8")
    result = scan_folder(str(sample), max_files=20)
    assert result["success"] is True
    assert result["finding_count"] >= 2
    joined = str(result["findings"])
    assert "setup.exe" in joined and "run.ps1" in joined
    assert "EncodedCommand AAAABBBBCCCC" not in joined
    assert "privacy" in result and "No file contents" in result["privacy"]


def test_local_folder_defensive_scan_can_save_markdown_report(tmp_path):
    sample = tmp_path / "Downloads"
    sample.mkdir()
    (sample / "run.ps1").write_text("powershell -EncodedCommand AAAA", encoding="utf-8")
    out_dir = tmp_path / "reports"
    result = scan_folder(str(sample), max_files=10, save_report=True, output_dir=str(out_dir), mission_id="msn_scan")
    assert result["report_saved"] is True
    assert result["report_path"]
    saved = Path(result["report_path"])
    assert saved.exists()
    assert "Local folder defensive scan" in saved.read_text(encoding="utf-8")
    assert result["report_artifact"]


def test_save_scan_report_returns_artifact_metadata_without_required_deps(tmp_path):
    result = {"markdown": "# report", "root": str(tmp_path), "finding_count": 0, "scanned_files": 0}
    saved = save_scan_report(result, mission_id="msn_manual", output_dir=str(tmp_path / "out"))
    assert saved["success"] is True
    assert saved["path"].endswith(".md")
    assert saved["artifact"]


def test_local_defense_report_reader_rejects_path_traversal(tmp_path):
    saved = save_scan_report({"markdown": "# Local folder defensive scan\n", "root": str(tmp_path)}, output_dir=str(tmp_path / "reports"))
    assert Path(saved["path"]).exists()
    assert read_scan_report("../../etc/passwd")["success"] is False


def test_tool_registry_discovers_local_defense_tool_statically():

    registry = Path("core/tool_registry.py").read_text(encoding="utf-8")
    tool = Path("tools/local_defense.py").read_text(encoding="utf-8")
    perms = Path("core/permissions.py").read_text(encoding="utf-8")
    assert '"tools.local_defense"' in registry
    assert "local_folder_defensive_scan" in tool
    assert "save_report" in tool and "mission_id" in tool
    assert '"local_folder_defensive_scan": (Risk.READ, Decision.ASK' in perms


def test_gateway_cli_and_dashboard_expose_local_defense_scan():
    routes = Path("gateway/routes_subsystems.py").read_text(encoding="utf-8")
    cli = Path("hermus.py").read_text(encoding="utf-8")
    dash = Path("gateway/control.html").read_text(encoding="utf-8")
    assert '@router.post("/local-defense/scan")' in routes
    assert "_permission_guard(\"local_folder_defensive_scan\"" in routes
    assert 'safety_sub.add_parser("scan-folder"' in cli
    assert "--save-report" in cli and "--mission-id" in cli
    assert "permission check failed closed" in cli
    assert "Run approved Downloads scan" in dash
    assert "local_folder_defensive_scan" in dash
    assert "save_report:true" in dash
    assert "/local-defense/scan" in dash


def test_preflight_uses_dedicated_local_defense_scanner():
    src = Path("core/autonomy_preflight.py").read_text(encoding="utf-8")
    assert 'add("local_folder_defensive_scan"' in src
    assert "Read-only defensive local folder scan" in src
