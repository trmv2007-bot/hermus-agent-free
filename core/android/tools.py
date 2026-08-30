"""Register the Android control tools into the canonical registry (§16).

These are the only Android entry points the agent may call; every one delegates to
the :class:`~core.android.tool.AndroidTool` facade, which enforces consent +
allowlist + audit and reports ``android_control_unavailable`` honestly.
Consent tools are explicit user-authorization surfaces, not covert actions.
"""
from __future__ import annotations

from typing import Any

from .permissions import OP_CLASSES
from .tool import get_android_tool


def _tool_def(name: str, desc: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": params}}


def register_android_tools(registry, tool=None) -> None:
    tool = tool or get_android_tool()
    perms = tool._permissions

    # -- device control (permission-gated, audited) --------------------------
    def android_connect() -> dict[str, Any]:
        return tool.connect()

    def android_get_screen() -> dict[str, Any]:
        return tool.get_screen()

    def android_get_ui_tree() -> dict[str, Any]:
        return tool.get_ui_tree()

    def android_tap(x: int, y: int) -> dict[str, Any]:
        return tool.tap(x, y)

    def android_type(text: str) -> dict[str, Any]:
        return tool.type(text)

    def android_back() -> dict[str, Any]:
        return tool.back()

    def android_launch_app(package: str) -> dict[str, Any]:
        return tool.launch_app(package)

    def android_observe() -> dict[str, Any]:
        # Consent-gated composite observation (screen_capture class).
        return tool.run("observe", {})

    def android_current_app() -> dict[str, Any]:
        return tool.run("current_app", {})

    def android_capability() -> dict[str, Any]:
        return tool.capability()

    # -- explicit consent / allowlist (user authorization, not covert) --------
    def android_permission_grant(op_class: str) -> dict[str, Any]:
        if op_class not in OP_CLASSES:
            return {"ok": False, "error": f"unknown class '{op_class}' (choose {OP_CLASSES})"}
        perms.grant(op_class)
        return {"ok": True, "granted": op_class}

    def android_permission_revoke(op_class: str) -> dict[str, Any]:
        perms.revoke(op_class)
        return {"ok": True, "revoked": op_class}

    def android_permission_status() -> dict[str, Any]:
        return {"ok": True,
                "consent": {c: perms.is_consented(c) for c in OP_CLASSES},
                "allowed_ops": perms.allowed_ops()}

    def android_permission_set_ops(ops: list[str]) -> dict[str, Any]:
        try:
            perms.set_allowed_ops(list(ops))
            return {"ok": True, "allowed_ops": perms.allowed_ops()}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    registry.register("android_capability", android_capability, _tool_def(
        "android_capability",
        "Report whether Android control is available and what the user has consented to. "
        "Returns {available, reason, transport, consent, allowed_ops}.",
        {"type": "object", "properties": {}}), source="core.android")

    registry.register("android_connect", android_connect, _tool_def(
        "android_connect",
        "Connect to the Android device/bridge. Requires 'device_info' consent.",
        {"type": "object", "properties": {}}), source="core.android")

    registry.register("android_get_screen", android_get_screen, _tool_def(
        "android_get_screen",
        "Capture the device screen (screencap). Requires 'screen_capture' consent.",
        {"type": "object", "properties": {}}), source="core.android")

    registry.register("android_get_ui_tree", android_get_ui_tree, _tool_def(
        "android_get_ui_tree",
        "Dump the accessibility UI tree (uiautomator). Requires 'ui_control' consent.",
        {"type": "object", "properties": {}}), source="core.android")

    registry.register("android_tap", android_tap, _tool_def(
        "android_tap",
        "Tap at (x, y) on the device. Requires 'ui_control' consent.",
        {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
         "required": ["x", "y"]}), source="core.android")

    registry.register("android_type", android_type, _tool_def(
        "android_type",
        "Type text into the focused field. Requires 'ui_control' consent.",
        {"type": "object", "properties": {"text": {"type": "string"}},
         "required": ["text"]}), source="core.android")

    registry.register("android_back", android_back, _tool_def(
        "android_back",
        "Send BACK keyevent. Requires 'ui_control' consent.",
        {"type": "object", "properties": {}}), source="core.android")

    registry.register("android_launch_app", android_launch_app, _tool_def(
        "android_launch_app",
        "Launch an app by package/activity. Requires 'launch_app' consent.",
        {"type": "object", "properties": {"package": {"type": "string"}},
         "required": ["package"]}), source="core.android")

    registry.register("android_observe", android_observe, _tool_def(
        "android_observe",
        "Observe the device screen semantically: visible text, buttons, fields, labels, "
        "bounds, focused/enabled state, package + screenshot reference. The model should "
        "reason from this (\"There are three buttons; the target is the one labeled X\") "
        "rather than raw coordinates. Requires 'screen_capture' consent.",
        {"type": "object", "properties": {}}), source="core.android")

    registry.register("android_current_app", android_current_app, _tool_def(
        "android_current_app",
        "Report the foreground application/package. Requires 'device_info' consent.",
        {"type": "object", "properties": {}}), source="core.android")

    registry.register("android_permission_grant", android_permission_grant, _tool_def(
        "android_permission_grant",
        "Explicitly grant the user's consent for an op class "
        "(screen_capture/ui_control/launch_app/device_info/notification).",
        {"type": "object", "properties": {"op_class": {"type": "string"}},
         "required": ["op_class"]}), source="core.android")

    registry.register("android_permission_revoke", android_permission_revoke, _tool_def(
        "android_permission_revoke",
        "Revoke the user's consent for an op class.",
        {"type": "object", "properties": {"op_class": {"type": "string"}},
         "required": ["op_class"]}), source="core.android")

    registry.register("android_permission_status", android_permission_status, _tool_def(
        "android_permission_status",
        "Show current consent and allowed-ops state.",
        {"type": "object", "properties": {}}), source="core.android")

    registry.register("android_permission_set_ops", android_permission_set_ops, _tool_def(
        "android_permission_set_ops",
        "Configure the allowed-ops allowlist (connect,get_screen,get_ui_tree,tap,type,back,launch_app).",
        {"type": "object", "properties": {"ops": {"type": "array", "items": {"type": "string"}}},
         "required": ["ops"]}), source="core.android")
