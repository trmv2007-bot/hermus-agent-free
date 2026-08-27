"""Agent control center — one-line live status for ``hermus computer``.

Reads the shared controller state (last action, dry-run vs. real backends, and
the global emergency stop) and renders a compact status panel.  It is purely
observational; it never mutates agent state.
"""
from __future__ import annotations

from typing import Any

from .permissions import emergency_stop


class ControlCenter:
    def __init__(self, controller: Any = None, emergency: Any = None):
        self.controller = controller
        self.emergency = emergency or emergency_stop

    def status(self) -> dict[str, Any]:
        history: list[dict[str, Any]] = list(getattr(self.controller, "history", []))
        last_action = history[-1] if history else None
        return {
            "active": not self.emergency.halted,
            "halted": self.emergency.halted,
            "halt_reason": self.emergency.reason,
            "actions": len(history),
            "last_action": last_action,
            "backends": {
                "mouse": getattr(getattr(self.controller, "mouse", None), "name", "?"),
                "keyboard": getattr(getattr(self.controller, "keyboard", None), "name", "?"),
                "windows": getattr(getattr(self.controller, "windows", None), "name", "?"),
            },
        }

    def render(self) -> str:
        s = self.status()
        status = "HALTED" if s["halted"] else "ACTIVE"
        last = s["last_action"]
        last_line = "-"
        verify_line = "-"
        if last:
            last_line = last.get("description") or str(last.get("action"))
            verify_line = "✓" if last.get("ok") else "✗"

        inner = 41

        def row(label: str, value: str) -> str:
            body = f" {label:<16} {value}"[:inner].ljust(inner)
            return "│" + body + "│"

        border = "┌" + "─" * inner + "┐"
        bottom = "└" + "─" * inner + "┘"
        divider = "├" + "─" * inner + "┤"
        title = "│" + " HERMUS COMPUTER ".center(inner) + "│"

        lines = [
            border,
            title,
            divider,
            row("Status:", status),
            row("Actions:", str(s["actions"])),
            row("Last action:", last_line),
            row("Verification:", verify_line),
            row("Mouse backend:", s["backends"]["mouse"]),
            row("Keyboard:", s["backends"]["keyboard"]),
            row("Windows:", s["backends"]["windows"]),
            bottom,
        ]
        if s["halted"] and s.get("halt_reason"):
            lines.append(f"(halted: {s['halt_reason']})")
        return "\n".join(lines)
