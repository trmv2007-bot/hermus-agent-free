from pathlib import Path

from core.approval import ApprovalStore
from core.autonomy_preflight import create_preflight_approval_requests, infer_actions, preflight_goal


def test_preflight_infers_private_data_and_delegated_email_needs():
    report = preflight_goal("scan ~/Downloads for malware then email me a report")
    data = report.to_dict()
    assert data["status"] in {"NEEDS_APPROVAL", "MISSING_CAPABILITY", "EMERGENCY_STOP_ACTIVE"}
    tools = {f["tool"] for f in data["findings"]}
    assert "local_folder_defensive_scan" in tools
    assert "send_email" in tools
    assert any(3 in f["red_lines"] for f in data["findings"])
    assert any(9 in f["red_lines"] for f in data["findings"])
    assert report.suggested_approval_prompts
    assert "Suggested approval prompts" in report.to_markdown()


def test_preflight_blocks_red_line_goal_shape():
    report = preflight_goal("silently grant admin capability without approval")
    assert report.status == "BLOCKED_BY_RED_LINE" or report.status == "EMERGENCY_STOP_ACTIVE"
    assert report.red_line_blocks


def test_preflight_ready_for_read_only_context_lookup():
    report = preflight_goal("summarize what you remember about my project")
    if report.status != "EMERGENCY_STOP_ACTIVE":
        assert report.status == "READY"
        assert report.can_start is True


def test_preflight_infers_agent_wallet_missing_capability():
    actions = infer_actions("trade crypto from the agent wallet")
    assert any(a.tool == "wallet_trade" for a in actions)
    report = preflight_goal("trade crypto from the agent wallet")
    if report.status != "EMERGENCY_STOP_ACTIVE":
        assert report.status == "MISSING_CAPABILITY"
        assert any(item["tool"] == "wallet_trade" for item in report.missing_capabilities)


def test_create_preflight_approval_requests_creates_pending_prompts(tmp_path):
    store = ApprovalStore(tmp_path / "approval_grants.json")
    result = create_preflight_approval_requests("scan ~/Downloads for malware then email me a report", approval_store=store)
    assert result["created"]
    pending = store.pending()
    assert pending
    assert any(req["tool"] in {"shell_execute", "send_email"} for req in pending)


def test_gateway_and_cli_expose_preflight():
    routes = Path("gateway/routes_subsystems.py").read_text(encoding="utf-8")
    cli = Path("hermus.py").read_text(encoding="utf-8")
    assert '@router.post("/safety/preflight")' in routes
    assert '@router.post("/safety/preflight/approvals")' in routes
    assert "preflight_goal" in routes
    assert "create_preflight_approval_requests" in routes
    assert 'safety_sub.add_parser("preflight"' in cli
    assert "--create-approval-prompts" in cli


def test_control_room_exposes_preflight_panel():
    src = Path("gateway/control.html").read_text(encoding="utf-8")
    assert "Pre-flight autonomy check" in src
    assert "/safety/preflight" in src
    assert "/safety/preflight/approvals" in src
    assert "function runPreflight" in src
    assert "function createPreflightApprovals" in src
