"""Capability Ledger helpers for Red Line 11.

Hermus may document powers it has, lacks, or discovers it could gain. This module
updates the human-readable ``CAPABILITY_LEDGER.md`` through a narrow API instead
of letting arbitrary file writes modify the ledger.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER_PATH = ROOT / "CAPABILITY_LEDGER.md"
DISCOVERED_HEADER = "## Discovered possible powers"
REQUESTED_HEADER = "## Requested powers"


@dataclass(frozen=True)
class CapabilityEntry:
    power: str
    use: str
    risk: str
    needed_approval_setup: str
    status: str = "not_granted"
    source: str = "user"
    created_at: str = ""

    @classmethod
    def create(
        cls,
        power: str,
        use: str = "",
        risk: str = "",
        needed_approval_setup: str = "",
        status: str = "not_granted",
        source: str = "user",
    ) -> "CapabilityEntry":
        return cls(
            power=_clean_cell(power),
            use=_clean_cell(use),
            risk=_clean_cell(risk),
            needed_approval_setup=_clean_cell(needed_approval_setup),
            status=_clean_cell(status or "not_granted"),
            source=_clean_cell(source or "user"),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def table_row(self) -> str:
        return (
            f"| {self.power} | {self.use} | {self.risk} | "
            f"{self.needed_approval_setup} | {self.status} |"
        )


@dataclass(frozen=True)
class CapabilityProposal:
    power: str
    category: str
    summary: str
    red_lines: list[int]
    required_approvals: list[str]
    implementation_plan: list[str]
    likely_files: list[str]
    tests: list[str]
    activation_gates: list[str]
    risks: list[str]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) if items else "- None"

        return f"""# Capability Setup Proposal — {self.power}

Generated: {self.created_at}
Category: {self.category}
Related red lines: {', '.join(str(x) for x in self.red_lines) or 'none'}

## Summary

{self.summary}

## Required approvals/setup

{bullets(self.required_approvals)}

## Implementation plan

{bullets(self.implementation_plan)}

## Likely files/modules

{bullets(self.likely_files)}

## Tests/evaluations

{bullets(self.tests)}

## Activation gates

{bullets(self.activation_gates)}

## Risks to manage

{bullets(self.risks)}

## Rule

This proposal documents how the power could be added. It does not grant,
activate, or authorize the power. Activation still requires explicit scoped
approval, audit logging, and revocation controls.
"""


class CapabilityLedger:
    def __init__(self, path: Path = DEFAULT_LEDGER_PATH):
        self.path = Path(path)

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""

    def list_discovered(self) -> list[dict[str, str]]:
        text = self.read()
        block = _section(text, DISCOVERED_HEADER)
        entries: list[dict[str, str]] = []
        for line in block.splitlines():
            if not line.strip().startswith("|") or "---" in line or "Power" in line:
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 5:
                entries.append({
                    "power": cells[0], "use": cells[1], "risk": cells[2],
                    "needed_approval_setup": cells[3], "status": cells[4],
                })
        return entries

    def add_discovered(self, entry: CapabilityEntry) -> dict[str, Any]:
        if not entry.power:
            return {"success": False, "error": "power is required"}
        text = self.read()
        if not text:
            text = _default_ledger()
        # Deduplicate by power name inside the discovered section.
        existing = {item["power"].lower() for item in self.list_discovered()}
        if entry.power.lower() in existing:
            return {"success": True, "deduped": True, "entry": entry.to_dict(), "path": str(self.path)}
        text = _insert_row(text, DISCOVERED_HEADER, entry.table_row())
        self.path.write_text(text, encoding="utf-8")
        self._audit("capability_discovered", entry.to_dict())
        self._publish("capability.discovered", entry.to_dict())
        return {"success": True, "deduped": False, "entry": entry.to_dict(), "path": str(self.path)}

    def record_blocked_action(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        safety: dict[str, Any] | None = None,
        *,
        reason: str = "",
        source: str = "permission_manager",
    ) -> dict[str, Any]:
        """Record a missing/not-granted power implied by a blocked/yellow action.

        This satisfies Red Line 11 without granting anything: Hermus documents the
        power it would need, the use, risk, and approval/setup needed.
        """
        entry = capability_entry_from_blocked_action(tool, args or {}, safety or {}, reason=reason, source=source)
        return self.add_discovered(entry)

    def propose(self, power: str, *, write: bool = False, output_dir: Optional[Path] = None) -> dict[str, Any]:
        """Generate a safe setup proposal for a missing/not-granted power."""
        proposal = capability_setup_proposal(power)
        markdown = proposal.to_markdown()
        path = None
        if write:
            out_dir = Path(output_dir) if output_dir is not None else (ROOT / "docs" / "capability_proposals")
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{_slug(power)}.md"
            path.write_text(markdown, encoding="utf-8")
            self._audit("capability_proposal_written", {"power": power, "path": str(path), "proposal": proposal.to_dict()})
            self._publish("capability.proposal.written", {"power": power, "path": str(path), "proposal": proposal.to_dict()})
        else:
            self._audit("capability_proposal_generated", {"power": power, "proposal": proposal.to_dict()})
            self._publish("capability.proposal.generated", {"power": power, "proposal": proposal.to_dict()})
        return {"success": True, "proposal": proposal.to_dict(), "markdown": markdown, "path": str(path) if path else None}

    def _audit(self, action: str, data: dict[str, Any]) -> None:
        try:
            from .workspace import workspace
            import json

            path = workspace.dirs["logs"] / "capability_ledger.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "action": action, **data}, sort_keys=True) + "\n")
        except Exception:
            pass

    def _publish(self, command: str, data: dict[str, Any]) -> None:
        try:
            from .contracts import Actor, CommandSource, CommandStatus, EventEnvelope, EventType
            from .events import get_bus

            get_bus().publish(EventEnvelope(
                actor=Actor.SYSTEM.value,
                source=CommandSource.INTERNAL.value,
                type=EventType.STATE_CHANGED.value,
                command=command,
                target=data.get("power"),
                args_redacted=data,
                status=CommandStatus.SUCCEEDED.value,
            ))
        except Exception:
            pass


def get_capability_ledger(path: Optional[Path] = None) -> CapabilityLedger:
    return CapabilityLedger(path or DEFAULT_LEDGER_PATH)


def capability_setup_proposal(power: str) -> CapabilityProposal:
    clean = _clean_cell(power or "Unknown capability")
    lowered = clean.lower()
    category = "generic"
    red_lines: list[int] = [11]
    approvals = ["Owner confirms the power should be added", "Activation remains disabled until a scoped grant exists"]
    plan = [
        "Define the exact user-facing capability and non-goals",
        "Add a connector/tool behind the canonical ToolGateway or route facade",
        "Classify actions through PermissionManager and red-line policy",
        "Expose status/health in the Control Room without fabricating readiness",
        "Add tests for allowed, approval-required, denied, and emergency-stop behavior",
    ]
    files = ["core/connectors/", "tools/", "core/permissions.py", "gateway/routes_subsystems.py", "gateway/control.html", "tests/"]
    tests = [
        "unit tests for connector/tool registration",
        "permission tests for green/yellow/red cases",
        "dashboard/API route tests",
        "emergency-stop regression test",
    ]
    gates = ["no connector enabled by import", "all external effects require scoped grant", "audit/event entries emitted", "revocation path documented"]
    risks = ["scope creep", "private data exposure", "unclear ownership of external resources"]

    if any(word in lowered for word in ("gmail", "email", "message", "telegram", "discord", "slack", "send", "reply")):
        category = "delegated_communication"
        red_lines = [3, 9, 11]
        approvals = [
            "User connects the exact account/channel",
            "User defines who Hermus may contact and when",
            "High-impact sends require preview/confirmation",
            "Revocation disables the connector immediately",
        ]
        plan = [
            "Create a disabled-by-default communication connector",
            "Add draft/preview/send actions as separate named tools",
            "Route send actions through scoped approval grants",
            "Log recipient, channel, subject/summary, and result without storing secrets",
            "Add Control Room approval UX for drafts and sends",
        ]
        files = ["core/connectors/", "tools/", "gateway/routes_subsystems.py", "gateway/control.html", "tests/test_*communication*.py"]
        tests = ["draft is green/read-only", "send without grant creates pending approval", "abusive impersonation/social-engineering wording is denied", "emergency stop blocks sends"]
        risks = ["privacy leak", "reputation damage", "unwanted commitments", "abusive impersonation"]
    elif any(word in lowered for word in ("wallet", "stock", "trade", "trading", "crypto", "invest", "money", "spend")):
        category = "agent_wallet_finance"
        red_lines = [4, 6, 11]
        approvals = ["Isolated agent-owned wallet/account only", "Visible ledger", "Minimum reserve", "Risk limits", "Owner-share policy", "Emergency freeze"]
        plan = ["Create wallet/account abstraction", "Implement append-only transaction ledger", "Add reserve/risk-limit checks before actions", "Separate quote/plan from execute", "Expose freeze/resume controls in dashboard"]
        files = ["core/wallet.py", "core/permissions.py", "gateway/routes_subsystems.py", "gateway/control.html", "tests/test_wallet*.py"]
        tests = ["cannot use personal bank/card", "cannot exceed reserve/risk limits", "market manipulation patterns denied", "emergency stop freezes actions"]
        risks = ["financial loss", "compliance/tax issues", "market manipulation", "hidden transactions"]
    elif any(word in lowered for word in ("scan", "network", "pentest", "security", "scrape", "incident")):
        category = "authorized_security_reach"
        red_lines = [4, 8, 11]
        approvals = ["Owned/administered/explicitly-authorized target scope", "Time-boxed scan window", "Allowed techniques", "No persistence/exfiltration"]
        plan = ["Create target-scope declaration model", "Gate scanners/scrapers through scope checks", "Log target/range/tool/purpose", "Add report-only default mode", "Expose active scope in dashboard"]
        files = ["pentest/", "core/permissions.py", "core/safety_policy.py", "gateway/control.html", "tests/test_*scope*.py"]
        tests = ["out-of-scope target denied", "in-scope target requires/uses grant", "random internet scan is blocked", "reports contain evidence and scope"]
        risks = ["unauthorized scanning", "third-party impact", "false positives", "sensitive findings exposure"]
    elif any(word in lowered for word in ("home", "folder", "file", "directory", "downloads", "documents")):
        category = "private_data_scope"
        red_lines = [3, 5, 11]
        approvals = ["Exact folders/resources", "Purpose", "Read-only vs write/delete", "TTL/use limit", "Output destination"]
        plan = ["Create scoped filesystem grant templates", "Default broad scans to read-only", "Add report generation without exfiltration", "Require checkpoint before destructive actions", "Show scan scope in Control Room"]
        files = ["tools/file_tools.py", "core/permissions.py", "gateway/control.html", "tests/test_*filesystem*.py"]
        tests = ["broad folder scan creates approval prompt", "scope-limited scan allowed", "delete requires recovery path", "outside-scope path denied"]
        risks = ["private data exposure", "accidental deletion", "over-broad indexing"]
    elif any(word in lowered for word in ("tool", "connector", "api", "calendar", "github", "cloud", "home assistant")):
        category = "connector_or_tool"
        red_lines = [3, 8, 11]
        approvals = ["Connector account/resource owner approval", "Credential storage plan", "Permission scopes", "Revocation path"]
        plan = ["Add disabled connector registration", "Expose status without network calls on import", "Add named read/write actions", "Gate write/high-impact actions", "Document setup in capability ledger"]
        files = ["core/connectors/", "tools/", "core/tool_registry.py", "gateway/routes_subsystems.py", "tests/test_connectors.py"]
        tests = ["connector disabled by default", "status works without credentials", "write action requires grant", "revocation disables actions"]
        risks = ["credential misuse", "over-broad account scope", "unexpected external effects"]

    return CapabilityProposal(
        power=clean,
        category=category,
        summary=f"Safe setup plan for adding or activating: {clean}.",
        red_lines=red_lines,
        required_approvals=approvals,
        implementation_plan=plan,
        likely_files=files,
        tests=tests,
        activation_gates=gates,
        risks=risks,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def capability_entry_from_blocked_action(
    tool: str,
    args: dict[str, Any],
    safety: dict[str, Any],
    *,
    reason: str = "",
    source: str = "permission_manager",
) -> CapabilityEntry:
    lines = {int(x) for x in (safety or {}).get("red_lines", []) if str(x).isdigit()}
    resource = _guess_resource(args)
    tool_name = _clean_cell(tool or "unknown_tool")
    status = "blocked" if (safety or {}).get("zone") == "red" else "not_granted"

    if 3 in lines:
        return CapabilityEntry.create(
            power=f"Scoped private-data access for {tool_name}",
            use=f"Use {tool_name} on {resource or 'approved private/local data'} for the requested defensive/search purpose",
            risk="private data or credential exposure",
            needed_approval_setup=f"Explicit resource scope, purpose, destination, TTL/use limit, and no data exfiltration outside scope. Reason: {reason}",
            status=status,
            source=source,
        )
    if 4 in lines or 8 in lines:
        return CapabilityEntry.create(
            power=f"Authorized security/reach scope for {tool_name}",
            use=f"Run discovery/scanning/scraping/control only on {resource or 'owned/administered/in-scope resources'}",
            risk="unauthorized access, third-party scanning, or incident-response overreach",
            needed_approval_setup=f"User-owned/administered/explicitly-authorized target scope and test purpose. Reason: {reason}",
            status=status,
            source=source,
        )
    if 6 in lines:
        return CapabilityEntry.create(
            power=f"Isolated agent-wallet capability for {tool_name}",
            use="Earn, spend, invest, trade, or transfer only from an approved agent-owned wallet/account",
            risk="financial loss, compliance, market manipulation, or hidden transactions",
            needed_approval_setup=f"Agent wallet/account, visible ledger, reserve, owner-share and risk limits. Reason: {reason}",
            status=status,
            source=source,
        )
    if 9 in lines:
        return CapabilityEntry.create(
            power=f"Delegated communication for {tool_name}",
            use="Communicate or transact on the user's behalf under an approved identity/channel rule",
            risk="privacy, reputation, legal/financial commitments, or abusive impersonation",
            needed_approval_setup=f"Channel/account connector, preview/send policy, recipient/scope, and high-impact approval rules. Reason: {reason}",
            status=status,
            source=source,
        )
    if 11 in lines:
        return CapabilityEntry.create(
            power=f"Capability expansion for {tool_name}",
            use="Activate or configure a new connector/capability only after visible approval",
            risk="silent power gain or uncontrolled scope expansion",
            needed_approval_setup=f"Documented setup plan, approval, audit logging, and revocation path. Reason: {reason}",
            status=status,
            source=source,
        )
    return CapabilityEntry.create(
        power=f"Missing or gated capability: {tool_name}",
        use=f"Needed to continue requested action on {resource or 'the requested target'}",
        risk="unknown until scoped",
        needed_approval_setup=f"Review blocker and grant only the minimum needed scope. Reason: {reason}",
        status=status,
        source=source,
    )


def _guess_resource(args: dict[str, Any]) -> str:
    for key in ("path", "file", "folder", "directory", "target", "url", "resource", "scope"):
        value = args.get(key)
        if value:
            return _clean_cell(value)
    command = str(args.get("command") or args.get("query") or "")
    for token in ("~/Downloads", "~/Documents", "~/", "/home/", "192.168.", "10.", "172.16."):
        if token in command:
            return token
    return ""


def _slug(value: object) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "capability").lower()).strip("-")
    return slug[:80] or "capability"


def _clean_cell(value: object) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "/").strip()
    return text[:300]


def _section(text: str, header: str) -> str:
    start = text.find(header)
    if start < 0:
        return ""
    rest = text[start + len(header):]
    next_header = rest.find("\n## ")
    return rest if next_header < 0 else rest[:next_header]


def _insert_row(text: str, header: str, row: str) -> str:
    if header not in text:
        text = text.rstrip() + f"\n\n{header}\n\n| Power | Use | Risk | Needed approval/setup | Status |\n|---|---|---|---|---|\n"
    start = text.find(header)
    next_header = text.find("\n## ", start + len(header))
    insert_at = len(text) if next_header < 0 else next_header
    prefix = text[:insert_at].rstrip()
    suffix = text[insert_at:]
    return prefix + "\n" + row + "\n" + suffix.lstrip("\n")


def _default_ledger() -> str:
    return """# Hermus Capability Ledger

## Discovered possible powers

| Power | Use | Risk | Needed approval/setup | Status |
|---|---|---|---|---|

## Requested powers

- None yet.
"""


__all__ = [
    "CapabilityEntry",
    "CapabilityLedger",
    "CapabilityProposal",
    "capability_entry_from_blocked_action",
    "capability_setup_proposal",
    "get_capability_ledger",
]
