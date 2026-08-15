"""Tests for Phase C & D — remote control, resources, plugins, delegation,
skill profiles and the gateway endpoints that expose them."""
from __future__ import annotations

import pytest

from core.computer import (
    ComputerActionController,
    ComputerSkillStore,
    DryRunKeyboard,
    DryRunMouse,
    DryRunWindowBackend,
    RemoteApprovalGate,
    RemoteControlHub,
    get_resource_monitor,
)
from core.computer.permissions import RiskLevel
from core.computer.remote import PromptState
from core.plugins import PluginRegistry


def _controller(approval=None):
    return ComputerActionController(
        mouse=DryRunMouse(),
        keyboard=DryRunKeyboard(),
        window_manager=DryRunWindowBackend(),
        approval=approval,
    )


# --------------------------------------------------------------------------
# C11 — Remote approval gate
# --------------------------------------------------------------------------

def test_approval_gate_disabled_by_default_allows():
    gate = RemoteApprovalGate()
    assert gate.enabled is False
    assert gate.check("click", {"x": 1, "y": 1}, risk="medium")["allowed"] is True
    assert gate.pending() == []


def test_approval_gate_queues_and_resolves():
    gate = RemoteApprovalGate()
    gate.set_enabled(True, required_risk=RiskLevel.MEDIUM)
    decision = gate.check("click", {"x": 5, "y": 5}, risk="medium", description="Click Install")
    assert decision["pending"] is True
    prompt_id = decision["prompt_id"]

    pending = gate.pending()
    assert len(pending) == 1 and pending[0]["prompt_id"] == prompt_id
    assert gate.status()["pending_count"] == 1

    res = gate.approve(prompt_id, by="test")
    assert res["decision"] == "approved"
    assert gate.pending() == []

    # resolving twice fails cleanly
    assert gate.approve(prompt_id)["success"] is False


def test_approval_gate_reject_and_risk_filtering():
    gate = RemoteApprovalGate()
    gate.set_enabled(True, required_risk=RiskLevel.MEDIUM)

    # LOW risk is not gated
    assert gate.check("move_mouse", {"x": 0, "y": 0}, risk="low")["allowed"] is True
    # HIGH is gated
    high = gate.check("sudo", {}, risk="high", description="sudo")
    assert high["pending"] is True
    res = gate.reject(high["prompt_id"], reason="not now", by="test")
    assert res["decision"] == "rejected"
    assert res["reason"] == "not now"
    assert gate.history()[-1]["state"] == PromptState.REJECTED.value


def test_controller_integration_blocks_until_approved():
    gate = RemoteApprovalGate()
    ctrl = _controller(approval=gate)
    # disabled -> allowed immediately
    assert ctrl.click(10, 10)["decision"] == "allow"

    gate.set_enabled(True)
    blocked = ctrl.click(10, 10)
    assert blocked["decision"] == "ask"
    assert blocked["ok"] is False
    assert blocked.get("approval_required") is True
    prompt_id = blocked.get("prompt_id")
    assert prompt_id and gate.pending()[0]["prompt_id"] == prompt_id

    # approve the prompt -> same action runs within the approval grace window
    gate.approve(prompt_id)
    assert ctrl.click(20, 20)["decision"] == "allow"


# --------------------------------------------------------------------------
# C11 — Remote control hub
# --------------------------------------------------------------------------

def test_remote_control_hub_lifecycle():
    from core.computer.task_control import get_task_control

    hub = RemoteControlHub()
    control = get_task_control()
    task_id = "remote-test-task"
    control.register_task(task_id, "Open Calculator")

    assert hub.pause(task_id)["success"] is True
    assert control.is_pause_requested(task_id)
    control.confirm_pause(task_id)
    assert control.is_task_paused(task_id)
    assert hub.resume(task_id)["success"] is True
    assert not control.is_task_paused(task_id)
    assert hub.cancel(task_id)["success"] is True

    # emergency stop + release via hub
    hub.emergency_stop("test stop")
    assert hub.emergency.halted
    hub.release()
    assert not hub.emergency.halted


def test_remote_control_hub_snapshot_shape():
    hub = RemoteControlHub()
    snap = hub.snapshot()
    assert "approval" in snap and "control" in snap and "emergency" in snap
    assert "recent_events" in snap and "ts" in snap


# --------------------------------------------------------------------------
# D13 — Resource monitor
# --------------------------------------------------------------------------

def test_resource_monitor_sample_shape():
    monitor = get_resource_monitor()
    sample = monitor.sample()
    for key in ("cpu_percent", "memory_bytes", "threads", "disk", "subsystems", "pid", "ts"):
        assert key in sample
    assert "event_bus" in sample["subsystems"]


# --------------------------------------------------------------------------
# D15 — Plugin ecosystem
# --------------------------------------------------------------------------

def test_plugin_registry_discovers_and_invokes():
    pr = PluginRegistry(search_dirs=["core/plugins"])
    result = pr.load_all()
    assert "example_plugin" in result["loaded"]

    tools = pr.tools()
    assert any(t["name"] == "example_safeguard_check" for t in tools)

    out = pr.invoke_tool("example_safeguard_check", action="sudo apt install")
    assert out["allowed"] is False and "sudo" in out["reason"]
    out2 = pr.invoke_tool("example_safeguard_check", action="click Install")
    assert out2["allowed"] is True

    with pytest.raises(Exception):
        pr.invoke_tool("does_not_exist")


def test_plugin_dispatch_event_isolated():
    # a broken handler must not break other handlers
    pr = PluginRegistry(search_dirs=["core/plugins"])
    pr.load_all()
    pr.dispatch_event("action_started", {"data": {"action": "click"}})  # should not raise


# --------------------------------------------------------------------------
# C9 — Skill profiles
# --------------------------------------------------------------------------

def test_skill_profile_summary(tmp_path):
    store = ComputerSkillStore(str(tmp_path / "skills"))
    store.save_skill("Install Firefox", [{"step": 1}, {"step": 2}], duration=39.8)
    # add a second successful run
    store.record_run("install-firefox", success=True, duration=39.8)

    profile = store.profile("install-firefox")
    assert profile is not None
    assert profile["runs"] == 2 and profile["successes"] == 2
    assert profile["success_percent"] == 100.0
    assert "2 runs / 2 success" in profile["summary"]
    assert profile["average_duration"] > 0

    assert store.profile("missing") is None


# --------------------------------------------------------------------------
# Gateway endpoints
# --------------------------------------------------------------------------

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from gateway.gateway import app

    with TestClient(app) as c:
        yield c


def test_gateway_phase_c_d_endpoints(client):
    assert client.get("/computer/dashboard").status_code == 200
    assert client.get("/remote").status_code == 200
    assert client.get("/computer/resources").status_code == 200
    assert client.get("/remote/status").json()["emergency"]["halted"] is False
    assert client.get("/remote/approvals").status_code == 200
    assert client.get("/computer/delegations").status_code == 200

    plugins = client.get("/plugins").json()
    assert plugins["plugins"]
    assert any(t["name"] == "example_safeguard_check" for t in plugins["tools"])

    invoke = client.post("/plugins/invoke", json={"tool": "example_safeguard_check", "args": {"action": "rm -rf /"}}).json()
    assert invoke["success"] is True and invoke["result"]["allowed"] is False


def test_gateway_remote_approval_flow(client):
    # enable the shared approval gate
    client.post("/remote/approval/enable", json={"enabled": True})
    try:
        assert client.get("/remote/approvals").json()["status"]["enabled"] is True
    finally:
        client.post("/remote/approval/enable", json={"enabled": False})
    assert client.get("/remote/approvals").json()["status"]["enabled"] is False


def test_gateway_delegation_dry_run(client):
    payload = {
        "task": "Find how to install X then install it",
        "plan": {
            "units": [
                {"unit_id": "unit-1", "role": "researcher", "task": "Research install steps"},
                {"unit_id": "unit-2", "role": "computer-operator", "task": "Install X",
                 "depends_on": ["unit-1"]},
            ]
        },
        "dry_run": True,
    }
    result = client.post("/computer/delegate", json=payload).json()
    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["plan"]["units"][1]["depends_on"] == ["unit-1"]


def test_gateway_skill_profile_endpoint_missing(client):
    # missing skill -> 404; existing profile is covered by the unit test above
    assert client.get("/computer/skills/definitely-not-a-skill/profile").status_code == 404
