"""Computer-control tools exposed to the agent via ToolGateway (§14).

These are the only agent-facing computer entry points. Every action honours the
honest capability check: if real control (pyautogui/pygetwindow) is unavailable and
the caller did not explicitly pass ``allow_dry_run``, the action returns
``{ok: False, error: 'computer_control_unavailable', reason}`` — it never silently
pretends a dry-run fallback is real success. Explicit ``allow_dry_run=True`` is the
offline audit/test mode.

``computer_capability`` reports the truthful availability state so the autonomy
layer can decline up front rather than act blind.
"""
from __future__ import annotations

from typing import Any

from .controller import ComputerActionController, detect_computer_capability

# action -> (controller method, arg schema keys)
_ACTIONS = {
    "move_mouse": ("move_mouse", ["x", "y"]),
    "click": ("click", ["x", "y"]),
    "double_click": ("double_click", ["x", "y"]),
    "right_click": ("right_click", ["x", "y"]),
    "scroll": ("scroll", ["amount"]),
    "type": ("type_text", ["text"]),
    "press_key": ("press_key", ["key"]),
    "hotkey": ("hotkey", ["keys"]),
    "open_application": ("open_application", ["name"]),
    "close_application": ("close_application", ["name"]),
    "focus_window": ("focus_window", ["name"]),
    "find_on_screen": ("find_on_screen", ["target"]),
}


def _tool_def(name: str, desc: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": params}}


def register_computer_tools(registry) -> None:
    def computer_capability() -> dict[str, Any]:
        return detect_computer_capability()

    def computer_action(action: str, args: dict[str, Any] | None = None,
                        allow_dry_run: bool = False) -> dict[str, Any]:
        if action not in _ACTIONS:
            return {"ok": False, "error": "unknown_computer_action",
                    "reason": f"unknown action '{action}' (choose {sorted(_ACTIONS)})"}
        method_name, schema = _ACTIONS[action]
        kwargs = {k: (args or {}).get(k) for k in schema}
        if any(v is None for v in kwargs.values()):
            return {"ok": False, "error": "missing_args",
                    "reason": f"action '{action}' requires {schema}"}
        ctl = ComputerActionController()
        cap = ctl.backend_capability()
        if not cap["available"] and not bool(allow_dry_run):
            return {"ok": False, "error": "computer_control_unavailable",
                    "reason": cap["reason"], "capability": cap}
        method = getattr(ctl, method_name)
        if method_name == "hotkey":
            result = method(*tuple(kwargs["keys"]))
        else:
            result = method(**kwargs)
        result = dict(result or {})
        result["allow_dry_run"] = bool(allow_dry_run)
        return result

    registry.register("computer_capability", computer_capability, _tool_def(
        "computer_capability",
        "Report whether real computer control is available (pyautogui + display) vs "
        "dry-run only, with an exact reason. Returns {available, dry_run_only, reason, backends}.",
        {"type": "object", "properties": {}}), source="core.computer")

    registry.register("computer_action", computer_action, _tool_def(
        "computer_action",
        "Perform a computer-control action (move_mouse/click/type/press_key/hotkey/"
        "open_application/find_on_screen/...). Returns computer_control_unavailable "
        "with a reason when real control is unavailable (pass allow_dry_run=True only "
        "for offline audit/testing).",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": sorted(_ACTIONS)},
            "args": {"type": "object"},
            "allow_dry_run": {"type": "boolean", "default": False},
        }, "required": ["action"]}), source="core.computer")
