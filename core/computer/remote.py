"""Remote / approval control hub for the computer agent.

Phase C (remote Android/web control) makes the gateway a *secure* channel into
the local desktop.  In "remote approval" mode every MEDIUM-or-higher action
(click, type, launch, shell, ...) is paused and surfaced to a remote client as
an approval prompt; the client can Approve, Reject or trigger Pause/Resume/
Cancel/Emergency Stop.  Combined with the existing WebSocket event feed and a
live-screen frame endpoint this gives a phone or browser a full remote view of
the desktop.

The module is stdlib-only and headless-safe, so it is unit-testable without a
display.  The actual wiring (gateway endpoints, mobile dashboard) lives in the
gateway; this file only defines the policy/queue semantics.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from .permissions import RiskLevel, emergency_stop


def _now() -> str:
    return datetime.now().astimezone().isoformat()


class PromptState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class ApprovalPrompt:
    """One pending human-approval request for a single desktop action."""

    prompt_id: str
    action: str
    description: str
    risk: str
    args: dict[str, Any] = field(default_factory=dict)
    state: str = PromptState.PENDING.value
    created: str = field(default_factory=_now)
    resolved: Optional[str] = None
    decided_by: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RemoteApprovalGate:
    """Queue desktop actions that need human approval before they can run.

    Defaults to *disabled* so existing autonomous behaviour is unchanged; the
    gateway enables it (e.g. when a remote client is attached or when
    ``HERMUS_REMOTE_APPROVAL=1``) and it then gates every action at or above
    ``required_risk``.
    """

    def __init__(self, required_risk: RiskLevel = RiskLevel.MEDIUM, max_age: float = 600.0,
                 approve_grace: float = 10.0):
        self._lock = threading.Lock()
        self.enabled: bool = False
        self.required_risk: RiskLevel = required_risk
        self.max_age: float = max_age
        # After a prompt for an action is approved, the same action (same risk)
        # is auto-allowed for this many seconds — otherwise multi-step tasks
        # would stall waiting for a human on every single click.
        self.approve_grace: float = approve_grace
        self._prompts: dict[str, ApprovalPrompt] = {}
        self._history: list[ApprovalPrompt] = []  # bounded audit trail
        self._history_limit = 200
        self._recent_approvals: dict[str, float] = {}  # action -> epoch seconds

    # -- policy ---------------------------------------------------------
    def set_enabled(self, enabled: bool, required_risk: Optional[RiskLevel] = None) -> dict[str, Any]:
        with self._lock:
            self.enabled = bool(enabled)
            if required_risk is not None:
                self.required_risk = required_risk
        return self.status()

    def _risk_at_or_above(self, risk: Optional[str]) -> bool:
        order = {RiskLevel.LOW.value: 0, RiskLevel.MEDIUM.value: 1, RiskLevel.HIGH.value: 2}
        return order.get(str(risk or ""), 1) >= order[self.required_risk.value]

    def check(
        self,
        action: str,
        args: Optional[dict[str, Any]] = None,
        risk: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        """Decide whether ``action`` may proceed or must await approval."""
        if not self.enabled or not self._risk_at_or_above(risk):
            return {"allowed": True, "pending": False, "reason": "ok"}
        if self._within_grace(action):
            return {"allowed": True, "pending": False, "reason": "approved (grace window)"}
        prompt = ApprovalPrompt(
            prompt_id=uuid.uuid4().hex[:12],
            action=action,
            description=description or _describe(action, args),
            risk=risk or RiskLevel.MEDIUM.value,
            args=dict(args or {}),
        )
        with self._lock:
            self._prune_expired_locked()
            self._prompts[prompt.prompt_id] = prompt
            self._history.append(prompt)
        return {
            "allowed": False,
            "pending": True,
            "prompt_id": prompt.prompt_id,
            "reason": f"awaiting remote approval for '{action}' (prompt {prompt.prompt_id})",
        }

    # -- resolution -----------------------------------------------------
    def approve(self, prompt_id: str, by: str = "remote") -> dict[str, Any]:
        with self._lock:
            prompt = self._prompts.get(prompt_id)
            if prompt is None:
                return {"success": False, "error": f"prompt '{prompt_id}' not found"}
            if prompt.state != PromptState.PENDING.value:
                return {"success": False, "error": f"prompt already {prompt.state}"}
            prompt.state = PromptState.APPROVED.value
            prompt.resolved = _now()
            prompt.decided_by = by
            self._prompts.pop(prompt_id, None)
            if self.approve_grace > 0:
                import time as _time

                self._recent_approvals[prompt.action] = _time.time()
        return {"success": True, "prompt_id": prompt_id, "decision": "approved",
                "action": prompt.action, "description": prompt.description}

    def reject(self, prompt_id: str, reason: str = "", by: str = "remote") -> dict[str, Any]:
        with self._lock:
            prompt = self._prompts.get(prompt_id)
            if prompt is None:
                return {"success": False, "error": f"prompt '{prompt_id}' not found"}
            if prompt.state != PromptState.PENDING.value:
                return {"success": False, "error": f"prompt already {prompt.state}"}
            prompt.state = PromptState.REJECTED.value
            prompt.resolved = _now()
            prompt.decided_by = by
            prompt.reason = reason
            self._prompts.pop(prompt_id, None)
        return {"success": True, "prompt_id": prompt_id, "decision": "rejected",
                "action": prompt.action, "description": prompt.description, "reason": reason}

    def _prune_expired_locked(self) -> None:
        now = datetime.now().astimezone().timestamp()
        expired = [
            pid for pid, prompt in self._prompts.items()
            if self.max_age > 0
            and (datetime.fromisoformat(prompt.created).astimezone().timestamp() + self.max_age) < now
        ]
        for pid in expired:
            prompt = self._prompts.pop(pid)
            prompt.state = PromptState.EXPIRED.value
            prompt.resolved = _now()
            self._history.append(prompt)
        if self.approve_grace > 0:
            cutoff = now - self.approve_grace
            self._recent_approvals = {
                action: ts for action, ts in self._recent_approvals.items() if ts >= cutoff
            }

    def _within_grace(self, action: str) -> bool:
        with self._lock:
            import time as _time

            ts = self._recent_approvals.get(action)
            if ts is None:
                return False
            if self.approve_grace > 0 and (_time.time() - ts) <= self.approve_grace:
                return True
            self._recent_approvals.pop(action, None)
            return False

    # -- inspection -----------------------------------------------------
    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [p.to_dict() for p in self._prompts.values()]

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._history)
        return [p.to_dict() for p in items[-max(1, int(limit)):]]

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._prune_expired_locked()
            return {
                "enabled": self.enabled,
                "required_risk": self.required_risk.value,
                "pending_count": len(self._prompts),
                "pending": [p.to_dict() for p in self._prompts.values()],
            }


def _describe(action: str, args: Optional[dict[str, Any]] = None) -> str:
    args = args or {}
    if action == "click":
        return f"Click at ({args.get('x')}, {args.get('y')})"
    if action == "type_text":
        return f"Type text ({len(str(args.get('text', '')))} chars)"
    if action == "open_application":
        return f"Open application '{args.get('name')}'"
    if action == "install_application":
        return f"Install application '{args.get('name')}'"
    if action == "shell":
        return f"Run shell command: {args.get('command') or args.get('cmd')}"
    if action == "click_target":
        return f"Click UI target '{args.get('target')}'"
    return f"{action} {args}"


class RemoteControlHub:
    """Aggregate entry point for a remote dashboard into the computer agent.

    Combines the approval gate, the task-control lifecycle (pause/resume/
    cancel/emergency-stop), live screen access and the recent event feed so a
    phone or browser has one object to talk to.
    """

    def __init__(
        self,
        approval: Optional[RemoteApprovalGate] = None,
        task_control: Optional[Any] = None,
        event_bus: Optional[Any] = None,
        emergency: Optional[Any] = None,
    ):
        self.approval = approval or remote_approval
        self.emergency = emergency or emergency_stop
        self._task_control = task_control
        self._event_bus = event_bus

    # -- lazy dependencies (avoid import cycles at module load) ----------
    def _control(self):
        if self._task_control is None:
            from .task_control import get_task_control

            self._task_control = get_task_control()
        return self._task_control

    def _bus(self):
        if self._event_bus is None:
            from .events import computer_event_bus

            self._event_bus = computer_event_bus
        return self._event_bus

    # -- lifecycle controls (delegate to task control + emergency stop) --
    def pause(self, task_id: str, reason: str = "remote pause") -> dict[str, Any]:
        ok = self._control().request_pause(task_id, reason)
        return {"success": ok, "action": "pause", "task_id": task_id, "reason": reason}

    def resume(self, task_id: str) -> dict[str, Any]:
        ok = self._control().resume(task_id)
        return {"success": ok, "action": "resume", "task_id": task_id}

    def cancel(self, task_id: str, reason: str = "remote cancel") -> dict[str, Any]:
        ok = self._control().request_cancel(task_id, reason)
        return {"success": ok, "action": "cancel", "task_id": task_id, "reason": reason}

    def emergency_stop(self, reason: str = "remote emergency stop") -> dict[str, Any]:
        self.emergency.halt(reason)
        self._control().emergency_stop(reason)
        return {"success": True, "action": "emergency_stop", "reason": reason}

    def release(self) -> dict[str, Any]:
        self.emergency.release()
        self._control().release_emergency_stop()
        return {"success": True, "action": "emergency_release"}

    # -- snapshot -------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """One consolidated view for a remote client (no heavy screen frame)."""
        control_status = {}
        try:
            control_status = self._control().get_status()
        except Exception:  # noqa: BLE001
            pass
        try:
            events = self._bus().recent(30)
        except Exception:  # noqa: BLE001
            events = []
        return {
            "ts": _now(),
            "approval": self.approval.status(),
            "control": control_status,
            "emergency": {
                "halted": self.emergency.halted,
                "reason": self.emergency.reason,
            },
            "recent_events": events,
        }


remote_approval = RemoteApprovalGate()
remote_control = RemoteControlHub()
