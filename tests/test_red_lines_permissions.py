from __future__ import annotations

from core.approval import ApprovalGrant, ApprovalStore
from core.emergency_stop import EmergencyStop
from core.permissions import Decision, PermissionManager


def test_permission_manager_attaches_red_line_safety_metadata(tmp_path):
    pm = PermissionManager(overrides_path=tmp_path / "permissions.json")
    info = pm.classify("read_file", {"path": "README.md"})
    assert info["default"] == Decision.ALLOW.value
    assert info["safety"]["zone"] == "green"


def test_permission_manager_escalates_yellow_red_line_to_ask(tmp_path):
    pm = PermissionManager(overrides_path=tmp_path / "permissions.json")
    info = pm.classify("read_file", {"path": "~/Documents"})
    assert info["default"] == Decision.ASK.value
    assert info["safety"]["zone"] == "yellow"
    assert 3 in info["safety"]["red_lines"]


def test_permission_manager_denies_red_line_action(tmp_path):
    pm = PermissionManager(overrides_path=tmp_path / "permissions.json")
    info = pm.classify("shell_execute", {"command": "deploy malware against real system"})
    assert info["default"] == Decision.DENY.value
    assert info["safety"]["zone"] == "red"
    assert 4 in info["safety"]["red_lines"]


def test_permission_manager_protects_safety_policy_files(tmp_path):
    pm = PermissionManager(overrides_path=tmp_path / "permissions.json")
    info = pm.classify("write_file", {"path": "RED_LINES.md", "content": "update"})
    assert info["default"] == Decision.DENY.value
    assert info["immutable"] is True
    assert info["safety"]["zone"] == "yellow"
    assert 7 in info["safety"]["red_lines"]


def test_approval_store_creates_and_resolves_pending_request(tmp_path):
    store = ApprovalStore(tmp_path / "approval_grants.json")
    safety = {"zone": "yellow", "red_lines": [3], "reasons": ["private scope"]}
    created = store.create_request("shell_execute", {"command": "scan ~/Downloads for malware"}, safety)
    assert created["success"] is True
    request_id = created["request"]["id"]
    assert store.pending()[0]["id"] == request_id
    resolved = store.resolve_request(request_id, "approve", ttl_minutes=30, max_uses=1)
    assert resolved["success"] is True
    assert resolved["request"]["status"] == "approved"
    assert resolved["grant"]["tool"] == "shell_execute"
    assert store.pending() == []


def test_approval_store_matches_scoped_yellow_action(tmp_path):
    store = ApprovalStore(tmp_path / "approval_grants.json")
    grant = ApprovalGrant.create(
        "Scan Downloads for malware",
        tool="shell_execute",
        red_lines=[3],
        resources=["~/Downloads"],
        purpose="malware",
        max_uses=1,
    )
    store.add(grant)
    safety = {"zone": "yellow", "red_lines": [3]}
    match = store.allowed(
        "shell_execute",
        {"command": "scan ~/Downloads for malware-like files", "purpose": "malware"},
        safety,
        consume=True,
    )
    assert match and match["grant"]["id"] == grant.id
    assert store.allowed("shell_execute", {"command": "scan ~/Downloads for malware-like files"}, safety) is None


def test_permission_manager_creates_pending_request_for_ungranted_yellow(tmp_path):
    pm = PermissionManager(overrides_path=tmp_path / "permissions.json", approvals_path=tmp_path / "approval_grants.json")
    info = pm.check("shell_execute", args={"command": "scan ~/Downloads for malware-like files", "purpose": "malware"})
    assert info["decision"] == Decision.ASK.value
    assert info["approval_request"]["status"] == "pending"
    assert info["approval_request"]["tool"] == "shell_execute"
    assert pm.approval_pending()[0]["id"] == info["approval_request"]["id"]


def test_permission_manager_uses_scoped_approval_to_allow_yellow(tmp_path):
    pm = PermissionManager(overrides_path=tmp_path / "permissions.json", approvals_path=tmp_path / "approval_grants.json")
    pm.approval_grant(
        "Allow Downloads malware scan",
        tool="shell_execute",
        red_lines=[3],
        resources=["~/Downloads"],
        purpose="malware",
        max_uses=2,
    )
    info = pm.check("shell_execute", args={"command": "scan ~/Downloads for malware-like files", "purpose": "malware"})
    assert info["decision"] == Decision.ALLOW.value
    assert info["approval"]["matched"] is True
    assert info["safety"]["zone"] == "yellow"


def test_approval_grant_cannot_allow_red_action(tmp_path):
    pm = PermissionManager(overrides_path=tmp_path / "permissions.json", approvals_path=tmp_path / "approval_grants.json")
    pm.approval_grant(
        "Bad grant should not bypass red",
        tool="shell_execute",
        red_lines=[4],
        resources=["real system"],
        purpose="malware",
    )
    info = pm.check("shell_execute", args={"command": "deploy malware against real system"})
    assert info["decision"] == Decision.DENY.value
    assert "approval" not in info


def test_emergency_stop_denies_risky_action_even_with_grant(tmp_path, monkeypatch):
    brake = EmergencyStop(tmp_path / "emergency_stop.json")
    brake.activate("test brake", set_by="test")
    monkeypatch.setattr("core.emergency_stop._emergency_stop", brake)
    pm = PermissionManager(overrides_path=tmp_path / "permissions.json", approvals_path=tmp_path / "approval_grants.json")
    pm.approval_grant(
        "Allow Downloads malware scan",
        tool="shell_execute",
        red_lines=[3],
        resources=["~/Downloads"],
        purpose="malware",
    )
    info = pm.check("shell_execute", args={"command": "scan ~/Downloads for malware-like files", "purpose": "malware"})
    assert info["decision"] == Decision.DENY.value
    assert info["emergency_stop"]["active"] is True


def test_emergency_stop_allows_read_status_actions(tmp_path, monkeypatch):
    brake = EmergencyStop(tmp_path / "emergency_stop.json")
    brake.activate("test brake", set_by="test")
    monkeypatch.setattr("core.emergency_stop._emergency_stop", brake)
    pm = PermissionManager(overrides_path=tmp_path / "permissions.json", approvals_path=tmp_path / "approval_grants.json")
    info = pm.check("read_file", args={"path": "README.md"})
    assert info["decision"] == Decision.ALLOW.value
