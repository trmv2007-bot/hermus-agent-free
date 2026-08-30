"""Canonical Android control facade — the ONE public Android boundary (§16).

``AndroidTool`` is the single owner of the Android capability surface. Tool calls
reach it through ``ToolGateway`` -> ``android_*`` tools -> :meth:`AndroidTool.run`.
Every operation:

1. is authorized by :class:`~core.android.permissions.AndroidPermissionManager`
   (explicit consent + configurable allowed-ops allowlist; missing consent is
   DENIED, never granted by default);
2. dispatches to a real transport (:class:`~core.android.transport.AdbAndroidTransport`
   or the companion bridge);
3. is audited to the local log + canonical EventBus; and
4. reports ``android_control_unavailable`` with an explicit reason (and
   ``"ok": False``) instead of fabricating success, when a device/permission is
   missing.
"""
from __future__ import annotations

from typing import Any, Optional

from .audit import record
from .permissions import OP_CLASSES, PermissionDenied, get_permission_manager
from .transport import AndroidTransport, AndroidUnavailable, detect_capability

_OP_ALIASES = {"type": "type_text", "connect": "connect", "get_screen": "get_screen",
               "get_ui_tree": "get_ui_tree", "tap": "tap", "back": "back",
               "launch_app": "launch_app", "current_app": "current_app",
               "observe": "_observe_transport"}

#: composite ops handled at the facade (not a single transport method)
_COMPOSITE_OPS = {"observe"}


class AndroidTool:
    def __init__(self, *, transport: Optional[AndroidTransport] = None,
                 permissions: Any = None):
        self._transport = transport
        self._permissions = permissions or get_permission_manager()

    @property
    def transport(self) -> Optional[AndroidTransport]:
        return self._transport

    def _authorize(self, op: str) -> str:
        return self._permissions.require_access(op)

    def observe(self, **kw) -> dict[str, Any]:
        """Semantic observation: UI hierarchy + screenshot + a model-friendly summary.

        This is the primary intelligence path (§7) — the agent reasons about visible
        text/buttons/fields and labels, not raw coordinates. Accessible even when only
        ``ui_control``/``screen_capture`` consent is present (observation is read-only).
        """
        if self._transport is None:
            cap = detect_capability()
            return {"ok": False, "error": "android_control_unavailable",
                    "reason": cap.get("reason") or "no Android transport configured"}
        from .observe import build_observation
        try:
            tree = self._transport.get_ui_tree(**kw)
            screen = self._transport.get_screen(**kw)
            return {"ok": True, **build_observation(tree, screen=screen)}
        except AndroidUnavailable as exc:
            return {"ok": False, "error": "android_control_unavailable", "reason": exc.reason}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "android_control_unavailable",
                    "reason": f"{type(exc).__name__}: {exc}"[:300]}

    def _dispatch(self, op: str, args: dict[str, Any]) -> dict[str, Any]:
        if self._transport is None:
            cap = detect_capability()
            raise AndroidUnavailable(cap.get("reason") or "no Android transport configured",
                                     category="device", op=op)
        method = getattr(self._transport, _OP_ALIASES[op], None)
        if method is None:
            raise AndroidUnavailable(f"transport does not implement '{op}'",
                                     category="op", op=op)
        return method(**args)

    def run(self, op: str, args: dict[str, Any], *, trace_id: Optional[str] = None,
            device: Optional[str] = None, mission_id: Optional[str] = None,
            run_id: Optional[str] = None) -> dict[str, Any]:
        """Execute one authorized Android operation.

        Returns an honest result dict. On any authorization/availability failure it
        returns ``{"ok": False, "error": "android_control_unavailable",
        "reason": ...}`` — it never fabricates a success.
        """
        if op not in _OP_ALIASES:
            return {"ok": False, "error": "android_control_unavailable",
                    "reason": f"unknown op '{op}' (choose {sorted(_OP_ALIASES)})"}
        # Authorize FIRST (no covert path): consent + allowlist.
        try:
            op_class = self._authorize(op)
        except PermissionDenied as exc:
            reason = exc.reason
            record(op, args, ok=False, reason=reason, device=device,
                   op_class=exc.category, trace_id=trace_id, mission_id=mission_id,
                   run_id=run_id)
            return {"ok": False, "error": "android_control_unavailable", "reason": reason}
        try:
            if op in _COMPOSITE_OPS:
                result = self.observe(**args)
            else:
                result = self._dispatch(op, args)
        except AndroidUnavailable as exc:
            reason = exc.reason
            record(op, args, ok=False, reason=reason, device=device, op_class=op_class,
                   trace_id=trace_id, mission_id=mission_id, run_id=run_id)
            return {"ok": False, "error": "android_control_unavailable", "reason": reason}
        except PermissionDenied as exc:
            reason = exc.reason
            record(op, args, ok=False, reason=reason, device=device, op_class=op_class,
                   trace_id=trace_id, mission_id=mission_id, run_id=run_id)
            return {"ok": False, "error": "android_control_unavailable", "reason": reason}
        except Exception as exc:  # noqa: BLE001 - surface everything honestly
            reason = f"{type(exc).__name__}: {exc}"[:300]
            record(op, args, ok=False, reason=reason, device=device, op_class=op_class,
                   trace_id=trace_id, mission_id=mission_id, run_id=run_id)
            return {"ok": False, "error": "android_control_unavailable", "reason": reason}
        ok = bool(result.get("ok", True))
        record(op, args, ok=ok, result=result, device=device, op_class=op_class,
               trace_id=trace_id, mission_id=mission_id, run_id=run_id)
        return result

    # -- canonical surface (the ops spec §17 requires) ------------------------
    def connect(self, **kw) -> dict[str, Any]:
        return self.run("connect", kw)

    def get_screen(self, **kw) -> dict[str, Any]:
        return self.run("get_screen", kw)

    def get_ui_tree(self, **kw) -> dict[str, Any]:
        return self.run("get_ui_tree", kw)

    def tap(self, x: int, y: int, **kw) -> dict[str, Any]:
        return self.run("tap", {"x": int(x), "y": int(y), **kw})

    def type(self, text: str, **kw) -> dict[str, Any]:
        return self.run("type", {"text": text, **kw})

    def back(self, **kw) -> dict[str, Any]:
        return self.run("back", kw)

    def launch_app(self, package: str, **kw) -> dict[str, Any]:
        return self.run("launch_app", {"package": package, **kw})

    def capability(self) -> dict[str, Any]:
        """Truthful capability report (never claims availability if unproven)."""
        cap = detect_capability(transport=self._transport)
        cap["consented_ops"] = self._permissions.allowed_ops()
        cap["consent"] = {cls: self._permissions.is_consented(cls)
                          for cls in OP_CLASSES}
        return cap


#: process-wide canonical instance (single Android boundary)
_android_tool: Optional[AndroidTool] = None


def get_android_tool() -> AndroidTool:
    global _android_tool
    if _android_tool is None:
        _android_tool = AndroidTool()
    return _android_tool
