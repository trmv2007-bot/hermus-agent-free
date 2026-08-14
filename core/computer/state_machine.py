"""Visual state machine — drive a desktop task as a graph of screen states.

Rather than reasoning only in messages, a task is expressed as a sequence of
states, each with the visual condition that confirms it was reached, an action
to advance, and pass/fail transitions.  The runner reuses the watcher (for
``wait_until`` semantics) and the verifier (for before/after confirmation), so
a failed action is diagnosed and repaired instead of blindly repeated.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


@dataclass
class VisualState:
    name: str
    expected: str = ""                      # visual condition confirming this state
    action: Optional[Dict[str, Any]] = None  # {"kind": ..., ...} action to advance
    on_success: Optional[str] = None        # next state name on pass
    on_failure: Optional[str] = None        # next state name on fail (default: retry)
    terminal: bool = False                  # SUCCESS (or FAILURE) end state

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def dispatch_action(controller: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Map an action spec to a :class:`ComputerActionController` call."""
    kind = spec.get("kind") or spec.get("action") or ""
    target = spec.get("target")
    if kind in ("click_target", "click_button"):
        return controller.click_target(target or spec.get("description", ""))
    if kind == "click":
        return controller.click(spec["x"], spec["y"])
    if kind == "double_click":
        return controller.double_click(spec["x"], spec["y"])
    if kind == "right_click":
        return controller.right_click(spec["x"], spec["y"])
    if kind == "type_text":
        return controller.type_text(spec.get("text", ""))
    if kind == "press_key":
        return controller.press_key(spec.get("key", ""))
    if kind == "hotkey":
        return controller.hotkey(*spec.get("keys", []))
    if kind == "scroll":
        return controller.scroll(spec.get("amount", 0), spec.get("x"), spec.get("y"))
    if kind == "open_application":
        return controller.open_application(spec.get("name", ""))
    if kind == "close_application":
        return controller.close_application(spec.get("name", ""))
    if kind == "focus_window":
        return controller.focus_window(spec.get("name", ""))
    if kind in ("move_mouse", "move"):
        return controller.move_mouse(spec.get("x", 0), spec.get("y", 0))
    return {"ok": False, "error": f"unknown action kind: {kind}", "action": kind}


class VisualStateMachine:
    """Execute a list of :class:`VisualState` steps with verify/repair."""

    def __init__(
        self,
        controller: Any = None,
        recorder: Any = None,
        wait_until: Optional[Callable[[str, float], Dict[str, Any]]] = None,
        execute: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        verify: Optional[Callable[[Any, Any, str], Dict[str, Any]]] = None,
        max_retries: int = 2,
    ):
        self.controller = controller
        self.recorder = recorder
        self.wait_until = wait_until or (lambda _c, _t: {"matched": True, "success": True})
        self.execute = execute or (lambda spec: dispatch_action(self.controller, spec) if self.controller else {"ok": False, "error": "no controller"})
        self.verify = verify or (lambda before, after, expected: {"ok": True, "detail": "no verifier", "confidence": 0.0})
        self.max_retries = max(0, int(max_retries))

    def _capture(self) -> Any:
        if self.recorder is not None:
            return self.recorder.capture_now(store=True)
        return None

    def run(self, states: List[VisualState], timeout_per_state: float = 30.0) -> Dict[str, Any]:
        if not states:
            return {"success": False, "error": "no states provided", "trace": []}
        by_name = {state.name: state for state in states}
        visited: List[Dict[str, Any]] = []
        current = states[0]
        guard = 0
        max_steps = len(states) * (self.max_retries + 2) + 4
        while guard < max_steps:
            guard += 1
            if current.terminal:
                success = current.name.upper() != "FAILURE"
                visited.append({"state": current.name, "terminal": True, "success": success})
                return {"success": success, "states_visited": visited, "final_state": current.name}

            # 1. Confirm we are in this state (wait for its expected condition).
            if current.expected:
                condition = self.wait_until(current.expected, timeout_per_state)
                if not condition.get("matched"):
                    fallback = current.on_failure
                    visited.append({"state": current.name, "wait": "timeout", "detail": condition.get("detail", "")})
                    if fallback and fallback in by_name:
                        current = by_name[fallback]
                        continue
                    return {"success": False, "states_visited": visited, "final_state": current.name,
                            "error": f"state '{current.name}' never reached: {condition.get('detail', 'timeout')}"}
            visited.append({"state": current.name, "wait": "ok"})

            # 2. Execute the action (with retry intelligence on failure).
            if current.action is None:
                nxt = current.on_success
                current = by_name[nxt] if nxt and nxt in by_name else current
                continue

            before = self._capture()
            executed = None
            for attempt in range(self.max_retries + 1):
                executed = self.execute(current.action)
                after = self._capture()
                verification = self.verify(before, after, current.expected or "")
                visited.append({
                    "state": current.name,
                    "action": executed,
                    "attempt": attempt + 1,
                    "verification": verification,
                })
                if executed.get("ok") and verification.get("ok"):
                    break
                # Diagnose: the failed verification itself is the hint for the
                # next attempt, so the loop can re-locate and re-act rather
                # than blindly repeating the same coordinates.
            if executed is not None and executed.get("ok") and verification.get("ok"):
                nxt = current.on_success
                current = by_name[nxt] if nxt and nxt in by_name else current
            else:
                fallback = current.on_failure
                current = by_name[fallback] if fallback and fallback in by_name else current
                if not (fallback and fallback in by_name):
                    return {"success": False, "states_visited": visited, "final_state": current.name,
                            "error": f"state '{current.name}' failed after {self.max_retries + 1} attempt(s)"}

        return {"success": False, "states_visited": visited, "final_state": current.name,
                "error": "state machine exceeded step guard (possible loop)"}

    @staticmethod
    def plan_to_states(plan: List[Dict[str, Any]], terminal: str = "SUCCESS") -> List[VisualState]:
        """Convert a list of plan step dicts into a linear state chain."""
        states: List[VisualState] = []
        for index, step in enumerate(plan):
            name = step.get("name") or f"STATE {index}"
            states.append(VisualState(
                name=name,
                expected=step.get("expected", ""),
                action=step.get("action"),
                on_success=(step.get("on_success") or
                            (plan[index + 1].get("name") if index + 1 < len(plan) else terminal)),
            ))
        states.append(VisualState(name=terminal, terminal=True))
        return states
