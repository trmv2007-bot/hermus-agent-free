"""Privacy and path policy for screen recordings, plus computer-action policy.

Tool-level consent remains enforced by :mod:`core.permissions`.  This module
adds storage-specific safeguards (private file modes, bounded capture
settings, and a default rule that agent-created recordings stay under
data/recordings) and the computer-control layer's own safety gates: a risk
tier for every desktop action and a global, thread-safe emergency stop.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional


class RecordingPolicy:
    def __init__(
        self,
        recordings_root: str = "data/recordings",
        max_fps: float = 30.0,
        max_buffer_seconds: float = 300.0,
        allow_external_paths: bool = False,
    ):
        raw_root = Path(recordings_root).expanduser()
        if not raw_root.is_absolute() and recordings_root == "data/recordings":
            raw_root = Path(__file__).resolve().parents[2] / raw_root
        self.root = raw_root.resolve()
        self.max_fps = float(max_fps)
        self.max_buffer_seconds = float(max_buffer_seconds)
        self.allow_external_paths = allow_external_paths

    def validate_settings(self, fps: float, max_seconds: float) -> Dict[str, Any]:
        if not 0.1 <= float(fps) <= self.max_fps:
            return {"ok": False, "error": f"fps must be between 0.1 and {self.max_fps:g}"}
        if not 1.0 <= float(max_seconds) <= self.max_buffer_seconds:
            return {"ok": False, "error": f"buffer duration must be between 1 and {self.max_buffer_seconds:g} seconds"}
        return {"ok": True, "fps": float(fps), "max_seconds": float(max_seconds)}

    def output_path(self, value: Optional[str], default_name: str = "recording.mp4") -> Path:
        requested = Path(value or default_name).expanduser()
        target = requested.resolve() if requested.is_absolute() else (self.root / requested).resolve()
        if target.suffix.lower() not in {".mp4", ".webm"}:
            raise ValueError("recording path must end in .mp4 or .webm")
        if not self.allow_external_paths and target != self.root and self.root not in target.parents:
            raise PermissionError(f"recording path must stay under {self.root}")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.parent.chmod(0o700)
        except OSError:
            pass
        return target

    @staticmethod
    def task_id(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", (value or "").strip()).strip(".-")
        if not safe:
            raise ValueError("task id must contain a letter or number")
        return safe

    @staticmethod
    def secure(path: Path) -> None:
        try:
            path.chmod(0o600)
        except OSError:
            pass


recording_policy = RecordingPolicy()


class RiskLevel(str, Enum):
    """Risk tier for a desktop action."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# action name -> risk tier.  LOW runs freely, MEDIUM is audited, HIGH always
# requires explicit approval before the agent may perform it.
ACTION_RISK: Dict[str, RiskLevel] = {
    "move_mouse": RiskLevel.LOW,
    "scroll": RiskLevel.LOW,
    "focus_window": RiskLevel.LOW,
    "find_on_screen": RiskLevel.LOW,
    "screen_understand": RiskLevel.LOW,
    "click": RiskLevel.MEDIUM,
    "double_click": RiskLevel.MEDIUM,
    "right_click": RiskLevel.MEDIUM,
    "click_target": RiskLevel.MEDIUM,
    "type_text": RiskLevel.MEDIUM,
    "press_key": RiskLevel.MEDIUM,
    "hotkey": RiskLevel.MEDIUM,
    "open_application": RiskLevel.MEDIUM,
    "close_application": RiskLevel.MEDIUM,
    # These escalate from the action arguments at check time.
    "delete_file": RiskLevel.MEDIUM,
    "shell": RiskLevel.MEDIUM,
    "install_application": RiskLevel.MEDIUM,
    "sudo": RiskLevel.HIGH,
    "admin": RiskLevel.HIGH,
    "system_config": RiskLevel.HIGH,
}


class ComputerPolicy:
    """Risk classification and approval gate for desktop actions.

    The low/medium/high tiers answer the safety question before any mouse or
    keyboard event is emitted: LOW actions are safe enough to run autonomously
    and audited; MEDIUM actions (click, type, launch) are audited and can be
    disabled wholesale; HIGH actions (sudo, admin, system config) require an
    explicit ``approve`` call and are denied until then.
    """

    def __init__(self, approvals_path: Optional[str] = None) -> None:
        self.approvals_path = approvals_path
        self._approved: set = set()
        self._lock = threading.Lock()

    def risk_of(self, action: str) -> RiskLevel:
        return ACTION_RISK.get(action, RiskLevel.MEDIUM)

    def escalate(self, action: str, args: Optional[Dict[str, Any]] = None) -> RiskLevel:
        """Raise a risk tier from dangerous argument content (e.g. sudo)."""
        risk = self.risk_of(action)
        text = " ".join(str(v) for v in (args or {}).values()).lower()
        if action in ("shell", "install_application") and any(
            marker in text for marker in ("sudo", "rm -rf", "dd if", "--force", "-y")
        ):
            return RiskLevel.HIGH
        if action == "type_text" and any(
            marker in text for marker in ("sudo", "password", "rm -rf")
        ):
            return RiskLevel.HIGH
        return risk

    def approve(self, action: str, scope: str = "") -> None:
        with self._lock:
            self._approved.add((action, scope))

    def revoke(self, action: str, scope: str = "") -> None:
        with self._lock:
            self._approved.discard((action, scope))

    def is_approved(self, action: str, scope: str = "") -> bool:
        with self._lock:
            return (action, scope) in self._approved or (action, "") in self._approved

    def check(
        self,
        action: str,
        args: Optional[Dict[str, Any]] = None,
        scope: str = "",
    ) -> Dict[str, Any]:
        """Return a decision record for an action (``allowed`` + reason)."""
        risk = self.escalate(action, args)
        allowed = True
        reason = "ok"
        if risk is RiskLevel.HIGH and not self.is_approved(action, scope):
            allowed = False
            reason = f"high-risk action '{action}' requires explicit approval"
        return {"action": action, "risk": risk.value, "allowed": allowed, "reason": reason}


computer_policy = ComputerPolicy()


class EmergencyStop:
    """Global, thread-safe halt for all computer control.

    ``hermus stop`` (or any in-process ``halt()``) flips this latch; every
    controller action checks it and refuses to emit further input until it is
    released, providing a circuit breaker against runaway autonomous loops.

    The latch is also mirrored to a small state file so a ``hermus stop`` run
    from a *different* process can halt an already-running agent: each action
    re-reads the file, so no further mouse/keyboard event is emitted once the
    stop has been requested.
    """

    def __init__(self, halt_path: Optional[str] = None) -> None:
        self._halted = threading.Event()
        self._lock = threading.Lock()
        self._reason: Optional[str] = None
        if halt_path:
            self.halt_path = Path(halt_path).expanduser().resolve()
        else:
            default = Path(__file__).resolve().parents[2] / "data" / "recordings" / ".computer-stop.json"
            self.halt_path = default.resolve()

    @property
    def halted(self) -> bool:
        if self._halted.is_set():
            return True
        return self.halt_path.exists()

    @property
    def reason(self) -> Optional[str]:
        if self._halted.is_set():
            return self._reason
        return self._read_file_reason()

    def _read_file_reason(self) -> Optional[str]:
        try:
            import json

            data = json.loads(self.halt_path.read_text(encoding="utf-8"))
            return data.get("reason")
        except Exception:
            return None

    def halt(self, reason: str = "emergency stop") -> None:
        with self._lock:
            self._reason = reason
        self._halted.set()
        try:
            import json

            self.halt_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.halt_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"halted": True, "reason": reason, "ts": datetime.now().isoformat()}),
                encoding="utf-8",
            )
            temporary.replace(self.halt_path)
        except OSError:
            pass

    def release(self) -> None:
        with self._lock:
            self._reason = None
        self._halted.clear()
        try:
            self.halt_path.unlink(missing_ok=True)
        except OSError:
            pass

    def check(self) -> Dict[str, Any]:
        if self.halted:
            return {"ok": False, "halted": True, "error": self.reason or "emergency stop engaged"}
        return {"ok": True, "halted": False}


emergency_stop = EmergencyStop()
