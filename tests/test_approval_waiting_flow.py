from __future__ import annotations

from core.tools.gateway import ToolGateway


class AskRegistry:
    executors = {"shell_execute": lambda **kwargs: {"ok": True}}

    def execute(self, name, args):
        return {
            "error": "Permission requires confirmation (ASK) for tool 'shell_execute'",
            "permission": {
                "decision": "ask",
                "safety": {"zone": "yellow", "red_lines": [3]},
                "approval_request": {
                    "id": "approval_test",
                    "tool": name,
                    "status": "pending",
                    "safety": {"zone": "yellow", "red_lines": [3]},
                },
            },
        }

    def list_tools(self):
        return {"catalog": []}


class DenyRegistry:
    executors = {"shell_execute": lambda **kwargs: {"ok": True}}

    def execute(self, name, args):
        return {
            "error": "Permission DENIED for tool 'shell_execute'",
            "permission": {
                "decision": "deny",
                "safety": {"zone": "red", "red_lines": [4]},
            },
        }

    def list_tools(self):
        return {"catalog": []}


def test_tool_gateway_classifies_ask_as_approval_required():
    result = ToolGateway(registry=AskRegistry()).execute("shell_execute", {"command": "scan ~/Downloads"})
    assert result.ok is False
    assert result.status == "blocked"
    assert result.error_code == "APPROVAL_REQUIRED"
    assert result.retryable is True
    assert result.next_action == "wait_for_approval"
    assert result.data["approval_request"]["id"] == "approval_test"


def test_tool_gateway_keeps_deny_as_policy_denied():
    result = ToolGateway(registry=DenyRegistry()).execute("shell_execute", {"command": "deploy malware"})
    assert result.ok is False
    assert result.error_code == "POLICY_DENIED"
    assert result.retryable is False
    assert result.next_action != "wait_for_approval"


def test_agent_preserves_approval_required_metadata_in_tool_result():
    src = __import__("pathlib").Path("core/agent.py").read_text(encoding="utf-8")
    assert 'result.get("error_code") == "APPROVAL_REQUIRED"' in src
    assert 'emit("approval_required"' in src
    assert '"waiting_for_approval": pending_approval' in src
    assert '"status": "waiting_for_approval" if pending_approval else "done"' in src


def test_mission_promotes_approval_required_to_blocked_state():
    src = __import__("pathlib").Path("core/mission.py").read_text(encoding="utf-8")
    assert 'res.get("status") == "waiting_for_approval"' in src
    assert '"error": "approval_required"' in src
    assert 'report.approval_request = approval_request' in src
    assert 'hermus perms resolve {req_id} approve --retry' in src
    assert 'report.approval_request = None' in src
    assert '"approval_request": self.approval_request' in src


def test_control_room_exposes_mission_approval_resume_controls():
    src = __import__("pathlib").Path("gateway/control.html").read_text(encoding="utf-8")
    assert 'id="tab-missions"' in src
    assert 'approve+retry' in src
    assert '/permissions/pending/resolve' in src
    assert '/missions/' in src and '/resume' in src
    assert 'refreshMissions' in src
