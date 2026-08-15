"""Task Control System - Pause, Resume, Cancel, and Emergency Stop for computer tasks.

This module provides granular control over running tasks:

- PAUSE: Save state at current safe boundary, stop execution
- RESUME: Continue from paused state
- CANCEL: Terminate task and mark as cancelled
- EMERGENCY_STOP: Immediately block all computer actions

The distinction:
- Pause = save state and stop after the current safe boundary (no partial actions)
- Cancel = terminate task and mark it cancelled (may leave partial state)
- Emergency Stop = immediately block computer actions (safety override)
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .events import publish


class TaskControlState(str, Enum):
    """Possible control states for a task."""
    IDLE = "idle"                    # No task running
    RUNNING = "running"              # Actively executing
    PAUSED = "paused"               # Paused at safe boundary
    PAUSING = "pausing"             # In process of pausing
    CANCELLING = "cancelling"       # In process of cancelling
    CANCELLED = "cancelled"         # User cancelled
    COMPLETED = "completed"         # Successfully completed
    FAILED = "failed"               # Failed with error
    INTERRUPTED = "interrupted"     # Interrupted externally


class ControlAction(str, Enum):
    """Actions that can be performed on a task."""
    RUN = "run"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    EMERGENCY_STOP = "emergency_stop"
    RELEASE_STOP = "release_stop"


@dataclass
class ControlEvent:
    """A control state change event."""
    action: ControlAction
    timestamp: str
    reason: str = ""
    task_id: str = ""
    previous_state: TaskControlState = TaskControlState.IDLE
    new_state: TaskControlState = TaskControlState.IDLE
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "timestamp": self.timestamp,
            "reason": self.reason,
            "task_id": self.task_id,
            "previous_state": self.previous_state.value,
            "new_state": self.new_state.value,
            "metadata": dict(self.metadata),
        }


@dataclass
class TaskControlContext:
    """Context for task control operations."""
    task_id: str
    task: str
    current_state: str = ""  # Current plan state
    control_state: TaskControlState = TaskControlState.IDLE
    paused_at: Optional[str] = None
    pause_reason: str = ""
    resume_count: int = 0
    cancel_requested: bool = False
    pause_requested: bool = False
    last_action: Optional[str] = None
    last_action_time: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task": self.task,
            "current_state": self.current_state,
            "control_state": self.control_state.value,
            "paused_at": self.paused_at,
            "pause_reason": self.pause_reason,
            "resume_count": self.resume_count,
            "cancel_requested": self.cancel_requested,
            "pause_requested": self.pause_requested,
            "last_action": self.last_action,
            "last_action_time": self.last_action_time,
        }


class TaskControlManager:
    """Manages pause/resume/cancel for computer tasks.
    
    This is a thread-safe singleton that coordinates task control
    across the computer agent and state machine.
    """
    
    _instance: Optional["TaskControlManager"] = None
    _lock = threading.RLock()
    
    def __new__(cls) -> "TaskControlManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        self._task_contexts: Dict[str, TaskControlContext] = {}
        self._control_events: List[ControlEvent] = []
        self._event_log: List[Dict[str, Any]] = []
        self._emergency_stop_active = False
        self._emergency_stop_reason = ""
        self._emergency_stop_time: Optional[str] = None
        self._active_task_id: Optional[str] = None
        self._pause_event = threading.Event()
        self._cancel_event = threading.Event()
        
        # Callbacks for state machine integration
        self._on_pause: Optional[callable] = None
        self._on_resume: Optional[callable] = None
        self._on_cancel: Optional[callable] = None
    
    def register_task(
        self,
        task_id: str,
        task: str,
        initial_state: str = "",
    ) -> TaskControlContext:
        """Register a new task for control."""
        with self._lock:
            ctx = TaskControlContext(
                task_id=task_id,
                task=task,
                current_state=initial_state,
                control_state=TaskControlState.RUNNING,
            )
            self._task_contexts[task_id] = ctx
            self._active_task_id = task_id
            self._emit_event(ControlEvent(
                action=ControlAction.RUN,
                timestamp=datetime.now().astimezone().isoformat(),
                task_id=task_id,
                previous_state=TaskControlState.IDLE,
                new_state=TaskControlState.RUNNING,
            ))
            return ctx
    
    def update_state(self, task_id: str, state: str) -> None:
        """Update the current plan state for a task."""
        with self._lock:
            if task_id in self._task_contexts:
                ctx = self._task_contexts[task_id]
                ctx.last_action = state
                ctx.last_action_time = datetime.now().astimezone().isoformat()
                ctx.current_state = state
    
    def request_pause(self, task_id: str, reason: str = "") -> bool:
        """Request pause at next safe boundary.
        
        Returns True if pause was requested, False if task not found.
        The actual pause happens at the next safe boundary in the state machine.
        """
        with self._lock:
            if task_id not in self._task_contexts:
                return False
            
            ctx = self._task_contexts[task_id]
            if ctx.control_state != TaskControlState.RUNNING:
                return False
            
            ctx.pause_requested = True
            ctx.control_state = TaskControlState.PAUSING
            
            self._emit_event(ControlEvent(
                action=ControlAction.PAUSE,
                timestamp=datetime.now().astimezone().isoformat(),
                reason=reason or "User requested pause",
                task_id=task_id,
                previous_state=TaskControlState.RUNNING,
                new_state=TaskControlState.PAUSING,
            ))
            
            # Signal the pause event
            self._pause_event.set()
            
            publish("task_pause_requested", {
                "task_id": task_id,
                "reason": reason,
                "state": ctx.current_state,
            })
            
            return True
    
    def confirm_pause(self, task_id: str, reason: str = "") -> bool:
        """Confirm that a task has paused at a safe boundary."""
        with self._lock:
            if task_id not in self._task_contexts:
                return False
            
            ctx = self._task_contexts[task_id]
            previous = ctx.control_state
            ctx.control_state = TaskControlState.PAUSED
            ctx.pause_requested = False
            ctx.paused_at = datetime.now().astimezone().isoformat()
            ctx.pause_reason = reason or "User requested pause"
            
            self._emit_event(ControlEvent(
                action=ControlAction.PAUSE,
                timestamp=ctx.paused_at,
                reason=ctx.pause_reason,
                task_id=task_id,
                previous_state=previous,
                new_state=TaskControlState.PAUSED,
            ))
            
            publish("task_paused", {
                "task_id": task_id,
                "state": ctx.current_state,
                "reason": ctx.pause_reason,
            })
            
            return True
    
    def resume(self, task_id: str) -> bool:
        """Resume a paused task."""
        with self._lock:
            if task_id not in self._task_contexts:
                return False
            
            ctx = self._task_contexts[task_id]
            if ctx.control_state != TaskControlState.PAUSED:
                return False
            
            previous = ctx.control_state
            ctx.control_state = TaskControlState.RUNNING
            ctx.resume_count += 1
            ctx.paused_at = None
            ctx.pause_reason = ""
            
            self._pause_event.clear()
            
            self._emit_event(ControlEvent(
                action=ControlAction.RESUME,
                timestamp=datetime.now().astimezone().isoformat(),
                task_id=task_id,
                previous_state=previous,
                new_state=TaskControlState.RUNNING,
                metadata={"resume_count": ctx.resume_count},
            ))
            
            publish("task_resumed", {
                "task_id": task_id,
                "state": ctx.current_state,
                "resume_count": ctx.resume_count,
            })
            
            # Trigger resume callback
            if self._on_resume:
                try:
                    self._on_resume(task_id)
                except Exception:
                    pass
            
            return True
    
    def request_cancel(self, task_id: str, reason: str = "") -> bool:
        """Request cancellation of a task."""
        with self._lock:
            if task_id not in self._task_contexts:
                return False
            
            ctx = self._task_contexts[task_id]
            if ctx.control_state not in (TaskControlState.RUNNING, TaskControlState.PAUSING, TaskControlState.PAUSED):
                return False
            
            previous = ctx.control_state
            ctx.control_state = TaskControlState.CANCELLING
            ctx.cancel_requested = True
            
            self._cancel_event.set()
            
            self._emit_event(ControlEvent(
                action=ControlAction.CANCEL,
                timestamp=datetime.now().astimezone().isoformat(),
                reason=reason or "User requested cancellation",
                task_id=task_id,
                previous_state=previous,
                new_state=TaskControlState.CANCELLING,
            ))
            
            publish("task_cancel_requested", {
                "task_id": task_id,
                "reason": reason,
                "state": ctx.current_state,
            })
            
            return True
    
    def confirm_cancel(self, task_id: str, reason: str = "") -> bool:
        """Confirm that a task has been cancelled."""
        with self._lock:
            if task_id not in self._task_contexts:
                return False
            
            ctx = self._task_contexts[task_id]
            previous = ctx.control_state
            ctx.control_state = TaskControlState.CANCELLED
            ctx.cancel_requested = False
            
            self._emit_event(ControlEvent(
                action=ControlAction.CANCEL,
                timestamp=datetime.now().astimezone().isoformat(),
                reason=reason or "Task cancelled by user",
                task_id=task_id,
                previous_state=previous,
                new_state=TaskControlState.CANCELLED,
            ))
            
            self._cancel_event.clear()
            self._pause_event.clear()
            
            publish("task_cancelled", {
                "task_id": task_id,
                "state": ctx.current_state,
                "reason": reason or "User cancelled",
            })
            
            return True
    
    def complete_task(self, task_id: str, success: bool) -> bool:
        """Mark a task as completed."""
        with self._lock:
            if task_id not in self._task_contexts:
                return False
            
            ctx = self._task_contexts[task_id]
            ctx.control_state = TaskControlState.COMPLETED if success else TaskControlState.FAILED
            
            self._emit_event(ControlEvent(
                action=ControlAction.RUN,
                timestamp=datetime.now().astimezone().isoformat(),
                task_id=task_id,
                previous_state=TaskControlState.RUNNING,
                new_state=ctx.control_state,
                metadata={"success": success},
            ))
            
            self._active_task_id = None
            
            return True
    
    def emergency_stop(self, reason: str = "") -> None:
        """Activate emergency stop - immediately block all computer actions."""
        with self._lock:
            self._emergency_stop_active = True
            self._emergency_stop_reason = reason or "Emergency stop activated"
            self._emergency_stop_time = datetime.now().astimezone().isoformat()
            
            # Cancel all running tasks
            for task_id, ctx in self._task_contexts.items():
                if ctx.control_state == TaskControlState.RUNNING:
                    ctx.control_state = TaskControlState.INTERRUPTED
            
            self._emit_event(ControlEvent(
                action=ControlAction.EMERGENCY_STOP,
                timestamp=self._emergency_stop_time,
                reason=self._emergency_stop_reason,
                new_state=TaskControlState.INTERRUPTED,
            ))
            
            publish("emergency_stop", {
                "active": True,
                "reason": self._emergency_stop_reason,
                "timestamp": self._emergency_stop_time,
            })
    
    def release_emergency_stop(self) -> bool:
        """Release emergency stop - re-enable computer actions."""
        with self._lock:
            if not self._emergency_stop_active:
                return False
            
            previous = self._emergency_stop_reason
            self._emergency_stop_active = False
            self._emergency_stop_reason = ""
            self._emergency_stop_time = None
            
            self._emit_event(ControlEvent(
                action=ControlAction.RELEASE_STOP,
                timestamp=datetime.now().astimezone().isoformat(),
                reason="Emergency stop released",
                previous_state=TaskControlState.INTERRUPTED,
                new_state=TaskControlState.IDLE,
            ))
            
            publish("emergency_stop_released", {
                "active": False,
            })
            
            return True
    
    def is_pause_requested(self, task_id: str) -> bool:
        """Check if pause has been requested for a task."""
        with self._lock:
            if task_id in self._task_contexts:
                return self._task_contexts[task_id].pause_requested
            return False
    
    def is_cancel_requested(self, task_id: str) -> bool:
        """Check if cancel has been requested for a task."""
        with self._lock:
            if task_id in self._task_contexts:
                return self._task_contexts[task_id].cancel_requested
            return False

    def is_cancelled(self) -> bool:
        """Check if any task has been cancelled."""
        with self._lock:
            return any(
                ctx.control_state == TaskControlState.CANCELLED
                for ctx in self._task_contexts.values()
            )

    def is_emergency_stop_active(self) -> bool:
        """Check if emergency stop is active."""
        return self._emergency_stop_active
    
    def is_task_running(self, task_id: str) -> bool:
        """Check if a task is currently running."""
        with self._lock:
            if task_id in self._task_contexts:
                return self._task_contexts[task_id].control_state == TaskControlState.RUNNING
            return False
    
    def is_task_paused(self, task_id: str) -> bool:
        """Check if a task is currently paused."""
        with self._lock:
            if task_id in self._task_contexts:
                return self._task_contexts[task_id].control_state == TaskControlState.PAUSED
            return False
    
    def get_task_context(self, task_id: str) -> Optional[TaskControlContext]:
        """Get the control context for a task."""
        with self._lock:
            return self._task_contexts.get(task_id)
    
    def get_active_task(self) -> Optional[str]:
        """Get the ID of the currently active task."""
        return self._active_task_id
    
    def get_control_state(self, task_id: str) -> Optional[TaskControlState]:
        """Get the control state for a task."""
        with self._lock:
            if task_id in self._task_contexts:
                return self._task_contexts[task_id].control_state
            return None
    
    def get_status(self) -> Dict[str, Any]:
        """Get overall task control status."""
        with self._lock:
            running = sum(1 for ctx in self._task_contexts.values() 
                         if ctx.control_state == TaskControlState.RUNNING)
            paused = sum(1 for ctx in self._task_contexts.values() 
                         if ctx.control_state == TaskControlState.PAUSED)
            cancelling = sum(1 for ctx in self._task_contexts.values() 
                           if ctx.control_state == TaskControlState.CANCELLING)
            
            return {
                "emergency_stop_active": self._emergency_stop_active,
                "emergency_stop_reason": self._emergency_stop_reason,
                "emergency_stop_time": self._emergency_stop_time,
                "active_task": self._active_task_id,
                "running_count": running,
                "paused_count": paused,
                "cancelling_count": cancelling,
                "tasks": {
                    tid: ctx.to_dict() 
                    for tid, ctx in self._task_contexts.items()
                },
                "recent_events": self._event_log[-20:],
            }
    
    def _emit_event(self, event: ControlEvent) -> None:
        """Emit a control event."""
        self._control_events.append(event)
        self._event_log.append(event.to_dict())
        
        # Keep log bounded
        if len(self._event_log) > 1000:
            self._event_log = self._event_log[-500:]
    
    def set_callbacks(
        self,
        on_pause: Optional[callable] = None,
        on_resume: Optional[callable] = None,
        on_cancel: Optional[callable] = None,
    ) -> None:
        """Set callbacks for state machine integration."""
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._on_cancel = on_cancel
    
    def wait_for_pause(self, timeout: float = 30.0) -> bool:
        """Wait for pause to be confirmed (blocking)."""
        return self._pause_event.wait(timeout=timeout)
    
    def wait_for_cancel(self, timeout: float = 10.0) -> bool:
        """Wait for cancel to be confirmed (blocking)."""
        return self._cancel_event.wait(timeout=timeout)
    
    def unregister_task(self, task_id: str) -> bool:
        """Unregister a task when it's done."""
        with self._lock:
            if task_id in self._task_contexts:
                del self._task_contexts[task_id]
                if self._active_task_id == task_id:
                    self._active_task_id = None
                return True
            return False


# Global singleton instance
task_control = TaskControlManager()


def get_task_control() -> TaskControlManager:
    """Get the global task control manager."""
    return task_control
