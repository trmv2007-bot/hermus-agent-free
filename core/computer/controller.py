"""Computer action engine — the desktop primitives the agent drives.

:class:`ComputerActionController` is the single gate through which every mouse,
keyboard and window action flows.  It enforces the emergency stop and the
:class:`ComputerPolicy` risk tiers (optionally also the global
``core.permissions`` tool gate), then emits a structured action record suitable
for the task timeline and for the verify/repair loop.  All three backends are
injectable, so the engine degrades to an auditable dry-run when no display or
input library is present.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .keyboard import KeyboardBackend, default_keyboard
from .mouse import MouseBackend, default_mouse
from .permissions import ComputerPolicy, EmergencyStop, computer_policy, emergency_stop
from .target_detector import TargetDetector
from .window_manager import WindowBackend, default_window_manager


def _now() -> str:
    return datetime.now().astimezone().isoformat()


class ComputerActionController:
    """Gated, injectable desktop action engine."""

    def __init__(
        self,
        mouse: Optional[MouseBackend] = None,
        keyboard: Optional[KeyboardBackend] = None,
        window_manager: Optional[WindowBackend] = None,
        policy: Optional[ComputerPolicy] = None,
        permissions: Optional[Any] = None,
        emergency: Optional[EmergencyStop] = None,
        frame_provider: Optional[Callable[[], Any]] = None,
        target_detector: Optional[TargetDetector] = None,
        scope: str = "default",
        approval: Optional[Any] = None,
    ):
        self.mouse = mouse or default_mouse()
        self.keyboard = keyboard or default_keyboard()
        self.windows = window_manager or default_window_manager()
        self.policy = policy or computer_policy
        self.permissions = permissions  # optional core.permissions.PermissionManager
        self.emergency = emergency or emergency_stop
        self.frame_provider = frame_provider
        self.target_detector = target_detector or TargetDetector()
        self.scope = scope
        # Remote approval gate.  Defaults to the shared module singleton so the
        # default ComputerAgent picks up remote approval automatically when the
        # gateway enables it; it is a no-op until enabled.
        if approval is None:
            from .remote import remote_approval

            approval = remote_approval
        self.approval = approval
        self.history: List[Dict[str, Any]] = []

    # -- safety ---------------------------------------------------------
    def _gate(self, action: str, args: Dict[str, Any]) -> Dict[str, Any]:
        halt = self.emergency.check()
        if halt.get("halted"):
            return {"allowed": False, "decision": "deny", "risk": "n/a", "reason": halt["error"]}
        policy = self.policy.check(action, args, scope=self.scope)
        if not policy.get("allowed"):
            return {**policy, "allowed": False, "decision": "deny"}
        decision = "allow"
        if self.permissions is not None:
            try:
                gate = self.permissions.check(f"computer_{action}", agent=self.scope, args=args)
                if gate.get("decision") == "deny":
                    return {"allowed": False, "decision": "deny", "risk": policy["risk"], "reason": "permission policy denied"}
                if gate.get("decision") == "ask":
                    decision = "ask"
            except Exception:  # noqa: BLE001
                pass
        # Remote approval gate (no-op unless the gateway enables it).
        approval = getattr(self, "approval", None)
        if approval is not None:
            try:
                gate = approval.check(action, args, risk=policy.get("risk"))
                if gate.get("pending"):
                    return {"allowed": False, "decision": "ask", "risk": policy.get("risk"),
                            "reason": gate.get("reason"), "prompt_id": gate.get("prompt_id")}
            except Exception:  # noqa: BLE001
                pass
        return {"allowed": True, "decision": decision, "risk": policy["risk"], "reason": "ok"}

    # -- action record helpers ------------------------------------------
    def _record(
        self,
        action: str,
        description: str,
        args: Dict[str, Any],
        gate: Dict[str, Any],
        backend_result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        backend_result = backend_result or {}
        record: Dict[str, Any] = {
            "ok": bool(backend_result.get("ok")) and not error,
            "action": action,
            "description": description,
            "args": args,
            "ts": _now(),
            "backend": backend_result.get("backend") or getattr(self._backend_for(action), "name", ""),
            "dry_run": bool(backend_result.get("dry_run")),
            "risk": gate.get("risk"),
            "decision": gate.get("decision"),
            "detail": backend_result.get("detail") or backend_result.get("error") or error or "",
        }
        if gate.get("prompt_id"):
            record["prompt_id"] = gate["prompt_id"]
            record["approval_required"] = True
        if error:
            record["error"] = error
        self.history.append(record)
        return record

    def _backend_for(self, action: str) -> Any:
        if action in ("move_mouse", "click", "double_click", "right_click", "scroll"):
            return self.mouse
        if action in ("type_text", "press_key", "hotkey"):
            return self.keyboard
        return self.windows

    def _perform(
        self,
        action: str,
        description: str,
        args: Dict[str, Any],
        fn: Callable[[], Dict[str, Any]],
    ) -> Dict[str, Any]:
        gate = self._gate(action, args)
        if not gate.get("allowed"):
            return self._record(action, description, args, gate, error=gate.get("reason"))
        try:
            result = fn()
            if result.get("dry_run"):
                result.setdefault("ok", True)
            result["backend"] = self._backend_for(action).name
            return self._record(action, description, args, gate, backend_result=result)
        except Exception as exc:  # noqa: BLE001
            return self._record(action, description, args, gate, error=str(exc))

    # -- mouse ----------------------------------------------------------
    def move_mouse(self, x: float, y: float) -> Dict[str, Any]:
        return self._perform("move_mouse", f"Move mouse to ({x}, {y})", {"x": x, "y": y},
                             lambda: self.mouse.move(x, y))

    def click(self, x: float, y: float, button: str = "left") -> Dict[str, Any]:
        return self._perform("click", f"Click ({x}, {y})", {"x": x, "y": y, "button": button},
                             lambda: self.mouse.click(x, y, button=button))

    def double_click(self, x: float, y: float) -> Dict[str, Any]:
        return self._perform("double_click", f"Double-click ({x}, {y})", {"x": x, "y": y},
                             lambda: self.mouse.click(x, y, clicks=2))

    def right_click(self, x: float, y: float) -> Dict[str, Any]:
        return self._perform("right_click", f"Right-click ({x}, {y})", {"x": x, "y": y},
                             lambda: self.mouse.click(x, y, button="right"))

    def scroll(self, amount: float, x: Optional[float] = None, y: Optional[float] = None) -> Dict[str, Any]:
        return self._perform("scroll", f"Scroll {amount}", {"amount": amount, "x": x, "y": y},
                             lambda: self.mouse.scroll(amount, x=x, y=y))

    # -- keyboard -------------------------------------------------------
    def type_text(self, text: str, interval: float = 0.0) -> Dict[str, Any]:
        return self._perform("type_text", f"Type text ({len(text)} chars)", {"text": text, "interval": interval},
                             lambda: self.keyboard.type_text(text, interval=interval))

    def press_key(self, key: str) -> Dict[str, Any]:
        return self._perform("press_key", f"Press key {key}", {"key": key},
                             lambda: self.keyboard.press(key))

    def hotkey(self, *keys: str) -> Dict[str, Any]:
        return self._perform("hotkey", f"Hotkey {'+'.join(keys)}", {"keys": list(keys)},
                             lambda: self.keyboard.hotkey(*keys))

    # -- windows / applications -----------------------------------------
    def open_application(self, name: str) -> Dict[str, Any]:
        return self._perform("open_application", f"Open application {name}", {"name": name},
                             lambda: self.windows.open_application(name))

    def close_application(self, name: str) -> Dict[str, Any]:
        return self._perform("close_application", f"Close application {name}", {"name": name},
                             lambda: self.windows.close_application(name))

    def focus_window(self, name: str) -> Dict[str, Any]:
        return self._perform("focus_window", f"Focus window {name}", {"name": name},
                             lambda: self.windows.focus_window(name))

    # -- vision-driven --------------------------------------------------
    def find_on_screen(self, target: str) -> Dict[str, Any]:
        """Locate a described UI target and return its screen coordinates."""
        args = {"target": target}
        gate = self._gate("find_on_screen", args)
        if not gate.get("allowed"):
            return self._record("find_on_screen", f"Find '{target}'", args, gate, error=gate.get("reason"))
        if self.frame_provider is None:
            return self._record("find_on_screen", f"Find '{target}'", args, gate,
                                error="no frame provider configured for the controller")
        frame = self.frame_provider()
        if frame is None:
            return self._record("find_on_screen", f"Find '{target}'", args, gate,
                                error="could not capture a screen frame")
        detection = self.target_detector.find_on_screen(frame, target)
        detection["action"] = "find_on_screen"
        detection["ts"] = _now()
        detection["risk"] = gate.get("risk")
        detection["decision"] = gate.get("decision")
        detection["ok"] = bool(detection.get("found"))
        self.history.append(detection)
        return detection

    def click_target(self, target: str) -> Dict[str, Any]:
        """Vision-driven click: locate ``target`` on screen, then click it."""
        detection = self.find_on_screen(target)
        if not detection.get("found"):
            return {**detection, "action": "click_target", "ok": False,
                    "error": detection.get("description") or f"target not found: {target}"}
        click = self.click(detection["x"], detection["y"])
        return {**click, "action": "click_target", "target": target,
                "located_x": detection["x"], "located_y": detection["y"],
                "confidence": detection.get("confidence", 0.0)}
