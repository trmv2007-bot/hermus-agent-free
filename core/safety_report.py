"""Autonomy Safety Report generation.

This module turns Hermus' safety control-plane state into a human-readable report:
pending approvals, active grants, blocked missions, recent policy/safety events,
capability discoveries, and emergency-stop state/history. It is read/reporting
only; generating a report never grants or activates a power.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .contracts import CommandStatus, EventEnvelope, EventType

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = ROOT / "docs" / "safety_reports"

SAFETY_COMMAND_PREFIXES = (
    "permission.", "capability.", "emergency.stop", "mission_", "mission.", "safety.",
)
SAFETY_EVENT_TYPES = {"permission.checked", "state.changed", "command.failed"}
SAFETY_ERROR_CODES = {"APPROVAL_REQUIRED", "POLICY_DENIED", "PERMISSION_DENIED"}


def is_safety_event(event: dict[str, Any]) -> bool:
    command = str(event.get("command") or "")
    event_type = str(event.get("type") or "")
    args = event.get("args_redacted") if isinstance(event.get("args_redacted"), dict) else {}
    if command.startswith(SAFETY_COMMAND_PREFIXES):
        return True
    if event_type in SAFETY_EVENT_TYPES and any(k in args for k in (
        "safety", "approval_request", "emergency_stop", "red_lines", "permission", "proposal",
    )):
        return True
    if event.get("error_code") in SAFETY_ERROR_CODES:
        return True
    return False


@dataclass(frozen=True)
class SafetyReport:
    generated_at: str
    emergency_stop: dict[str, Any]
    pending_approvals: list[dict[str, Any]]
    active_grants: list[dict[str, Any]]
    blocked_missions: list[dict[str, Any]]
    capability_discoveries: list[dict[str, Any]]
    local_defense_reports: list[dict[str, Any]]
    recent_safety_events: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["summary"] = self.summary()
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "emergency_active": bool(self.emergency_stop.get("active")),
            "pending_approvals": len(self.pending_approvals),
            "active_grants": len(self.active_grants),
            "blocked_missions": len(self.blocked_missions),
            "capability_discoveries": len(self.capability_discoveries),
            "local_defense_reports": len(self.local_defense_reports),
            "recent_safety_events": len(self.recent_safety_events),
        }

    def to_markdown(self) -> str:
        s = self.summary()
        lines: list[str] = [
            "# Hermus Autonomy Safety Report",
            "",
            f"Generated: {self.generated_at}",
            "",
            "## Summary",
            "",
            f"- Emergency brake active: {s['emergency_active']}",
            f"- Pending approvals: {s['pending_approvals']}",
            f"- Active scoped grants: {s['active_grants']}",
            f"- Blocked missions: {s['blocked_missions']}",
            f"- Capability discoveries: {s['capability_discoveries']}",
            f"- Local defense reports: {s['local_defense_reports']}",
            f"- Recent safety events: {s['recent_safety_events']}",
            "",
            "## Emergency stop",
            "",
            f"- Active: {self.emergency_stop.get('active')}",
            f"- Reason: {self.emergency_stop.get('reason') or ''}",
            f"- Set by: {self.emergency_stop.get('set_by') or ''}",
            f"- Updated: {self.emergency_stop.get('updated_at') or ''}",
            "",
            "## Pending approvals",
            "",
        ]
        lines += _table(
            self.pending_approvals,
            ["id", "tool", "red_lines", "suggested_resources", "suggested_purpose", "created_at"],
            empty="No pending approvals.",
        )
        lines += ["", "## Active scoped grants", ""]
        lines += _table(
            self.active_grants,
            ["id", "tool", "red_lines", "resources", "purpose", "uses", "max_uses", "expires_at"],
            empty="No active scoped grants.",
        )
        lines += ["", "## Blocked missions", ""]
        lines += _table(
            self.blocked_missions,
            ["mission_id", "state", "progress_pct", "approval_id", "blocker_reason"],
            empty="No blocked missions.",
        )
        lines += ["", "## Capability discoveries", ""]
        lines += _table(
            self.capability_discoveries,
            ["power", "status", "use", "risk", "needed_approval_setup"],
            empty="No discovered/missing powers recorded.",
        )
        lines += ["", "## Local defense reports", ""]
        lines += _table(
            self.local_defense_reports,
            ["name", "updated_at", "size_bytes", "url"],
            empty="No local defense scan reports.",
        )
        lines += ["", "## Recent safety events", ""]
        if self.recent_safety_events:
            for event in self.recent_safety_events:
                label = _event_label(event)
                lines.append(f"- `{event.get('timestamp') or ''}` **{label}** status={event.get('status') or ''}")
        else:
            lines.append("No recent safety events.")
        lines += [
            "",
            "## Rule",
            "",
            "This report is evidence and review material. It does not grant, activate,",
            "or authorize any capability. Yellow actions still require scoped approval;",
            "red actions remain blocked.",
            "",
        ]
        return "\n".join(lines)


def generate_safety_report(*, event_limit: int = 80) -> SafetyReport:
    return SafetyReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        emergency_stop=_safe_emergency_state(),
        pending_approvals=_safe_pending_approvals(),
        active_grants=_safe_active_grants(),
        blocked_missions=_safe_blocked_missions(),
        capability_discoveries=_safe_capability_discoveries(),
        local_defense_reports=_safe_local_defense_reports(),
        recent_safety_events=_safe_safety_events(event_limit),
    )


def write_safety_report(report: Optional[SafetyReport] = None, *, output: Optional[Path] = None) -> dict[str, Any]:
    report = report or generate_safety_report()
    path = Path(output) if output is not None else DEFAULT_REPORT_DIR / f"autonomy-safety-report-{_stamp()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    markdown = report.to_markdown()
    path.write_text(markdown, encoding="utf-8")
    _publish_report_event(str(path), report)
    return {"success": True, "path": str(path), "report": report.to_dict(), "markdown": markdown}


def _safe_emergency_state() -> dict[str, Any]:
    try:
        from .emergency_stop import get_emergency_stop

        return get_emergency_stop().state().to_dict()
    except Exception as exc:  # noqa: BLE001
        return {"active": True, "reason": f"emergency state unavailable: {exc}", "fail_closed": True}


def _safe_pending_approvals() -> list[dict[str, Any]]:
    try:
        from .permissions import permission_manager

        return permission_manager.approval_pending(include_resolved=False)
    except Exception:
        return []


def _safe_active_grants() -> list[dict[str, Any]]:
    try:
        from .permissions import permission_manager

        return permission_manager.approvals_list(include_inactive=False)
    except Exception:
        return []


def _safe_blocked_missions() -> list[dict[str, Any]]:
    try:
        from .mission import mission_engine

        out = []
        for m in mission_engine.list_missions():
            data = m.to_dict()
            if str(data.get("state", "")).lower() == "blocked" or data.get("approval_request"):
                ap = data.get("approval_request") or {}
                data["approval_id"] = ap.get("id", "")
                out.append({k: data.get(k) for k in ("mission_id", "state", "progress_pct", "approval_id", "blocker_reason")})
        return out
    except Exception:
        return []


def _safe_capability_discoveries() -> list[dict[str, Any]]:
    try:
        from .capability_ledger import get_capability_ledger

        return get_capability_ledger().list_discovered()
    except Exception:
        return []


def _safe_local_defense_reports() -> list[dict[str, Any]]:
    try:
        from .local_defense_scanner import list_scan_reports

        return list_scan_reports(limit=20)
    except Exception:
        return []


def _safe_safety_events(limit: int) -> list[dict[str, Any]]:
    try:
        from .events import get_bus

        max_limit = max(1, min(200, int(limit)))
        events = [e.to_dict() for e in get_bus().recent(limit=max_limit * 4)]
        return [e for e in events if is_safety_event(e)][-max_limit:]
    except Exception:
        return []


def _publish_report_event(path: str, report: SafetyReport) -> None:
    try:
        from .events import get_bus

        get_bus().publish(EventEnvelope(
            type=EventType.STATE_CHANGED.value,
            command="safety.report.written",
            args_redacted={"path": path, "summary": report.summary()},
            status=CommandStatus.SUCCEEDED.value,
        ))
    except Exception:
        pass


def _table(rows: list[dict[str, Any]], columns: list[str], *, empty: str) -> list[str]:
    if not rows:
        return [empty]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        cells = []
        for col in columns:
            value = row.get(col)
            if col == "red_lines" and isinstance(value, dict):
                value = value.get("red_lines", [])
            if isinstance(value, list):
                value = ", ".join(str(x) for x in value)
            cells.append(_cell(value))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("\n", " ").replace("|", "/")[:220]


def _event_label(event: dict[str, Any]) -> str:
    command = event.get("command") or event.get("type") or "event"
    args = event.get("args_redacted") if isinstance(event.get("args_redacted"), dict) else {}
    parts = []
    for key in ("id", "tool", "power", "state", "decision"):
        if args.get(key):
            parts.append(str(args[key]))
    if event.get("mission_id"):
        parts.append("mission " + str(event["mission_id"]))
    if event.get("error_code"):
        parts.append(str(event["error_code"]))
    return str(command) + (" · " + " · ".join(parts) if parts else "")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


__all__ = [
    "SafetyReport",
    "generate_safety_report",
    "is_safety_event",
    "write_safety_report",
]
