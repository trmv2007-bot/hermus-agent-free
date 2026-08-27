"""Permission & risk manager — ALLOW / ASK / DENY gate for tool execution.

Security as a first-class subsystem. Every tool request is classified into a
risk category (READ / WRITE / EXECUTE / NETWORK / GUI / ADMIN), mapped to a
policy decision, with per-agent overrides, allow/deny lists, and an append-only
audit log.

Default posture (overridable):

    read / web lookup   → ALLOW
    create file         → ALLOW
    delete / modify     → ASK
    shell / sudo        → ASK
    network scan        → ASK
    credentials         → DENY
    admin / GUI         → DENY (until explicitly enabled)

This module is a decision engine: it does not enforce anything by itself. Wire
it into ``tool_registry.execute`` / the computer-control layer so every risky
action is gated.
"""
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

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


# tool name (or substring) -> (risk, default decision)
DEFAULT_POLICY: Dict[str, tuple] = {
    # READ — safe
    "read_file": (Risk.READ, Decision.ALLOW),
    "list_files": (Risk.READ, Decision.ALLOW),
    "memory_search": (Risk.READ, Decision.ALLOW),
    "memory2": (Risk.READ, Decision.ALLOW),
    "web_search": (Risk.NETWORK, Decision.ALLOW),
    "web_read": (Risk.NETWORK, Decision.ALLOW),
    "browser_navigate": (Risk.NETWORK, Decision.ALLOW),
    "browser_screenshot": (Risk.NETWORK, Decision.ALLOW),
    "vision_analyze": (Risk.READ, Decision.ALLOW),
    # WRITE — creating is fine
    "write_file": (Risk.WRITE, Decision.ALLOW),
    "create_file": (Risk.WRITE, Decision.ALLOW),
    "append_file": (Risk.WRITE, Decision.ALLOW),
    "curate_memory": (Risk.WRITE, Decision.ALLOW),
    "embeddings_ingest": (Risk.WRITE, Decision.ALLOW),
    # WRITE — destructive needs consent
    "delete_file": (Risk.WRITE, Decision.ASK),
    "move_file": (Risk.WRITE, Decision.ASK),
    "overwrite_file": (Risk.WRITE, Decision.ASK),
    # EXECUTE
    "shell_execute": (Risk.EXECUTE, Decision.ASK),
    "shell": (Risk.EXECUTE, Decision.ASK),
    "run_backend": (Risk.EXECUTE, Decision.ASK),
    "sudo": (Risk.ADMIN, Decision.ASK),
    # NETWORK — riskier
    "network_scan": (Risk.NETWORK, Decision.ASK),
    "http_request": (Risk.NETWORK, Decision.ASK),
    "port_scan": (Risk.NETWORK, Decision.ASK),
    # GUI / ADMIN
    "click": (Risk.GUI, Decision.DENY),
    "type_text": (Risk.GUI, Decision.DENY),
    "keyboard": (Risk.GUI, Decision.DENY),
    "mouse": (Risk.GUI, Decision.DENY),
    # screen recording: capturing the display is sensitive (DENY), but
    # read-only inspection of the recorder state is safe (ALLOW).
    "screen_record_start": (Risk.GUI, Decision.DENY),
    "screen_record_stop": (Risk.GUI, Decision.ALLOW),
    "screen_record_status": (Risk.READ, Decision.ALLOW),
    "screen_record_save": (Risk.GUI, Decision.ASK),
    "screen_get_recent": (Risk.READ, Decision.ALLOW),
    "screen_analyze": (Risk.READ, Decision.ALLOW),
    "screen_understand": (Risk.READ, Decision.ALLOW),
    "screen_verify": (Risk.READ, Decision.ALLOW),
    "screen_action_before": (Risk.GUI, Decision.DENY),
    "screen_action_after": (Risk.GUI, Decision.DENY),
    "screen_watch": (Risk.GUI, Decision.DENY),
    # computer action engine (v2) — delegated to core.computer.ComputerPolicy,
    # but registered here so the tool registry can gate them as well.
    "computer_move_mouse": (Risk.GUI, Decision.DENY),
    "computer_click": (Risk.GUI, Decision.DENY),
    "computer_double_click": (Risk.GUI, Decision.DENY),
    "computer_right_click": (Risk.GUI, Decision.DENY),
    "computer_click_target": (Risk.GUI, Decision.DENY),
    "computer_find_on_screen": (Risk.READ, Decision.ALLOW),
    "computer_type_text": (Risk.GUI, Decision.DENY),
    "computer_press_key": (Risk.GUI, Decision.DENY),
    "computer_hotkey": (Risk.GUI, Decision.DENY),
    "computer_scroll": (Risk.GUI, Decision.DENY),
    "computer_open_application": (Risk.GUI, Decision.DENY),
    "computer_close_application": (Risk.GUI, Decision.DENY),
    "computer_focus_window": (Risk.GUI, Decision.DENY),
    "computer_task": (Risk.GUI, Decision.ASK),
    "computer_stop": (Risk.GUI, Decision.ALLOW),
    # ---- upgrades: hybrid memory, sandbox, delegation, skill forge --------
    "memory_hybrid_search": (Risk.READ, Decision.ALLOW),
    "memory2_recall": (Risk.READ, Decision.ALLOW),
    "memory_sweep": (Risk.WRITE, Decision.ASK),      # archives/purges typed memory
    "skill_harvest": (Risk.WRITE, Decision.ALLOW),   # writes a validated skill file
    "skill_forge_stats": (Risk.READ, Decision.ALLOW),
    "delegate_tasks": (Risk.EXECUTE, Decision.ALLOW),  # spawns sandboxed worker procs
    "subagent_spawn": (Risk.EXECUTE, Decision.ALLOW),
    # A sandboxed command is strictly safer than the raw shell tool, so it stays
    # ASK (audited) rather than DENY — but escalation inside it is still admin.
    "sandbox_run": (Risk.EXECUTE, Decision.ASK),
    "sandbox_status": (Risk.READ, Decision.ALLOW),
    "credential_access": (Risk.ADMIN, Decision.DENY),
    "get_credential": (Risk.ADMIN, Decision.DENY),
    "install_package": (Risk.ADMIN, Decision.ASK),
    "system_config": (Risk.ADMIN, Decision.ASK),
}

DEFAULT_DECISION = Decision.ASK
DEFAULT_RISK = Risk.READ


class PermissionManager:
    def __init__(self, overrides_path: Optional[Path] = None):
        self.overrides_path = overrides_path or (workspace.dirs["memory"] / "permissions.json")
        self.overrides = self._load_overrides()

    # -- persistence ----------------------------------------------------
    def _load_overrides(self) -> Dict[str, Any]:
        try:
            return json.loads(self.overrides_path.read_text(encoding="utf-8"))
        except Exception:
            return {"tools": {}, "agents": {}, "allowlist": [], "denylist": []}

    def _save_overrides(self) -> None:
        self.overrides_path.parent.mkdir(parents=True, exist_ok=True)
        self.overrides_path.write_text(json.dumps(self.overrides, indent=2), encoding="utf-8")

    # -- classification -------------------------------------------------
    def classify(self, tool_name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        args = args or {}
        risk = DEFAULT_RISK
        decision = DEFAULT_DECISION
        # longest matching policy key wins
        matched = None
        for key, (r, d) in DEFAULT_POLICY.items():
            if tool_name == key or (len(key) > 3 and key in tool_name):
                if matched is None or len(key) > len(matched):
                    matched = key
                    risk, decision = r, d
        # args can escalate risk (e.g. shell with sudo, write to sensitive path)
        if tool_name in ("shell_execute", "shell", "sandbox_run", "backend_execute", "computer_task"):
            cmd = " ".join(str(v) for v in args.values()).lower()
            # Privilege escalation is admin regardless of what the sandbox screen thinks.
            dangerous = any(m in cmd for m in ("sudo ", " sudo", "rm -rf /", "dd if=", "mkfs", ":(){"))
            if not dangerous:
                try:  # agree with the sandbox's own screen — one list to maintain
                    from .sandbox import scan_command

                    dangerous = bool(scan_command(cmd))
                except Exception:
                    pass
            if dangerous:
                risk, decision = Risk.ADMIN, Decision.ASK
            elif args.get("network") or "curl " in cmd or "wget " in cmd:
                risk, decision = Risk.NETWORK, Decision.ASK
        if tool_name in ("write_file", "create_file", "append_file"):
            target = str(args.get("path") or args.get("file") or "").lower()
            if any(s in target for s in ("/etc/", "~/.ssh", ".env", "credentials", "id_rsa")):
                risk, decision = Risk.ADMIN, Decision.ASK
        return {"tool": tool_name, "risk": risk.value, "default": decision.value}

    def check(self, tool_name: str, agent: Optional[str] = None,
              args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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

        info["decision"] = decision.value
        info["agent"] = agent
        self.audit(tool_name, decision.value, agent, info["risk"])
        return info

    def set_policy(self, tool_name: str, decision: str,
                   agent: Optional[str] = None) -> Dict[str, Any]:
        if decision not in (Decision.ALLOW.value, Decision.ASK.value, Decision.DENY.value):
            return {"success": False, "error": f"decision must be allow/ask/deny, got '{decision}'"}
        if agent:
            self.overrides.setdefault("agents", {}).setdefault(agent, {})[tool_name] = decision
        else:
            self.overrides.setdefault("tools", {})[tool_name] = decision
        self._save_overrides()
        return {"success": True, "tool": tool_name, "decision": decision, "agent": agent}

    def audit(self, tool_name: str, decision: str, agent: Optional[str],
              risk: Optional[str] = None) -> Path:
        # Append a structured JSONL entry directly (workspace.log wraps strings
        # as {"ts", "line"}, which would double-encode the fields we need).
        path = workspace.dirs["logs"] / "permissions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(),
            "tool": tool_name,
            "decision": decision,
            "agent": agent,
            "risk": risk,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return path

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
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


permission_manager = PermissionManager()
