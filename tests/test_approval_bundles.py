from pathlib import Path

from core.approval import ApprovalStore
from core.autonomy_preflight import create_preflight_approval_requests


def test_approval_store_creates_and_resolves_bundle(tmp_path):
    store = ApprovalStore(tmp_path / "approval_grants.json")
    r1 = store.create_request("shell_execute", {"command": "scan downloads folder ~/Downloads for malware"}, {"zone": "yellow", "red_lines": [3], "reasons": ["private data"]})["request"]
    r2 = store.create_request("send_email", {"action": "send email"}, {"zone": "yellow", "red_lines": [9], "reasons": ["delegated communication"]})["request"]
    bundle = store.create_bundle("Approval plan for mission msn_test", [r1["id"], r2["id"]], mission_id="msn_test", goal="scan and email")
    assert bundle["success"] is True
    assert store.bundles()[0]["mission_id"] == "msn_test"
    resolved = store.resolve_bundle(bundle["bundle"]["id"], "approve", ttl_minutes=30, max_uses=1)
    assert resolved["success"] is True
    assert len(store.list()) == 2
    assert not store.pending()


def test_preflight_can_bundle_draft_approval_prompts(tmp_path):
    store = ApprovalStore(tmp_path / "approval_grants.json")
    result = create_preflight_approval_requests(
        "scan ~/Downloads for malware then email me a report",
        approval_store=store,
        mission_id="msn_bundle",
        bundle=True,
    )
    assert result["created"]
    assert result["bundle"]
    assert result["bundle"]["mission_id"] == "msn_bundle"
    assert store.bundles()


def test_permission_manager_exposes_bundle_facade():
    src = Path("core/permissions.py").read_text(encoding="utf-8")
    assert "approval_bundles" in src
    assert "approval_bundle_resolve" in src
    assert "resolve_bundle" in src


def test_gateway_exposes_bundle_routes_and_mission_bridge():
    subsystems = Path("gateway/routes_subsystems.py").read_text(encoding="utf-8")
    realtime = Path("gateway/realtime.py").read_text(encoding="utf-8")
    assert '@router.get("/permissions/bundles")' in subsystems
    assert '@router.post("/permissions/bundles/resolve")' in subsystems
    assert "resume_mission" in subsystems
    assert '@router.post("/missions/{mission_id}/preflight/approvals")' in realtime
    assert "bundle=True" in realtime


def test_cli_and_dashboard_expose_bundle_flow():
    cli = Path("hermus.py").read_text(encoding="utf-8")
    dash = Path("gateway/control.html").read_text(encoding="utf-8")
    assert 'add_parser("bundles"' in cli
    assert 'add_parser("resolve-bundle"' in cli
    assert "Approval bundles" in dash
    assert "/permissions/bundles/resolve" in dash
    assert "function resolveBundle" in dash
