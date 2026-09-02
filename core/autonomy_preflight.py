"""Pre-flight autonomy checks for missions and powerful actions.

A pre-flight does not execute tools and does not create pending approvals. It
predicts likely capabilities, red/yellow/green zones, missing approvals, missing
connectors/tools, emergency-stop state, and whether Hermus can start honestly.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .contracts import CommandStatus, EventEnvelope, EventType
from .safety_policy import assess_tool_action


@dataclass(frozen=True)
class PreflightAction:
    tool: str
    description: str
    args: dict[str, Any] = field(default_factory=dict)
    expected_capability: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PreflightFinding:
    tool: str
    description: str
    zone: str
    decision: str
    red_lines: list[int]
    reasons: list[str]
    capabilities: list[str]
    approval_present: bool = False
    missing_approval: bool = False
    missing_capability: bool = False
    missing_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AutonomyPreflightReport:
    goal: str
    status: str
    can_start: bool
    generated_at: str
    emergency_stop: dict[str, Any]
    actions: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    missing_approvals: list[dict[str, Any]]
    missing_capabilities: list[dict[str, Any]]
    red_line_blocks: list[dict[str, Any]]
    suggested_approval_prompts: list[dict[str, Any]]
    scope_assumptions: list[str]
    next_steps: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            f"# Autonomy Pre-flight — {self.goal}",
            "",
            f"Generated: {self.generated_at}",
            f"Status: **{self.status}**",
            f"Can start now: **{self.can_start}**",
            "",
            "## Emergency stop",
            "",
            f"- Active: {self.emergency_stop.get('active')}",
            f"- Reason: {self.emergency_stop.get('reason') or ''}",
            "",
            "## Likely actions",
            "",
        ]
        lines += _table(self.findings, ["tool", "zone", "decision", "red_lines", "capabilities", "description"], empty="No likely powerful actions detected.")
        lines += ["", "## Missing approvals", ""]
        lines += _table(self.missing_approvals, ["tool", "zone", "red_lines", "description", "missing_reason"], empty="No missing approvals predicted.")
        lines += ["", "## Missing capabilities/connectors", ""]
        lines += _table(self.missing_capabilities, ["tool", "expected_capability", "description", "missing_reason"], empty="No missing capabilities predicted.")
        lines += ["", "## Red-line blockers", ""]
        lines += _table(self.red_line_blocks, ["tool", "red_lines", "description", "reasons"], empty="No red-line blockers predicted.")
        lines += ["", "## Suggested approval prompts", ""]
        lines += _table(self.suggested_approval_prompts, ["title", "tool", "red_lines", "resources", "purpose"], empty="No draft approval prompts suggested.")
        lines += ["", "## Scope assumptions", ""]
        lines += [f"- {x}" for x in self.scope_assumptions] or ["- None"]
        lines += ["", "## Next steps", ""]
        lines += [f"- {x}" for x in self.next_steps] or ["- None"]
        lines += ["", "## Rule", "", "Pre-flight is advisory. It does not grant approvals, activate capabilities, or execute tools.", ""]
        return "\n".join(lines)


def preflight_goal(goal: str, *, actions: list[dict[str, Any]] | None = None) -> AutonomyPreflightReport:
    goal = str(goal or "").strip()
    inferred = [PreflightAction(**a) for a in actions] if actions else infer_actions(goal)
    emergency = _emergency_state()
    findings: list[PreflightFinding] = []
    missing_approvals: list[dict[str, Any]] = []
    missing_capabilities: list[dict[str, Any]] = []
    red_blocks: list[dict[str, Any]] = []

    for action in inferred:
        finding = assess_preflight_action(action)
        findings.append(finding)
        fd = {**finding.to_dict(), "expected_capability": action.expected_capability}
        if finding.missing_approval:
            missing_approvals.append(fd)
        if finding.missing_capability:
            missing_capabilities.append(fd)
        if finding.zone == "red" or finding.decision == "deny":
            red_blocks.append(fd)

    if emergency.get("active"):
        status = "EMERGENCY_STOP_ACTIVE"
    elif red_blocks:
        status = "BLOCKED_BY_RED_LINE"
    elif missing_capabilities:
        status = "MISSING_CAPABILITY"
    elif missing_approvals:
        status = "NEEDS_APPROVAL"
    else:
        status = "READY"

    report = AutonomyPreflightReport(
        goal=goal,
        status=status,
        can_start=status == "READY",
        generated_at=datetime.now(timezone.utc).isoformat(),
        emergency_stop=emergency,
        actions=[a.to_dict() for a in inferred],
        findings=[f.to_dict() for f in findings],
        missing_approvals=missing_approvals,
        missing_capabilities=missing_capabilities,
        red_line_blocks=red_blocks,
        suggested_approval_prompts=_suggest_approval_prompts(inferred, findings),
        scope_assumptions=_scope_assumptions(goal, findings),
        next_steps=_next_steps(status, missing_approvals, missing_capabilities, red_blocks, emergency),
    )
    _publish_preflight(report)
    return report


def create_preflight_approval_requests(
    goal: str,
    *,
    approval_store: Any = None,
    mission_id: str = "",
    bundle: bool = False,
) -> dict[str, Any]:
    """Create draft pending approval requests suggested by pre-flight.

    This creates *pending prompts only*. It never grants approval and never
    retries/executes the blocked action.
    """
    report = preflight_goal(goal)
    if approval_store is None:
        approval_store = _default_approval_store()
    if approval_store is None:
        return {"success": False, "error": "approval store unavailable", "preflight": report.to_dict(), "created": []}
    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    actions_by_tool = {a["tool"]: a for a in report.actions}
    for prompt in report.suggested_approval_prompts:
        tool = prompt.get("tool", "")
        action = actions_by_tool.get(tool, {})
        safety = {"zone": "yellow", "red_lines": prompt.get("red_lines", []), "reasons": prompt.get("reasons", []), "suggested_decision": "ask"}
        try:
            result = approval_store.create_request(tool, dict(action.get("args") or {}), safety)
            if result.get("success"):
                created.append(result.get("request", {}))
            else:
                errors.append({"tool": tool, "error": result.get("error")})
        except Exception as exc:  # noqa: BLE001
            errors.append({"tool": tool, "error": str(exc)})
    bundle_result = None
    if bundle and created and hasattr(approval_store, "create_bundle"):
        bundle_result = approval_store.create_bundle(
            f"Approval plan for mission {mission_id}" if mission_id else f"Approval plan for: {goal[:80]}",
            [req.get("id", "") for req in created],
            mission_id=mission_id,
            goal=goal,
        )
    _publish_preflight_prompts(goal, report, created, errors)
    return {"success": not errors, "preflight": report.to_dict(), "created": created, "bundle": bundle_result.get("bundle") if isinstance(bundle_result, dict) else None, "errors": errors}


def assess_preflight_action(action: PreflightAction) -> PreflightFinding:
    safety = assess_tool_action(action.tool, action.args)
    decision = "allow" if safety.zone == "green" else "ask" if safety.zone == "yellow" else "deny"
    capabilities: list[str] = []
    missing_capability = False
    missing_reason = ""
    approval_present = False

    try:
        from .permissions import DEFAULT_POLICY, permission_manager

        classified = permission_manager.classify(action.tool, action.args)
        capabilities = list(classified.get("capabilities") or [])
        decision = "deny" if classified.get("default") == "deny" or safety.zone == "red" else classified.get("default", decision)
        if action.tool not in DEFAULT_POLICY and not any(key in action.tool for key in DEFAULT_POLICY):
            missing_capability = True
            missing_reason = "tool/connector is not registered in the permission policy yet"
        if decision == "ask" and getattr(permission_manager, "approvals", None) is not None:
            approval_present = bool(permission_manager.approvals.allowed(action.tool, action.args, safety.to_dict(), consume=False))
    except Exception:
        if action.tool in {"send_email", "wallet_trade", "calendar_write", "connector_setup"}:
            missing_capability = True
            missing_reason = "tool/connector availability could not be confirmed"

    missing_approval = decision == "ask" and not approval_present and safety.zone != "red"
    if missing_capability and not missing_reason:
        missing_reason = "capability must be implemented/registered before use"
    return PreflightFinding(
        tool=action.tool,
        description=action.description,
        zone=safety.zone,
        decision=decision,
        red_lines=list(safety.red_lines),
        reasons=list(safety.reasons),
        capabilities=capabilities or [action.expected_capability] if action.expected_capability else capabilities,
        approval_present=approval_present,
        missing_approval=missing_approval,
        missing_capability=missing_capability,
        missing_reason=missing_reason,
    )


def infer_actions(goal: str) -> list[PreflightAction]:
    text = goal.lower()
    actions: list[PreflightAction] = []

    def add(tool: str, description: str, args: dict[str, Any], cap: str = "") -> None:
        if not any(a.tool == tool and a.description == description for a in actions):
            actions.append(PreflightAction(tool=tool, description=description, args=args, expected_capability=cap))

    if any(w in text for w in ("download", "documents", "home directory", "~/", "/home/", "folder", "local files")):
        resource = "~/Downloads" if "download" in text else "~/"
        label = "downloads folder" if "download" in text else "home directory"
        add("local_folder_defensive_scan", "Read-only defensive local folder scan", {"path": resource, "purpose": goal, "label": label}, "read")
    if any(w in text for w in ("malware", "virus", "forensic", "incident")) and not actions:
        add("local_folder_defensive_scan", "Read-only defensive local inspection", {"path": "approved local folder", "purpose": goal}, "read")
    if any(w in text for w in ("network scan", "port scan", "local network", "subnet", "pentest", "vulnerability", "security scan")):
        target = "192.168.0.0/16" if "local" in text or "subnet" in text else "declared target"
        add("network_scan", "Run authorized security/discovery scan", {"target": target, "purpose": goal}, "network")
    if any(w in text for w in ("gmail", "email", "send message", "reply", "slack", "telegram", "discord", "post as")):
        add("send_email", "Delegated communication or external send", {"action": "send email/reply as user", "purpose": goal}, "delegated_communication_connector")
    if any(w in text for w in ("wallet", "trade", "trading", "crypto", "stock", "invest", "spend", "buy", "purchase")):
        add("wallet_trade", "Agent wallet/spending/trading action", {"action": "agent wallet trade/spend", "purpose": goal}, "agent_wallet")
    if any(w in text for w in ("calendar", "schedule meeting", "book", "appointment")):
        add("calendar_write", "External calendar/account write", {"action": "write calendar event", "purpose": goal}, "calendar_connector")
    if any(w in text for w in ("screen", "click", "desktop", "computer", "browser automation")):
        add("computer_task", "Computer/GUI automation", {"task": goal}, "gui")
    if any(w in text for w in ("install connector", "add connector", "enable connector", "new capability", "new power")):
        add("connector_setup", "Capability/connector setup", {"action": goal}, "connector_or_tool")
    if any(w in text for w in ("delete", "wipe", "destroy", "drop database", "force push", "rm -rf")):
        add("shell_execute", "Potentially destructive change", {"command": goal}, "execute_host")
    if any(w in text for w in ("code", "build", "test", "fix", "implement", "repository", "app")):
        add("write_file", "Workspace code/file changes", {"path": "workspace", "purpose": goal}, "write_workspace")
        add("shell_execute", "Run build/tests/verification", {"command": "run project tests/build", "purpose": goal}, "execute_host")

    if not actions:
        add("memory_search", "Read/context lookup", {"query": goal}, "read")
    return actions


def _emergency_state() -> dict[str, Any]:
    try:
        from .emergency_stop import get_emergency_stop

        return get_emergency_stop().state().to_dict()
    except Exception as exc:
        return {"active": True, "reason": f"emergency stop unavailable: {exc}", "fail_closed": True}


def _suggest_approval_prompts(actions: list[PreflightAction], findings: list[PreflightFinding]) -> list[dict[str, Any]]:
    prompts: list[dict[str, Any]] = []
    # findings is built one-per-action in preflight_goal(); strict zip enforces that invariant.
    for action, finding in zip(actions, findings, strict=True):
        if not finding.missing_approval:
            continue
        safety_lines = list(finding.red_lines)
        prompts.append({
            "title": _approval_title(action, finding),
            "tool": action.tool,
            "red_lines": safety_lines,
            "resources": _prompt_resources(action.args, safety_lines),
            "purpose": _prompt_purpose(action.args, safety_lines),
            "reasons": finding.reasons,
            "description": action.description,
        })
    return prompts


def _approval_title(action: PreflightAction, finding: PreflightFinding) -> str:
    lines = set(finding.red_lines)
    if 3 in lines:
        resource = (_prompt_resources(action.args, finding.red_lines) or ["private data"])[0]
        return f"Allow {resource} scoped inspection"
    if 4 in lines or 8 in lines:
        resource = (_prompt_resources(action.args, finding.red_lines) or ["authorized target"])[0]
        return f"Allow authorized security scope for {resource}"
    if 6 in lines:
        return "Allow isolated agent-wallet action scope"
    if 9 in lines:
        return "Allow delegated communication scope"
    if 11 in lines:
        return "Allow capability setup planning scope"
    return f"Allow scoped {action.tool}"


def _prompt_resources(args: dict[str, Any], lines: list[int]) -> list[str]:
    text = " ".join(str(v) for v in args.values())
    out: list[str] = []
    for token in ("~/Downloads", "~/Documents", "~/", "/home/", "192.168.0.0/16", "192.168.", "10.0.0.0/8", "10.", "declared target"):
        if token in text and token not in out:
            out.append(token)
    if not out and 9 in set(lines):
        out.append("approved account/channel + recipients")
    if not out and 6 in set(lines):
        out.append("isolated agent wallet/account")
    return out


def _prompt_purpose(args: dict[str, Any], lines: list[int]) -> str:
    text = " ".join(str(v) for v in args.values()).lower()
    for word in ("malware", "incident", "backup", "recovery", "report", "trading", "wallet"):
        if word in text:
            return word
    line_set = set(lines)
    if 3 in line_set:
        return "defensive_scan"
    if 4 in line_set or 8 in line_set:
        return "authorized_security"
    if 6 in line_set:
        return "agent_wallet"
    if 9 in line_set:
        return "delegated_communication"
    return ""


def _default_approval_store() -> Any:
    try:
        from .permissions import permission_manager

        return getattr(permission_manager, "approvals", None)
    except Exception:
        try:
            from pathlib import Path
            from .approval import ApprovalStore

            return ApprovalStore(Path(__file__).resolve().parents[1] / "data" / "memory" / "approval_grants.json")
        except Exception:
            return None


def _scope_assumptions(goal: str, findings: list[PreflightFinding]) -> list[str]:
    assumptions: list[str] = []
    lines = {line for f in findings for line in f.red_lines}
    if 3 in lines:
        assumptions.append("Private/local data access must stay inside the approved resource, purpose, and output destination.")
    if 4 in lines or 8 in lines:
        assumptions.append("Security/reach actions require owned, administered, or explicitly authorized targets only.")
    if 6 in lines:
        assumptions.append("Financial actions require an isolated agent wallet/account, visible ledger, reserve, and risk limits.")
    if 9 in lines:
        assumptions.append("Delegated communication requires approved account/channel, recipient/scope, and high-impact confirmation rules.")
    if any(f.tool in {"send_email", "wallet_trade", "calendar_write"} for f in findings):
        assumptions.append("External account connectors must be disabled until the user completes setup and grants scope.")
    return assumptions


def _next_steps(status: str, approvals: list[dict[str, Any]], missing: list[dict[str, Any]], red: list[dict[str, Any]], emergency: dict[str, Any]) -> list[str]:
    if status == "READY":
        return ["Proceed through the normal runtime; continue to enforce ToolGateway permissions."]
    if status == "EMERGENCY_STOP_ACTIVE":
        return ["Review the emergency-stop reason and clear it only when safe."]
    steps: list[str] = []
    if red:
        steps.append("Rewrite the goal/action to avoid red-line behavior; red actions cannot be approved by grants.")
    if missing:
        steps.append("Record or review missing capabilities in CAPABILITY_LEDGER.md and generate setup proposals before implementation.")
    if approvals:
        steps.append("Create the narrowest scoped approval grants needed, with resource, purpose, TTL, and max-use limits.")
    return steps


def _publish_preflight(report: AutonomyPreflightReport) -> None:
    try:
        from .events import get_bus

        get_bus().publish(EventEnvelope(
            type=EventType.STATE_CHANGED.value,
            command="safety.preflight.completed",
            args_redacted={"goal": report.goal, "status": report.status, "summary": {
                "missing_approvals": len(report.missing_approvals),
                "missing_capabilities": len(report.missing_capabilities),
                "red_line_blocks": len(report.red_line_blocks),
                "suggested_approval_prompts": len(report.suggested_approval_prompts),
            }},
            status=CommandStatus.SUCCEEDED.value,
        ))
    except Exception:
        pass


def _publish_preflight_prompts(goal: str, report: AutonomyPreflightReport, created: list[dict[str, Any]], errors: list[dict[str, Any]]) -> None:
    try:
        from .events import get_bus

        get_bus().publish(EventEnvelope(
            type=EventType.STATE_CHANGED.value,
            command="safety.preflight.approval_prompts_created",
            args_redacted={"goal": goal, "preflight_status": report.status, "created": len(created), "errors": len(errors)},
            status=CommandStatus.SUCCEEDED.value if not errors else CommandStatus.FAILED.value,
        ))
    except Exception:
        pass


def _table(rows: list[dict[str, Any]], columns: list[str], *, empty: str) -> list[str]:
    if not rows:
        return [empty]
    out = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        vals = []
        for col in columns:
            val = row.get(col)
            if isinstance(val, list):
                val = ", ".join(str(x) for x in val)
            vals.append(str(val if val is not None else "").replace("\n", " ").replace("|", "/")[:220])
        out.append("| " + " | ".join(vals) + " |")
    return out


__all__ = [
    "AutonomyPreflightReport",
    "PreflightAction",
    "PreflightFinding",
    "assess_preflight_action",
    "create_preflight_approval_requests",
    "infer_actions",
    "preflight_goal",
]
