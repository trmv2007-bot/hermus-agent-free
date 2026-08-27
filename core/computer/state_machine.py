"""Visual state graph execution with explicit diagnose/repair/retry behavior.

Each non-terminal state executes one desktop action and verifies its expected
post-action screen.  Failures follow one bounded path:

``diagnose -> repair (when available) -> verify repair -> retry original``

If no safe repair is available, only the configured original-action retry is
allowed.  Exhausted, non-retryable, and failed-repair paths terminate with a
structured reason instead of remaining on the same state until a guard trips.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from .task_control import get_task_control, TaskControlState


def _now() -> str:
    return datetime.now().astimezone().isoformat()


@dataclass
class VisualState:
    name: str
    # The visual state that must be true *after* action execution.
    expected: str = ""
    action: Optional[Dict[str, Any]] = None
    on_success: Optional[str] = None
    on_failure: Optional[str] = None
    terminal: bool = False
    # Optional visual gate that must be true before executing the action.
    precondition: str = ""

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


class _EventTrace(list):
    """List that emits every durable state-machine event as it is appended."""

    def __init__(self, callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        super().__init__()
        self.callback = callback

    def append(self, event: Dict[str, Any]) -> None:
        super().append(event)
        if self.callback is not None:
            try:
                self.callback(event)
            except Exception:
                # Checkpoint/telemetry failures must never cause another mouse
                # action or alter the state machine's safety behavior.
                pass


class VisualStateMachine:
    """Execute :class:`VisualState` nodes with bounded verified recovery."""

    def __init__(
        self,
        controller: Any = None,
        recorder: Any = None,
        wait_until: Optional[Callable[[str, float], Dict[str, Any]]] = None,
        execute: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        verify: Optional[Callable[[Any, Any, str], Dict[str, Any]]] = None,
        repair: Optional[Callable[[str, str, Dict[str, Any]], Any]] = None,
        max_retries: int = 2,
        world_state: Any = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_telemetry: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self.controller = controller
        self.recorder = recorder
        self.wait_until = wait_until or (lambda _condition, _timeout: {"matched": True, "success": True})
        self.execute = execute or (
            lambda spec: dispatch_action(self.controller, spec)
            if self.controller else {"ok": False, "error": "no controller"}
        )
        self.verify = verify or (
            lambda _before, _after, _expected: {
                "ok": True,
                "detail": "no verifier",
                "confidence": 0.0,
            }
        )
        self.repair = repair
        self.max_retries = max(0, int(max_retries))
        self.world_state = world_state
        self.on_event = on_event
        self.on_telemetry = on_telemetry

    def _telemetry(self, event_type: str, **data: Any) -> None:
        """Emit transient lifecycle telemetry without affecting execution."""
        if self.on_telemetry is not None:
            try:
                self.on_telemetry(event_type, data)
            except Exception:
                pass

    def _handle_event(self, event: Dict[str, Any]) -> None:
        """Update shared world state, then durably publish the event."""
        if self.world_state is not None:
            try:
                phase = event.get("phase")
                state = str(event.get("state") or "")
                if phase in {"original_action", "repair"}:
                    action_spec = event.get("action_spec") or {}
                    if phase == "original_action" and state:
                        self.world_state.before_action(state, action_spec)
                    verification = event.get("verification")
                    if isinstance(verification, dict):
                        self.world_state.update(
                            {**verification, "task_state": state or self.world_state.task_state},
                            source="repair_verification" if phase == "repair" else "action_verification",
                        )
                    if phase == "original_action" and state:
                        self.world_state.mark_state(
                            state,
                            event.get("outcome") == "success",
                            str(event.get("failure_reason") or ""),
                        )
                elif phase == "diagnose":
                    self.world_state.current_state = f"DIAGNOSE:{state}"
                elif phase == "transition" and event.get("next_state"):
                    self.world_state.current_state = str(event["next_state"])
                elif phase == "terminal":
                    self.world_state.finish_task(bool(event.get("success")))
            except Exception:
                pass
        if self.on_event is not None:
            self.on_event(event)

    def _capture(self) -> Any:
        if self.recorder is not None:
            try:
                return self.recorder.capture_now(store=True)
            except Exception:  # noqa: BLE001
                return None
        return None

    @staticmethod
    def _result_dict(value: Any, action: str = "") -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {
            "ok": False,
            "action": action,
            "error": f"action returned unsupported result: {type(value).__name__}",
        }

    def _execute_action(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Execute one action, including state-machine-native visual waits."""
        kind = str(spec.get("kind") or spec.get("action") or "")
        if kind == "wait_until":
            condition = str(spec.get("condition") or "").strip()
            if not condition:
                return {"ok": False, "action": kind, "error": "wait_until requires a condition"}
            try:
                timeout = max(0.1, float(spec.get("timeout", 30.0)))
            except (TypeError, ValueError):
                timeout = 30.0
            try:
                result = self.wait_until(condition, timeout)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "action": kind, "error": f"wait_until failed: {exc}"}
            result = result if isinstance(result, dict) else {}
            matched = bool(result.get("matched", result.get("success", result.get("ok", False))))
            return {
                "ok": matched,
                "action": kind,
                "description": f"Wait until: {condition}",
                "condition": condition,
                "condition_verified": matched,
                "detail": result.get("detail") or result.get("error") or ("condition matched" if matched else "condition did not match"),
                "confidence": result.get("confidence", 0.0),
                "wait_result": result,
                "ts": _now(),
            }
        try:
            return self._result_dict(self.execute(spec), action=kind)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "action": kind, "error": f"action executor raised: {exc}", "ts": _now()}

    def _verify_action(self, before: Any, after: Any, expected: str, executed: Dict[str, Any]) -> Dict[str, Any]:
        # A successful wait_until already semantically verified its condition;
        # comparing static before/after pixels would incorrectly turn it into a
        # failure when the condition was already present.
        if executed.get("condition_verified"):
            return {
                "ok": True,
                "matched": True,
                "detail": executed.get("detail") or "wait condition matched",
                "confidence": executed.get("confidence", 0.0),
                "expected_state": expected or executed.get("condition", ""),
            }
        try:
            result = self.verify(before, after, expected)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"verifier raised: {exc}", "detail": f"verifier raised: {exc}", "confidence": 0.0}
        if not isinstance(result, dict):
            return {"ok": False, "error": "verifier returned an unsupported result", "confidence": 0.0}
        return result

    @staticmethod
    def _failure_reason(executed: Dict[str, Any], verification: Dict[str, Any]) -> str:
        reasons: List[str] = []
        if not executed.get("ok"):
            action_reason = executed.get("error") or executed.get("detail") or "action execution failed"
            reasons.append(f"action: {action_reason}")
        if not verification.get("ok"):
            verify_reason = verification.get("error") or verification.get("detail") or "visual verification failed"
            reasons.append(f"verification: {verify_reason}")
        return "; ".join(reasons) or "action failed for an unknown reason"

    @staticmethod
    def _normalize_repair(raw: Any) -> Dict[str, Any]:
        """Accept RepairPlan, its dict form, or the legacy list-of-steps API."""
        if raw is None:
            payload: Dict[str, Any] = {}
        elif hasattr(raw, "to_dict") and callable(raw.to_dict):
            converted = raw.to_dict()
            payload = converted if isinstance(converted, dict) else {}
        elif isinstance(raw, dict):
            payload = dict(raw)
        elif isinstance(raw, list):
            payload = {"steps": raw, "retry_original": True, "source": "legacy"}
        else:
            payload = {"reason": f"repair callback returned unsupported result: {type(raw).__name__}"}

        steps = payload.get("steps")
        payload["steps"] = [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []
        diagnosis = payload.get("diagnosis")
        payload["diagnosis"] = diagnosis if isinstance(diagnosis, dict) else {}
        payload["retry_original"] = bool(payload.get("retry_original", True))
        payload["available"] = bool(payload["steps"])
        payload.setdefault("source", "unknown")
        payload.setdefault("reason", "")
        payload.setdefault("plan_id", "")
        return payload

    def _request_repair(
        self,
        state: VisualState,
        attempt: int,
        action_result: Dict[str, Any],
        verification: Dict[str, Any],
        reason: str,
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        if self.repair is None:
            return self._normalize_repair(None), None
        # Preserve top-level action result fields for old callbacks while adding
        # all structured evidence needed by RepairEngine.create_plan.
        context = dict(action_result)
        context.update({
            "state": state.name,
            "attempt": attempt,
            "spec": dict(state.action or {}),
            "result": action_result,
            "verification": verification,
        })
        try:
            return self._normalize_repair(self.repair(reason, state.expected, context)), None
        except Exception as exc:  # noqa: BLE001
            return self._normalize_repair(None), f"repair engine raised: {exc}"

    def _execute_repair(
        self,
        state: VisualState,
        attempt: int,
        plan: Dict[str, Any],
        trace: List[Dict[str, Any]],
    ) -> Tuple[bool, str]:
        plan_id = plan.get("plan_id") or f"repair-{state.name}-{attempt}"
        for index, step in enumerate(plan.get("steps", []), 1):
            action = step.get("action")
            name = str(step.get("name") or f"STEP_{index}")
            if not isinstance(action, dict) or not action:
                reason = f"repair step '{name}' has no executable action"
                trace.append({
                    "state": state.name,
                    "phase": "repair",
                    "repair_state": name,
                    "repair_for": state.name,
                    "repair_plan_id": plan_id,
                    "attempt": attempt,
                    "outcome": "failure",
                    "failure_reason": reason,
                })
                return False, reason

            self._telemetry("screen_event", state=state.name, repair_state=name, attempt=attempt, stage="before_action")
            before = self._capture()
            self._telemetry("action_started", state=state.name, repair_state=name, attempt=attempt, action_spec=action, phase="repair")
            executed = self._execute_action(action)
            self._telemetry("action_completed", state=state.name, repair_state=name, attempt=attempt, action_spec=action, action=executed, phase="repair", ok=bool(executed.get("ok")))
            after = self._capture()
            self._telemetry("screen_event", state=state.name, repair_state=name, attempt=attempt, stage="after_action")
            expected = str(step.get("expected") or "")
            self._telemetry("verification_started", state=state.name, repair_state=name, attempt=attempt, expected=expected, phase="repair_verification")
            verification = self._verify_action(before, after, expected, executed)
            self._telemetry("verification_completed", state=state.name, repair_state=name, attempt=attempt, expected=expected, verification=verification, phase="repair_verification", ok=bool(verification.get("ok")))
            ok = bool(executed.get("ok")) and bool(verification.get("ok"))
            reason = "" if ok else self._failure_reason(executed, verification)
            trace.append({
                "state": f"REPAIR:{name}",
                "phase": "repair",
                "repair_state": name,
                "repair_for": state.name,
                "repair_plan_id": plan_id,
                "attempt": attempt,
                "action_spec": action,
                "action": executed,
                "verification": verification,
                "expected": expected,
                "rationale": step.get("rationale", ""),
                "outcome": "success" if ok else "failure",
                "failure_reason": reason or None,
            })
            if not ok:
                return False, f"repair step '{name}' failed: {reason}"
        return True, "repair plan verified"

    @staticmethod
    def _failure_result(
        current: VisualState,
        trace: List[Dict[str, Any]],
        reason: str,
        category: str = "state_failed",
        underlying_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        failure = {
            "state": current.name,
            "category": category,
            "reason": reason,
            "timestamp": _now(),
        }
        return {
            "success": False,
            "states_visited": trace,
            "trace": trace,
            "final_state": current.name,
            "error": reason,
            "reason": underlying_reason or reason,
            "failure": failure,
        }

    def _failure_transition(
        self,
        current: VisualState,
        by_name: Dict[str, VisualState],
        trace: List[Dict[str, Any]],
        reason: str,
        underlying_reason: Optional[str] = None,
    ) -> Tuple[Optional[VisualState], Optional[Dict[str, Any]]]:
        fallback = current.on_failure
        if not fallback:
            return None, self._failure_result(current, trace, reason, underlying_reason=underlying_reason)
        if fallback == current.name:
            explicit = f"{reason}; on_failure for '{current.name}' points to itself"
            return None, self._failure_result(current, trace, explicit, category="invalid_failure_transition", underlying_reason=underlying_reason)
        if fallback not in by_name:
            explicit = f"{reason}; on_failure target '{fallback}' does not exist"
            return None, self._failure_result(current, trace, explicit, category="invalid_failure_transition", underlying_reason=underlying_reason)
        trace.append({
            "state": current.name,
            "phase": "transition",
            "outcome": "failure_transition",
            "next_state": fallback,
            "failure_reason": reason,
        })
        return by_name[fallback], None

    def run(
        self,
        states: List[VisualState],
        timeout_per_state: float = 30.0,
        start_state: Optional[str] = None,
        task_id: str = "",
    ) -> Dict[str, Any]:
        if not states:
            return {"success": False, "error": "no states provided", "states_visited": [], "trace": []}

        by_name: Dict[str, VisualState] = {}
        duplicate_names: List[str] = []
        for state in states:
            if state.name in by_name:
                duplicate_names.append(state.name)
            by_name[state.name] = state
        if duplicate_names:
            return {
                "success": False,
                "error": f"duplicate state name(s): {', '.join(sorted(set(duplicate_names)))}",
                "states_visited": [],
                "trace": [],
            }

        task_control = get_task_control()

        # Check emergency stop before we start
        if task_control.is_emergency_stop_active():
            return {
                "success": False,
                "error": "emergency stop is active",
                "states_visited": [],
                "trace": [],
                "failure": {"state": "INIT", "category": "emergency_stop", "reason": task_control._emergency_stop_reason},
            }

        trace: List[Dict[str, Any]] = _EventTrace(self._handle_event)
        if start_state and start_state not in by_name:
            return {
                "success": False,
                "error": f"start state '{start_state}' does not exist",
                "states_visited": trace,
                "trace": trace,
            }
        current = by_name[start_state] if start_state else states[0]
        transitions = 0
        max_transitions = max(4, len(states) * (self.max_retries + 2) + 4)

        # Helper to check for pause/cancel at a safe boundary
        def _check_control() -> Optional[Dict[str, Any]]:
            if task_control.is_emergency_stop_active():
                trace.append({
                    "state": current.name if current else "unknown",
                    "phase": "emergency_stop",
                    "outcome": "halted",
                    "failure_reason": task_control._emergency_stop_reason,
                })
                return {
                    "success": False,
                    "states_visited": trace,
                    "trace": trace,
                    "final_state": current.name if current else "unknown",
                    "error": f"emergency stop: {task_control._emergency_stop_reason}",
                    "failure": {"state": current.name if current else "unknown", "category": "emergency_stop",
                               "reason": task_control._emergency_stop_reason},
                }
            if task_id and task_control.is_cancel_requested(task_id):
                task_control.confirm_cancel(task_id, "Cancelled during execution")
                trace.append({
                    "state": current.name if current else "unknown",
                    "phase": "cancelled",
                    "outcome": "cancelled",
                })
                return {
                    "success": False,
                    "states_visited": trace,
                    "trace": trace,
                    "final_state": current.name if current else "unknown",
                    "error": "task cancelled by user",
                    "failure": {"state": current.name if current else "unknown", "category": "cancelled",
                               "reason": "User requested cancellation"},
                }
            if task_id and task_control.is_pause_requested(task_id):
                task_control.confirm_pause(task_id, "Paused at state boundary")
                # Wait here until resumed or cancelled
                while task_control.is_task_paused(task_id):
                    import time
                    time.sleep(1)
                    if task_control.is_emergency_stop_active():
                        return {
                            "success": False,
                            "states_visited": trace,
                            "trace": trace,
                            "final_state": current.name if current else "unknown",
                            "error": "emergency stop during pause",
                            "failure": {"state": current.name if current else "unknown",
                                       "category": "emergency_stop", "reason": task_control._emergency_stop_reason},
                        }
                    if task_control.is_cancel_requested(task_id):
                        task_control.confirm_cancel(task_id, "Cancelled while paused")
                        trace.append({
                            "state": current.name if current else "unknown",
                            "phase": "cancelled",
                            "outcome": "cancelled",
                        })
                        return {
                            "success": False,
                            "states_visited": trace,
                            "trace": trace,
                            "final_state": current.name if current else "unknown",
                            "error": "task cancelled while paused",
                            "failure": {"state": current.name if current else "unknown",
                                       "category": "cancelled", "reason": "Cancelled while paused"},
                        }
                # Resumed - re-check emergency stop
                if task_control.is_emergency_stop_active():
                    return {
                        "success": False,
                        "states_visited": trace,
                        "trace": trace,
                        "final_state": current.name if current else "unknown",
                        "error": "emergency stop after resume",
                        "failure": {"state": current.name if current else "unknown",
                                   "category": "emergency_stop", "reason": task_control._emergency_stop_reason},
                    }
            return None

        while transitions < max_transitions:
            transitions += 1

            # Check for control signals at each iteration boundary
            control_result = _check_control()
            if control_result is not None:
                return control_result
            if current.terminal:
                success = current.name.upper() != "FAILURE"
                trace.append({
                    "state": current.name,
                    "phase": "terminal",
                    "terminal": True,
                    "success": success,
                    "outcome": "success" if success else "failure",
                })
                if success:
                    return {
                        "success": True,
                        "states_visited": trace,
                        "trace": trace,
                        "final_state": current.name,
                    }
                return self._failure_result(current, trace, "task transitioned to FAILURE", category="failure_terminal")

            # Preconditions are explicit. ``expected`` is reserved for the
            # post-action verification and is never used as both before/after.
            if current.precondition:
                try:
                    condition = self.wait_until(current.precondition, timeout_per_state)
                except Exception as exc:  # noqa: BLE001
                    condition = {"matched": False, "detail": f"precondition watcher raised: {exc}"}
                matched = bool(condition.get("matched", condition.get("success", False))) if isinstance(condition, dict) else False
                trace.append({
                    "state": current.name,
                    "phase": "precondition",
                    "condition": current.precondition,
                    "outcome": "success" if matched else "failure",
                    "detail": condition.get("detail", "") if isinstance(condition, dict) else "invalid watcher result",
                })
                if not matched:
                    reason = f"precondition for state '{current.name}' was not reached: " + (
                        condition.get("detail") or condition.get("error") or "timeout"
                        if isinstance(condition, dict) else "watcher returned an unsupported result"
                    )
                    current, failed = self._failure_transition(current, by_name, trace, reason)
                    if failed:
                        return failed
                    assert current is not None
                    continue

            # A state with no action is a condition/transition node.  It must
            # still have an explicit way forward; silently retaining ``current``
            # is the loop bug this state machine is designed to avoid.
            if current.action is None:
                if current.expected:
                    try:
                        condition = self.wait_until(current.expected, timeout_per_state)
                    except Exception as exc:  # noqa: BLE001
                        condition = {"matched": False, "detail": f"state watcher raised: {exc}"}
                    matched = bool(condition.get("matched", condition.get("success", False))) if isinstance(condition, dict) else False
                    trace.append({
                        "state": current.name,
                        "phase": "state_check",
                        "condition": current.expected,
                        "outcome": "success" if matched else "failure",
                        "detail": condition.get("detail", "") if isinstance(condition, dict) else "invalid watcher result",
                    })
                    if not matched:
                        reason = f"state '{current.name}' condition was not reached: " + (
                            condition.get("detail") or condition.get("error") or "timeout"
                            if isinstance(condition, dict) else "watcher returned an unsupported result"
                        )
                        current, failed = self._failure_transition(current, by_name, trace, reason)
                        if failed:
                            return failed
                        assert current is not None
                        continue
                nxt = current.on_success
                if not nxt or nxt not in by_name or nxt == current.name:
                    reason = f"state '{current.name}' has no valid on_success transition"
                    return self._failure_result(current, trace, reason, category="invalid_success_transition")
                trace.append({"state": current.name, "phase": "transition", "outcome": "success_transition", "next_state": nxt})
                current = by_name[nxt]
                continue

            max_attempts = self.max_retries + 1
            state_succeeded = False
            final_reason = ""
            final_category = "retries_exhausted"

            for attempt in range(1, max_attempts + 1):
                self._telemetry("screen_event", state=current.name, attempt=attempt, stage="before_action")
                before = self._capture()
                self._telemetry("action_started", state=current.name, attempt=attempt, action_spec=current.action, phase="original_action")
                executed = self._execute_action(current.action)
                self._telemetry("action_completed", state=current.name, attempt=attempt, action_spec=current.action, action=executed, phase="original_action", ok=bool(executed.get("ok")))
                after = self._capture()
                self._telemetry("screen_event", state=current.name, attempt=attempt, stage="after_action")
                self._telemetry("verification_started", state=current.name, attempt=attempt, expected=current.expected, phase="action_verification")
                verification = self._verify_action(before, after, current.expected, executed)
                self._telemetry("verification_completed", state=current.name, attempt=attempt, expected=current.expected, verification=verification, phase="action_verification", ok=bool(verification.get("ok")))
                ok = bool(executed.get("ok")) and bool(verification.get("ok"))
                reason = "" if ok else self._failure_reason(executed, verification)
                trace.append({
                    "state": current.name,
                    "phase": "original_action",
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "action_spec": current.action,
                    "action": executed,
                    "verification": verification,
                    "expected": current.expected,
                    "outcome": "success" if ok else "failure",
                    "failure_reason": reason or None,
                })
                if ok:
                    state_succeeded = True
                    break

                final_reason = f"state '{current.name}' attempt {attempt}/{max_attempts} failed: {reason}"
                underlying_reason = verification.get("detail") or verification.get("error") or executed.get("detail") or executed.get("error") or reason
                retries_left = attempt < max_attempts
                if not retries_left:
                    trace.append({
                        "state": current.name,
                        "phase": "retry_decision",
                        "attempt": attempt,
                        "outcome": "fail",
                        "retry_allowed": False,
                        "failure": True,
                        "decision": "fail_task",
                        "failure_reason": final_reason,
                    })
                    break

                plan, repair_error = self._request_repair(current, attempt, executed, verification, reason)
                diagnosis = plan.get("diagnosis", {})
                retryable = bool(diagnosis.get("retryable", True))
                trace.append({
                    "state": f"DIAGNOSE:{current.name}",
                    "phase": "diagnose",
                    "attempt": attempt,
                    "outcome": "repair_available" if plan.get("available") else "no_repair",
                    "failure_reason": reason,
                    "diagnosis": diagnosis,
                    "repair_plan": plan,
                    "repair_error": repair_error,
                })

                if not retryable or not plan.get("retry_original", True):
                    final_category = "non_retryable"
                    detail = diagnosis.get("summary") or plan.get("reason") or reason
                    final_reason = f"state '{current.name}' is not safe to retry: {detail}"
                    trace.append({
                        "state": current.name,
                        "phase": "retry_decision",
                        "attempt": attempt,
                        "outcome": "fail",
                        "retry_allowed": False,
                        "failure": True,
                        "decision": "fail_task",
                        "failure_reason": final_reason,
                    })
                    break

                if plan.get("available"):
                    repaired, repair_reason = self._execute_repair(current, attempt, plan, trace)
                    if not repaired:
                        # Never replay the original action after an unverified
                        # repair; that is just blind retry with extra clicks.
                        final_category = "repair_failed"
                        final_reason = f"state '{current.name}' could not be repaired: {repair_reason}"
                        trace.append({
                            "state": current.name,
                            "phase": "retry_decision",
                            "attempt": attempt,
                            "outcome": "fail",
                            "retry_allowed": False,
                            "failure": True,
                            "decision": "fail_task",
                            "failure_reason": final_reason,
                        })
                        break
                    trace.append({
                        "state": current.name,
                        "phase": "retry_decision",
                        "attempt": attempt,
                        "outcome": "retry_after_repair",
                        "retry_allowed": True,
                        "reason": repair_reason,
                    })
                else:
                    trace.append({
                        "state": current.name,
                        "phase": "retry_decision",
                        "attempt": attempt,
                        "outcome": "bounded_retry",
                        "retry_allowed": True,
                        "reason": plan.get("reason") or repair_error or "no repair callback configured",
                    })

            if state_succeeded:
                nxt = current.on_success
                if not nxt or nxt not in by_name:
                    reason = f"state '{current.name}' succeeded but on_success target '{nxt}' is invalid"
                    return self._failure_result(current, trace, reason, category="invalid_success_transition")
                if nxt == current.name:
                    reason = f"state '{current.name}' succeeded but on_success points to itself"
                    return self._failure_result(current, trace, reason, category="invalid_success_transition")
                trace.append({"state": current.name, "phase": "transition", "outcome": "success_transition", "next_state": nxt})
                current = by_name[nxt]
                continue

            current, failed = self._failure_transition(current, by_name, trace, final_reason, underlying_reason=underlying_reason)
            if failed:
                if failed.get("failure"):
                    failed["failure"]["category"] = final_category
                return failed
            assert current is not None

        reason = f"state machine exceeded transition guard ({max_transitions}); graph may contain a cycle"
        return self._failure_result(current, trace, reason, category="transition_guard")

    @staticmethod
    def plan_to_states(plan: List[Dict[str, Any]], terminal: str = "SUCCESS") -> List[VisualState]:
        """Convert planner steps into a linear graph while preserving overrides."""
        states: List[VisualState] = []
        for index, step in enumerate(plan):
            name = str(step.get("name") or f"STATE_{index}")
            next_name = (
                str(plan[index + 1].get("name") or f"STATE_{index + 1}")
                if index + 1 < len(plan) else terminal
            )
            states.append(VisualState(
                name=name,
                precondition=str(step.get("precondition") or ""),
                expected=str(step.get("expected") or ""),
                action=step.get("action") if isinstance(step.get("action"), dict) else None,
                on_success=step.get("on_success") or next_name,
                on_failure=step.get("on_failure"),
                terminal=bool(step.get("terminal", False)),
            ))
        # Materialize referenced terminal nodes.  Planners may explicitly route
        # failures to FAILURE while successful linear plans use SUCCESS.
        names = {state.name for state in states}
        referenced = {
            target
            for state in states
            for target in (state.on_success, state.on_failure)
            if target
        }
        for terminal_name in (terminal, "FAILURE"):
            if terminal_name not in names and (terminal_name == terminal or terminal_name in referenced):
                states.append(VisualState(name=terminal_name, terminal=True))
                names.add(terminal_name)
        return states
