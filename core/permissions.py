"""Permission & risk manager — ALLOW / ASK / DENY gate for tool execution.

Security as a first-class subsystem. Every tool request is classified into a
capability and risk category (READ, WRITE_WORKSPACE, WRITE_SYSTEM, EXECUTE_SANDBOX,
EXECUTE_HOST, NETWORK, CREDENTIALS, GUI, ADMIN), mapped to a policy decision,
with per-agent overrides, allow/deny lists, and an append-only audit log.

Unified execution path:
  LLM Request → Policy Classifier → Capability Check → Permission/Sandbox Gate → Execution → Audit
"""
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .workspace import workspace


class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class Risk(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    GUI = "gui"
    ADMIN = "admin"


class Capability(str, Enum):
    READ = "read"
    WRITE_WORKSPACE = "write_workspace"
    WRITE_SYSTEM = "write_system"
    EXECUTE_SANDBOX = "execute_sandbox"
    EXECUTE_HOST = "execute_host"
    NETWORK = "network"
    CREDENTIALS = "credentials"
    GUI = "gui"
    ADMIN = "admin"


# tool name (or substring) -> (risk, default decision, required capabilities)
DEFAULT_POLICY: dict[str, tuple] = {
    # READ — safe
    "read_file": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "list_files": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "memory_search": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "memory2": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "web_search": (Risk.NETWORK, Decision.ALLOW, [Capability.NETWORK]),
    "web_read": (Risk.NETWORK, Decision.ALLOW, [Capability.NETWORK]),
    "browser_navigate": (Risk.NETWORK, Decision.ALLOW, [Capability.NETWORK]),
    "browser_screenshot": (Risk.NETWORK, Decision.ALLOW, [Capability.NETWORK]),
    "vision_analyze": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "local_folder_defensive_scan": (Risk.READ, Decision.ASK, [Capability.READ]),
    "speech_status": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "speech_clone_prompts": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "voice_available_models": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "voice_discover_local_models": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "avatar_service_status": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "avatar_render_status": (Risk.NETWORK, Decision.ALLOW, [Capability.NETWORK]),
    # WRITE — creating is fine
    "write_file": (Risk.WRITE, Decision.ALLOW, [Capability.WRITE_WORKSPACE]),
    "create_file": (Risk.WRITE, Decision.ALLOW, [Capability.WRITE_WORKSPACE]),
    "append_file": (Risk.WRITE, Decision.ALLOW, [Capability.WRITE_WORKSPACE]),
    "curate_memory": (Risk.WRITE, Decision.ALLOW, [Capability.WRITE_WORKSPACE]),
    "speech_clone_prompt_create": (Risk.WRITE, Decision.ALLOW, [Capability.WRITE_WORKSPACE]),
    "speech_synthesize": (Risk.WRITE, Decision.ALLOW, [Capability.WRITE_WORKSPACE]),
    "embeddings_ingest": (Risk.WRITE, Decision.ALLOW, [Capability.WRITE_WORKSPACE]),
    # WRITE — destructive needs consent
    "delete_file": (Risk.WRITE, Decision.ASK, [Capability.WRITE_WORKSPACE]),
    "move_file": (Risk.WRITE, Decision.ASK, [Capability.WRITE_WORKSPACE]),
    "overwrite_file": (Risk.WRITE, Decision.ASK, [Capability.WRITE_WORKSPACE]),
    # EXECUTE
    "shell_execute": (Risk.EXECUTE, Decision.ASK, [Capability.EXECUTE_HOST]),
    "shell": (Risk.EXECUTE, Decision.ASK, [Capability.EXECUTE_HOST]),
    "run_backend": (Risk.EXECUTE, Decision.ASK, [Capability.EXECUTE_HOST]),
    "sudo": (Risk.ADMIN, Decision.ASK, [Capability.ADMIN]),
    # NETWORK — riskier
    "network_scan": (Risk.NETWORK, Decision.ASK, [Capability.NETWORK]),
    "http_request": (Risk.NETWORK, Decision.ASK, [Capability.NETWORK]),
    "port_scan": (Risk.NETWORK, Decision.ASK, [Capability.NETWORK]),
    "avatar_prepare_voice": (Risk.NETWORK, Decision.ASK, [Capability.NETWORK]),
    "avatar_synthesize_audio": (Risk.NETWORK, Decision.ASK, [Capability.NETWORK]),
    "avatar_render_video": (Risk.NETWORK, Decision.ASK, [Capability.NETWORK]),
    # GUI / ADMIN
    "click": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "type_text": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "keyboard": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "mouse": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "screen_record_start": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "screen_record_stop": (Risk.GUI, Decision.ALLOW, [Capability.GUI]),
    "screen_record_status": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "screen_record_save": (Risk.GUI, Decision.ASK, [Capability.GUI]),
    "screen_get_recent": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "screen_analyze": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "screen_understand": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "screen_verify": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "screen_action_before": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "screen_action_after": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "screen_watch": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    # computer action engine (v2)
    "computer_move_mouse": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "computer_click": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "computer_double_click": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "computer_right_click": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "computer_click_target": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "computer_find_on_screen": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "computer_type_text": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "computer_press_key": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "computer_hotkey": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "computer_scroll": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "computer_open_application": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "computer_close_application": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "computer_focus_window": (Risk.GUI, Decision.DENY, [Capability.GUI]),
    "computer_task": (Risk.GUI, Decision.ASK, [Capability.GUI]),
    "computer_stop": (Risk.GUI, Decision.ALLOW, [Capability.GUI]),
    # ---- upgrades: hybrid memory, sandbox, delegation, skill forge --------
    "memory_hybrid_search": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "memory2_recall": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "memory_sweep": (Risk.WRITE, Decision.ASK, [Capability.WRITE_WORKSPACE]),
    "skill_harvest": (Risk.WRITE, Decision.ALLOW, [Capability.WRITE_WORKSPACE]),
    "skill_forge_stats": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "delegate_tasks": (Risk.EXECUTE, Decision.ALLOW, [Capability.EXECUTE_SANDBOX]),
    "subagent_spawn": (Risk.EXECUTE, Decision.ALLOW, [Capability.EXECUTE_SANDBOX]),
    "sandbox_run": (Risk.EXECUTE, Decision.ASK, [Capability.EXECUTE_SANDBOX]),
    "sandbox_status": (Risk.READ, Decision.ALLOW, [Capability.READ]),
    "credential_access": (Risk.ADMIN, Decision.DENY, [Capability.CREDENTIALS]),
    "get_credential": (Risk.ADMIN, Decision.DENY, [Capability.CREDENTIALS]),
    "install_package": (Risk.ADMIN, Decision.ASK, [Capability.ADMIN]),
    "system_config": (Risk.ADMIN, Decision.ASK, [Capability.ADMIN]),
}

DEFAULT_DECISION = Decision.ASK
DEFAULT_RISK = Risk.READ
DEFAULT_CAPS = [Capability.READ]


class PermissionManager:
    def __init__(self, overrides_path: Optional[Path] = None, approvals_path: Optional[Path] = None):
        self.overrides_path = overrides_path or (workspace.dirs["memory"] / "permissions.json")
        self.overrides = self._load_overrides()
        try:
            from .approval import ApprovalStore

            self.approvals = ApprovalStore(approvals_path or (self.overrides_path.parent / "approval_grants.json"))
        except Exception:
            self.approvals = None

    # -- persistence ----------------------------------------------------
    def _load_overrides(self) -> dict[str, Any]:
        try:
            return json.loads(self.overrides_path.read_text(encoding="utf-8"))
        except Exception:
            return {"tools": {}, "agents": {}, "allowlist": [], "denylist": []}

    def _save_overrides(self) -> None:
        self.overrides_path.parent.mkdir(parents=True, exist_ok=True)
        self.overrides_path.write_text(json.dumps(self.overrides, indent=2), encoding="utf-8")

    # -- classification -------------------------------------------------
    def classify(self, tool_name: str, args: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        args = args or {}
        risk = DEFAULT_RISK
        decision = DEFAULT_DECISION
        caps = list(DEFAULT_CAPS)

        # longest matching policy key wins
        matched = None
        for key, entry in DEFAULT_POLICY.items():
            r = entry[0]
            d = entry[1]
            c = entry[2] if len(entry) > 2 else [Capability.READ]
            if tool_name == key or (len(key) > 3 and key in tool_name):
                if matched is None or len(key) > len(matched):
                    matched = key
                    risk, decision, caps = r, d, c

        # args can escalate risk (e.g. shell with sudo, write to sensitive path)
        if tool_name in ("shell_execute", "shell", "sandbox_run", "backend_execute", "computer_task"):
            cmd = " ".join(str(v) for v in args.values()).lower()
            dangerous = any(m in cmd for m in ("sudo ", " sudo", "rm -rf /", "dd if=", "mkfs", ":(){"))
            if not dangerous:
                try:
                    from .sandbox import scan_command
                    dangerous = bool(scan_command(cmd))
                except Exception:
                    pass
            if dangerous:
                risk, decision = Risk.ADMIN, Decision.ASK
                caps = [Capability.ADMIN, Capability.EXECUTE_HOST]
            elif args.get("network") or "curl " in cmd or "wget " in cmd:
                risk, decision = Risk.NETWORK, Decision.ASK
                caps = [Capability.NETWORK, Capability.EXECUTE_HOST]

        if tool_name in ("write_file", "create_file", "append_file"):
            target = str(args.get("path") or args.get("file") or "").replace("\\", "/")
            lowered = target.lower()
            if any(s in lowered for s in ("/etc/", "~/.ssh", ".env", "credentials", "id_rsa")):
                risk, decision = Risk.ADMIN, Decision.ASK
                caps = [Capability.WRITE_SYSTEM, Capability.CREDENTIALS]
            else:
                caps = [Capability.WRITE_WORKSPACE]

            # Direct tool calls may write ordinary workspace files, but the
            # evolution control plane is immutable to an autonomous writer.
            # A protected change can still be proposed through core.evolution
            # and reviewed independently.
            try:
                from .evolution import EvolutionPolicy
                if target and EvolutionPolicy().protected_files([target]):
                    risk, decision = Risk.ADMIN, Decision.DENY
                    caps = [Capability.ADMIN]
            except Exception:
                # Fail closed if the control-plane policy cannot be loaded.
                if target and any(part in lowered for part in ("permissions.py", "rollback.py", "sandbox.py")):
                    risk, decision = Risk.ADMIN, Decision.DENY
                    caps = [Capability.ADMIN]

        safety_info: dict[str, Any] = {"zone": "green", "red_lines": [], "reasons": [], "suggested_decision": "allow"}
        try:
            from .safety_policy import assess_tool_action

            safety = assess_tool_action(tool_name, args)
            safety_info = safety.to_dict()
            if safety.zone == "red":
                risk, decision = Risk.ADMIN, Decision.DENY
                caps = [Capability.ADMIN]
            elif safety.zone == "yellow" and decision == Decision.ALLOW:
                # Yellow actions are valid power, not forbidden. They should be
                # explicitly approved or covered by a configured automation rule.
                decision = Decision.ASK
        except Exception as exc:
            # The red-line classifier is part of the safety path; if it fails on
            # an obviously powerful action, require approval rather than greenlighting it.
            safety_info = {"zone": "unknown", "red_lines": [], "reasons": [f"safety classifier unavailable: {exc}"], "suggested_decision": "ask"}
            if decision == Decision.ALLOW and risk in (Risk.ADMIN, Risk.EXECUTE, Risk.NETWORK, Risk.GUI):
                decision = Decision.ASK

        return {
            "tool": tool_name,
            "risk": risk.value,
            "default": decision.value,
            "immutable": bool(tool_name in ("write_file", "create_file", "append_file") and decision == Decision.DENY and risk == Risk.ADMIN),
            "capabilities": [c.value if isinstance(c, Capability) else str(c) for c in caps],
            "safety": safety_info,
        }

    def check(
        self,
        tool_name: str,
        agent: Optional[str] = None,
        args: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        info = self.classify(tool_name, args)
        decision = Decision(info["default"])

        # global allow/deny lists take precedence
        if tool_name in self.overrides.get("denylist", []):
            decision = Decision.DENY
        if tool_name in self.overrides.get("allowlist", []):
            decision = Decision.ALLOW
        # per-agent overrides
        if agent and agent in self.overrides.get("agents", {}):
            agent_cfg = self.overrides["agents"][agent]
            if tool_name in agent_cfg:
                decision = Decision(str(agent_cfg[tool_name]))
        # per-tool override
        if tool_name in self.overrides.get("tools", {}):
            decision = Decision(str(self.overrides["tools"][tool_name]))

        safety = info.get("safety") or {}
        approval_match = None
        emergency = self._emergency_stop_decision(tool_name, info)

        # Red-zone safety findings, immutable control-plane paths, and active
        # emergency stop cannot be re-enabled by allowlists or per-agent
        # overrides. Emergency stop is the Red Line 1 brake.
        if safety.get("zone") == "red":
            decision = Decision.DENY
            self._record_capability_need(tool_name, args or {}, safety,
                                         reason="red-line action blocked")
        elif info.get("immutable"):
            decision = Decision.DENY
            self._record_capability_need(tool_name, args or {}, safety,
                                         reason="protected safety/control-plane change requires independent review")
        elif emergency is not None:
            decision = Decision.DENY
            info["emergency_stop"] = emergency
        elif decision == Decision.ASK and safety.get("zone") == "yellow" and self.approvals is not None:
            approval_match = self.approvals.allowed(tool_name, args or {}, safety, consume=True)
            if approval_match:
                decision = Decision.ALLOW
            else:
                request = self.approvals.create_request(tool_name, args or {}, safety)
                if request.get("success"):
                    info["approval_request"] = request.get("request")
                    self._record_capability_need(tool_name, args or {}, safety,
                                                 reason="yellow action needs scoped approval")

        info["decision"] = decision.value
        info["agent"] = agent
        if approval_match:
            info["approval"] = approval_match
        self.audit(tool_name, decision.value, agent, info["risk"], extra={"safety": safety, "approval": approval_match, "approval_request": info.get("approval_request"), "emergency_stop": info.get("emergency_stop")})
        return info

    def _emergency_stop_decision(self, tool_name: str, info: dict[str, Any]) -> Optional[dict[str, Any]]:
        # Status/stop/read-only actions must remain available so the user can
        # inspect and recover the system after hitting the brake.
        safe_tools = {
            "read_file", "list_files", "memory_search", "memory2", "sandbox_status",
            "screen_record_status", "screen_get_recent", "computer_stop",
            "emergency_stop", "emergency_status", "emergency_resume",
        }
        if tool_name in safe_tools or tool_name.endswith("_status") or tool_name.endswith("_stop"):
            return None
        if info.get("risk") == Risk.READ.value and info.get("safety", {}).get("zone") == "green":
            return None
        try:
            from .emergency_stop import get_emergency_stop

            state = get_emergency_stop().state()
            if state.active:
                return state.to_dict()
        except Exception:
            return {"active": True, "reason": "emergency stop check failed closed"}
        return None

    def _record_capability_need(self, tool_name: str, args: dict[str, Any], safety: dict[str, Any], *, reason: str) -> None:
        try:
            from .capability_ledger import get_capability_ledger

            get_capability_ledger().record_blocked_action(tool_name, args, safety, reason=reason)
        except Exception:
            # Ledger updates are important but must not break permission checks.
            pass

    def set_policy(
        self,
        tool_name: str,
        decision: str,
        agent: Optional[str] = None,
    ) -> dict[str, Any]:
        if decision not in (Decision.ALLOW.value, Decision.ASK.value, Decision.DENY.value):
            return {"success": False, "error": f"decision must be allow/ask/deny, got '{decision}'"}
        if agent:
            self.overrides.setdefault("agents", {}).setdefault(agent, {})[tool_name] = decision
        else:
            self.overrides.setdefault("tools", {})[tool_name] = decision
        self._save_overrides()
        return {"success": True, "tool": tool_name, "decision": decision, "agent": agent}

    def audit(
        self,
        tool_name: str,
        decision: str,
        agent: Optional[str],
        risk: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> Path:
        path = workspace.dirs["logs"] / "permissions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(),
            "tool": tool_name,
            "decision": decision,
            "agent": agent,
            "risk": risk,
            "extra": extra or {},
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return path

    def approvals_list(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        if self.approvals is None:
            return []
        return self.approvals.list(include_inactive=include_inactive)

    def approval_grant(
        self,
        title: str,
        *,
        tool: str = "*",
        red_lines: Optional[list[int]] = None,
        resources: Optional[list[str]] = None,
        purpose: str = "",
        ttl_minutes: Optional[int] = None,
        max_uses: Optional[int] = None,
        notes: str = "",
    ) -> dict[str, Any]:
        if self.approvals is None:
            return {"success": False, "error": "approval store unavailable"}
        return self.approvals.create(
            title=title,
            tool=tool,
            red_lines=red_lines or [],
            resources=resources or [],
            purpose=purpose,
            ttl_minutes=ttl_minutes,
            max_uses=max_uses,
            notes=notes,
        )

    def approval_revoke(self, grant_id: str) -> dict[str, Any]:
        if self.approvals is None:
            return {"success": False, "error": "approval store unavailable", "id": grant_id}
        return self.approvals.revoke(grant_id)

    def approval_pending(self, include_resolved: bool = False) -> list[dict[str, Any]]:
        if self.approvals is None:
            return []
        return self.approvals.pending(include_resolved=include_resolved)

    def approval_bundles(self, include_resolved: bool = False) -> list[dict[str, Any]]:
        if self.approvals is None or not hasattr(self.approvals, "bundles"):
            return []
        return self.approvals.bundles(include_resolved=include_resolved)

    def approval_bundle_resolve(
        self,
        bundle_id: str,
        decision: str,
        *,
        ttl_minutes: Optional[int] = None,
        max_uses: Optional[int] = None,
        notes: str = "",
    ) -> dict[str, Any]:
        if self.approvals is None or not hasattr(self.approvals, "resolve_bundle"):
            return {"success": False, "error": "approval bundle store unavailable", "id": bundle_id}
        return self.approvals.resolve_bundle(bundle_id, decision, ttl_minutes=ttl_minutes, max_uses=max_uses, notes=notes)

    def approval_resolve(
        self,
        request_id: str,
        decision: str,
        *,
        resources: Optional[list[str]] = None,
        purpose: str = "",
        ttl_minutes: Optional[int] = None,
        max_uses: Optional[int] = None,
        notes: str = "",
    ) -> dict[str, Any]:
        if self.approvals is None:
            return {"success": False, "error": "approval store unavailable", "id": request_id}
        return self.approvals.resolve_request(
            request_id,
            decision,
            resources=resources,
            purpose=purpose,
            ttl_minutes=ttl_minutes,
            max_uses=max_uses,
            notes=notes,
        )

    def approval_retry(self, request_id: str) -> dict[str, Any]:
        """Retry an approved pending action through the canonical ToolGateway.

        This is intentionally conservative: it retries only requests that are
        already resolved as approved, and it uses the stored redacted args. The
        normal permission path runs again, so the freshly-created scoped grant is
        consumed and red-zone actions remain denied.
        """
        if self.approvals is None:
            return {"success": False, "error": "approval store unavailable", "id": request_id}
        req = self.approvals.get_request(request_id)
        if req is None:
            return {"success": False, "error": "approval request not found", "id": request_id}
        if req.status != "approved":
            return {"success": False, "error": f"approval request is {req.status}, not approved", "request": req.to_dict()}
        try:
            from .tools import get_tool_gateway

            result = get_tool_gateway().execute(req.tool, dict(req.args_redacted), actor="approval-retry")
            payload = result.to_dict() if hasattr(result, "to_dict") else dict(result)
            return {"success": bool(payload.get("ok", False)), "request": req.to_dict(), "result": payload}
        except Exception as exc:
            return {"success": False, "error": str(exc), "request": req.to_dict()}

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        path = workspace.dirs["logs"] / "permissions.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out


class PolicyGate:
    """Unified policy enforcement layer guaranteeing no tool call bypasses security."""

    def __init__(self, manager: Optional[PermissionManager] = None):
        self.manager = manager or permission_manager

    def enforce(
        self,
        tool_name: str,
        args: Optional[dict[str, Any]] = None,
        agent: Optional[str] = None,
        granted_capabilities: Optional[set[str]] = None,
        strict: bool = False,
    ) -> dict[str, Any]:
        check_res = self.manager.check(tool_name, agent=agent, args=args)
        decision = check_res.get("decision")
        req_caps = set(check_res.get("capabilities", []))

        # Check explicit capabilities if specified
        if granted_capabilities is not None:
            missing_caps = req_caps - granted_capabilities
            if missing_caps:
                reason = f"Missing required capabilities: {', '.join(missing_caps)}"
                self.manager.audit(tool_name, Decision.DENY.value, agent, check_res.get("risk"), extra={"missing": list(missing_caps)})
                if strict:
                    raise PermissionError(f"Permission Denied for tool '{tool_name}': {reason}")
                return {"allowed": False, "decision": Decision.DENY.value, "reason": reason, "tool": tool_name}

        if decision == Decision.DENY.value:
            reason = f"Tool '{tool_name}' is blocked by security policy"
            if strict:
                raise PermissionError(reason)
            return {"allowed": False, "decision": Decision.DENY.value, "reason": reason, "tool": tool_name}

        return {
            "allowed": True,
            "decision": decision,
            "tool": tool_name,
            "risk": check_res.get("risk"),
            "capabilities": check_res.get("capabilities"),
        }


permission_manager = PermissionManager()
policy_gate = PolicyGate(permission_manager)
