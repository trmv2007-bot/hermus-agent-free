from pathlib import Path

from core.approval import ApprovalStore
from core.capability_registry import CapabilityRegistry


def test_capability_registry_setup_plan_records_proposed_state(tmp_path):
    reg = CapabilityRegistry(tmp_path / "registry.json")
    result = reg.setup_plan("Gmail delegated send", write_proposal=False)
    assert result["success"] is True
    record = result["record"]
    assert record["status"] == "proposed"
    assert record["planning_command"]
    assert "Gmail delegated send" in record["name"]


def test_capability_activation_requires_approved_request(tmp_path):
    store = ApprovalStore(tmp_path / "approval_grants.json")
    reg = CapabilityRegistry(tmp_path / "registry.json")
    reg.register("Calendar connector", status="configured")

    import sys
    from types import SimpleNamespace

    old = sys.modules.get("core.permissions")
    fake_permissions = SimpleNamespace(permission_manager=SimpleNamespace(approvals=store))
    sys.modules["core.permissions"] = fake_permissions
    try:
        req = reg.request_activation("Calendar connector", reason="ready for use")
        assert req["success"] is True
        denied = reg.activate("Calendar connector", approval_id=req["request"]["id"])
        assert denied["success"] is False
        store.resolve_request(req["request"]["id"], "approve", ttl_minutes=30, max_uses=1)
        activated = reg.activate("Calendar connector", approval_id=req["request"]["id"])
        assert activated["success"] is True
        assert activated["record"]["status"] == "active"
    finally:
        if old is None:
            sys.modules.pop("core.permissions", None)
        else:
            sys.modules["core.permissions"] = old


def test_gateway_exposes_capability_registry_routes():
    src = Path("gateway/routes_subsystems.py").read_text(encoding="utf-8")
    assert '@router.get("/capabilities/registry")' in src
    assert '@router.post("/capabilities/registry/setup")' in src
    assert '@router.post("/capabilities/registry/request-activation")' in src
    assert '@router.post("/capabilities/registry/activate")' in src


def test_cli_and_dashboard_expose_capability_activation_flow():
    cli = Path("hermus.py").read_text(encoding="utf-8")
    dash = Path("gateway/control.html").read_text(encoding="utf-8")
    assert 'add_parser("registry"' in cli
    assert 'add_parser("setup"' in cli
    assert 'add_parser("request-activation"' in cli
    assert 'add_parser("activate"' in cli
    assert "Capability readiness / activation registry" in dash
    assert "/capabilities/registry/setup" in dash
    assert "/capabilities/registry/request-activation" in dash
    assert "function setupCapability" in dash
