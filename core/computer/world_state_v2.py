"""Enhanced World State with richer model and observation types.

The enhanced WorldState distinguishes between:
- OBSERVED: directly seen via vision
- INFERRED: deduced from context or past actions
- EXPECTED: what the planner assumes will happen
- UNKNOWN: not yet determined
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


class ObservationType(str, Enum):
    """Distinguishes how a piece of world state was determined."""
    OBSERVED = "observed"       # Directly seen via vision
    INFERRED = "inferred"       # Deduced from context
    EXPECTED = "expected"       # Planner assumption
    UNKNOWN = "unknown"         # Not yet determined


class CertaintyLevel(float, Enum):
    """How confident we are in the observation."""
    HIGH = 0.95      # Direct vision with clear match
    MEDIUM = 0.70    # Clear inference from context
    LOW = 0.40       # Heuristic or weak signal
    SPECULATIVE = 0.10  # Guess or assumption


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _unique(values: Iterable[Any], limit: int = 50) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output[-limit:]


@dataclass
class GroundedTarget:
    """Structured visual target with grounding metadata."""
    name: str
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # x1, y1, x2, y2
    confidence: float = 0.0
    role: str = "unknown"  # button, link, field, menu, etc.
    state: str = "unknown"  # enabled, disabled, visible, hidden, etc.
    safe_to_click: bool = False
    text: str = ""
    element_type: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "bbox": list(self.bbox),
            "confidence": round(self.confidence, 4),
            "role": self.role,
            "state": self.state,
            "safe_to_click": self.safe_to_click,
            "text": self.text,
            "element_type": self.element_type,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GroundedTarget":
        bbox = data.get("bbox", (0.0, 0.0, 0.0, 0.0))
        if isinstance(bbox, list) and len(bbox) == 4:
            bbox = tuple(float(v) for v in bbox)
        return cls(
            name=str(data.get("name", "")),
            bbox=bbox,
            confidence=float(data.get("confidence", 0.0)),
            role=str(data.get("role", "unknown")),
            state=str(data.get("state", "unknown")),
            safe_to_click=bool(data.get("safe_to_click", False)),
            text=str(data.get("text", "")),
            element_type=str(data.get("element_type", "")),
        )


@dataclass
class DesktopContext:
    """Full context about the current desktop state."""
    active_application: Optional[str] = None
    active_window: Optional[str] = None
    active_window_id: Optional[str] = None
    focused_element: Optional[str] = None
    focused_element_type: Optional[str] = None
    mouse_position: Tuple[float, float] = (0.0, 0.0)
    screen_resolution: Tuple[int, int] = (1920, 1080)
    visible_controls: List[str] = field(default_factory=list)
    dialogs: List[str] = field(default_factory=list)
    active_modal: Optional[str] = None
    notifications: List[str] = field(default_factory=list)
    known_errors: List[str] = field(default_factory=list)
    downloads: List[Dict[str, str]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_application": self.active_application,
            "active_window": self.active_window,
            "active_window_id": self.active_window_id,
            "focused_element": self.focused_element,
            "focused_element_type": self.focused_element_type,
            "mouse_position": list(self.mouse_position),
            "screen_resolution": list(self.screen_resolution),
            "visible_controls": list(self.visible_controls),
            "dialogs": list(self.dialogs),
            "active_modal": self.active_modal,
            "notifications": list(self.notifications),
            "known_errors": list(self.known_errors),
            "downloads": list(self.downloads),
        }


@dataclass 
class RichObservation:
    """Single observation with provenance metadata."""
    key: str
    value: Any
    observation_type: ObservationType = ObservationType.UNKNOWN
    confidence: float = 0.0
    timestamp: str = field(default_factory=_now)
    source: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "observation_type": self.observation_type.value,
            "confidence": round(self.confidence, 4),
            "timestamp": self.timestamp,
            "source": self.source,
            "evidence": dict(self.evidence),
        }


@dataclass
class TaskContext:
    """Current task execution context."""
    task: Optional[str] = None
    task_state: str = "UNKNOWN"
    current_step: Optional[str] = None
    plan_position: int = 0
    completed_steps: List[str] = field(default_factory=list)
    failed_steps: List[str] = field(default_factory=list)
    repair_count: int = 0
    retry_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "task_state": self.task_state,
            "current_step": self.current_step,
            "plan_position": self.plan_position,
            "completed_steps": list(self.completed_steps),
            "failed_steps": list(self.failed_steps),
            "repair_count": self.repair_count,
            "retry_count": self.retry_count,
        }


@dataclass
class WorldStateV2:
    """Enhanced canonical desktop state with observation types and grounding."""
    
    # Core desktop context
    context: DesktopContext = field(default_factory=DesktopContext)
    
    # Task execution context
    task_ctx: TaskContext = field(default_factory=TaskContext)
    
    # Structured grounded targets (visual grounding)
    targets: List[GroundedTarget] = field(default_factory=list)
    
    # Rich observations with provenance
    observations: List[RichObservation] = field(default_factory=list)
    
    # Legacy compatible fields
    visible_targets: List[str] = field(default_factory=list)
    dialogs: List[str] = field(default_factory=list)
    
    # Lock for thread safety
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)
    
    # Metadata
    revision: int = 0
    timestamp: str = field(default_factory=_now)
    
    # Legacy compatibility
    @property
    def active_application(self) -> Optional[str]:
        return self.context.active_application
    
    @active_application.setter
    def active_application(self, value: Optional[str]) -> None:
        self.context.active_application = value
    
    @property
    def active_window(self) -> Optional[str]:
        return self.context.active_window
    
    @active_window.setter
    def active_window(self, value: Optional[str]) -> None:
        self.context.active_window = value
    
    @property
    def task_state(self) -> str:
        return self.task_ctx.task_state
    
    @task_state.setter
    def task_state(self, value: str) -> None:
        self.task_ctx.task_state = value
    
    @property
    def confidence(self) -> float:
        """Aggregate confidence from all observations."""
        if not self.observations:
            return 0.0
        total = sum(obs.confidence for obs in self.observations)
        return total / len(self.observations)
    
    @property
    def application(self) -> Optional[str]:
        return self.active_application
    
    @property
    def window(self) -> Optional[str]:
        return self.active_window
    
    @property
    def elements(self) -> List[str]:
        return self.visible_targets
    
    @property
    def modal(self) -> Optional[str]:
        return self.context.active_modal
    
    @property
    def current_state(self) -> str:
        return self.task_state
    
    @staticmethod
    def _confidence(value: Any, default: float = 0.0) -> float:
        try:
            return max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            return default
    
    def reset(self, task: Optional[str] = None) -> None:
        with self._lock:
            self.context = DesktopContext()
            self.task_ctx = TaskContext(task=task, task_state="PLANNING" if task else "UNKNOWN")
            self.targets = []
            self.observations = []
            self.visible_targets = []
            self.dialogs = []
            self.revision = 0
            self.timestamp = _now()
    
    def observe(
        self,
        key: str,
        value: Any,
        observation_type: ObservationType = ObservationType.OBSERVED,
        confidence: float = 0.8,
        source: str = "vision",
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a rich observation with provenance."""
        with self._lock:
            obs = RichObservation(
                key=key,
                value=value,
                observation_type=observation_type,
                confidence=confidence,
                timestamp=_now(),
                source=source,
                evidence=evidence or {},
            )
            self.observations.append(obs)
            self.observations = self.observations[-100:]  # Keep last 100
            
            # Update the corresponding field
            self._apply_observation(obs)
            self.revision += 1
            self.timestamp = _now()
    
    def _apply_observation(self, obs: RichObservation) -> None:
        """Apply an observation to the appropriate field."""
        key = obs.key
        value = obs.value
        
        if key == "active_application":
            self.context.active_application = value
        elif key == "active_window":
            self.context.active_window = value
        elif key == "focused_element":
            self.context.focused_element = value
        elif key == "visible_controls":
            if isinstance(value, list):
                self.context.visible_controls = _unique(value)
        elif key == "dialogs":
            if isinstance(value, list):
                self.dialogs = _unique(value)
                self.context.dialogs = self.dialogs
        elif key == "mouse_position":
            if isinstance(value, (list, tuple)) and len(value) == 2:
                self.context.mouse_position = (float(value[0]), float(value[1]))
        elif key == "notifications":
            if isinstance(value, list):
                self.context.notifications = _unique(value)
        elif key == "known_errors":
            if isinstance(value, list):
                self.context.known_errors = _unique(value)
        elif key == "downloads":
            if isinstance(value, list):
                self.context.downloads = value
        elif key == "visible_targets":
            if isinstance(value, list):
                self.visible_targets = _unique(value)
    
    def add_target(self, target: GroundedTarget) -> None:
        """Add a grounded target to the world state."""
        with self._lock:
            # Update or append
            for i, existing in enumerate(self.targets):
                if existing.name == target.name:
                    self.targets[i] = target
                    return
            self.targets.append(target)
            self.visible_targets.append(target.name)
    
    def find_target(self, name: str, fuzzy: bool = False) -> Optional[GroundedTarget]:
        """Find a target by name with optional fuzzy matching."""
        with self._lock:
            name_lower = name.lower()
            for target in self.targets:
                if target.name.lower() == name_lower:
                    return target
                if fuzzy and (name_lower in target.name.lower() or 
                              target.name.lower() in name_lower):
                    return target
            return None
    
    def get_targets_by_role(self, role: str) -> List[GroundedTarget]:
        """Get all targets with a specific role."""
        with self._lock:
            return [t for t in self.targets if t.role == role]
    
    def get_safe_to_click_targets(self) -> List[GroundedTarget]:
        """Get all targets that are safe to click."""
        with self._lock:
            return [t for t in self.targets if t.safe_to_click and t.state == "enabled"]
    
    def update_from_vision(self, vision_result: Dict[str, Any]) -> Dict[str, Any]:
        """Update world state from structured vision output."""
        with self._lock:
            # Process grounded targets
            targets = vision_result.get("targets", vision_result.get("grounded_targets", []))
            for t in targets:
                if isinstance(t, dict):
                    target = GroundedTarget.from_dict(t)
                    self.add_target(target)
            
            # Process other observations
            for key in ("active_application", "active_window", "focused_element", 
                       "mouse_position", "visible_controls", "dialogs", "notifications"):
                if key in vision_result:
                    confidence = float(vision_result.get(f"{key}_confidence", 0.8))
                    obs_type = ObservationType.OBSERVED
                    self.observe(key, vision_result[key], obs_type, confidence, "vision")
            
            self.revision += 1
            self.timestamp = _now()
            return self.to_dict()
    
    def expect(self, key: str, value: Any) -> None:
        """Mark something as expected (planner assumption)."""
        self.observe(key, value, ObservationType.EXPECTED, 0.5, "planner")
    
    def infer(self, key: str, value: Any, evidence: Dict[str, Any]) -> None:
        """Mark something as inferred (deduced from context)."""
        self.observe(key, value, ObservationType.INFERRED, 0.6, "inference", evidence)
    
    def satisfies_condition(self, condition: str) -> Dict[str, Any]:
        """Check if the world state satisfies a condition."""
        wanted = str(condition or "").strip().casefold()
        if not wanted:
            return {"matched": True, "confidence": 1.0, "detail": "empty condition"}
        
        haystack_parts = []
        
        # Add observable values
        if self.context.active_application:
            haystack_parts.append(self.context.active_application)
        if self.context.active_window:
            haystack_parts.append(self.context.active_window)
        haystack_parts.extend(self.visible_targets)
        haystack_parts.extend(self.dialogs)
        if self.context.focused_element:
            haystack_parts.append(self.context.focused_element)
        
        haystack = " ".join(filter(None, haystack_parts)).casefold()
        
        tokens = [token for token in re.findall(r"[a-z0-9]+", wanted) if len(token) > 2]
        matched_tokens = [token for token in tokens if token in haystack]
        ratio = len(matched_tokens) / len(tokens) if tokens else 0.0
        
        return {
            "matched": ratio >= 0.7,
            "confidence": round(min(self.confidence, ratio), 3),
            "detail": f"world-state token match {len(matched_tokens)}/{len(tokens)}",
        }
    
    def begin_task(self, task: str, state: str = "PLANNING") -> None:
        with self._lock:
            self.task_ctx.task = task
            self.task_ctx.task_state = state
            self.timestamp = _now()
            self.revision += 1
    
    def mark_step(self, step: str, success: bool) -> None:
        with self._lock:
            if success:
                if step not in self.task_ctx.completed_steps:
                    self.task_ctx.completed_steps.append(step)
                if step in self.task_ctx.failed_steps:
                    self.task_ctx.failed_steps.remove(step)
            else:
                if step not in self.task_ctx.failed_steps:
                    self.task_ctx.failed_steps.append(step)
            self.task_ctx.plan_position = len(self.task_ctx.completed_steps)
    
    def increment_repair(self) -> None:
        with self._lock:
            self.task_ctx.repair_count += 1
    
    def increment_retry(self) -> None:
        with self._lock:
            self.task_ctx.retry_count += 1
    
    def finish_task(self, success: bool) -> None:
        with self._lock:
            self.task_ctx.task_state = "SUCCESS" if success else "FAILURE"
            self.timestamp = _now()
            self.revision += 1
    
    def to_dict(self, include_history: bool = True) -> Dict[str, Any]:
        with self._lock:
            data = {
                # Context
                "context": self.context.to_dict(),
                
                # Task context
                "task_ctx": self.task_ctx.to_dict(),
                
                # Grounded targets
                "targets": [t.to_dict() for t in self.targets],
                
                # Legacy compatible fields
                "active_application": self.context.active_application,
                "active_window": self.context.active_window,
                "visible_targets": list(self.visible_targets),
                "dialogs": list(self.dialogs),
                "task": self.task_ctx.task,
                "task_state": self.task_ctx.task_state,
                "confidence": round(self.confidence, 4),
                "timestamp": self.timestamp,
                "revision": self.revision,
                
                # Legacy compatibility
                "application": self.context.active_application,
                "window": self.context.active_window,
                "elements": list(self.visible_targets),
                "modal": self.context.active_modal,
                "current_state": self.task_ctx.task_state,
            }
            
            if include_history:
                data["observations"] = [obs.to_dict() for obs in self.observations]
            
            return data
    
    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "WorldStateV2":
        data = data if isinstance(data, dict) else {}
        
        # Parse context
        ctx_data = data.get("context", {})
        context = DesktopContext(
            active_application=ctx_data.get("active_application", data.get("active_application")),
            active_window=ctx_data.get("active_window", data.get("active_window")),
            active_window_id=ctx_data.get("active_window_id"),
            focused_element=ctx_data.get("focused_element"),
            focused_element_type=ctx_data.get("focused_element_type"),
            visible_controls=ctx_data.get("visible_controls", []),
            dialogs=ctx_data.get("dialogs", data.get("dialogs", [])),
            active_modal=ctx_data.get("active_modal"),
            notifications=ctx_data.get("notifications", []),
            known_errors=ctx_data.get("known_errors", []),
            downloads=ctx_data.get("downloads", []),
        )
        
        # Parse task context
        task_ctx_data = data.get("task_ctx", {})
        task_ctx = TaskContext(
            task=data.get("task"),
            task_state=str(data.get("task_state", data.get("current_state", "UNKNOWN"))),
            completed_steps=task_ctx_data.get("completed_steps", data.get("completed_states", [])),
            failed_steps=task_ctx_data.get("failed_steps", data.get("failed_states", [])),
            repair_count=task_ctx_data.get("repair_count", 0),
            retry_count=task_ctx_data.get("retry_count", 0),
        )
        
        # Parse targets
        targets = [GroundedTarget.from_dict(t) for t in data.get("targets", [])]
        
        # Parse observations
        observations = []
        for obs_data in data.get("observations", []):
            if isinstance(obs_data, dict):
                obs_type = ObservationType(obs_data.get("observation_type", "unknown"))
                observations.append(RichObservation(
                    key=str(obs_data.get("key", "")),
                    value=obs_data.get("value"),
                    observation_type=obs_type,
                    confidence=float(obs_data.get("confidence", 0.0)),
                    timestamp=str(obs_data.get("timestamp", _now())),
                    source=str(obs_data.get("source", "")),
                    evidence=obs_data.get("evidence", {}),
                ))
        
        return cls(
            context=context,
            task_ctx=task_ctx,
            targets=targets,
            observations=observations,
            visible_targets=data.get("visible_targets", data.get("elements", [])),
            dialogs=data.get("dialogs", []),
            revision=int(data.get("revision", 0)),
            timestamp=str(data.get("timestamp", _now())),
        )
    
    def to_legacy_dict(self) -> Dict[str, Any]:
        """Convert to legacy WorldState format for compatibility."""
        return {
            "active_application": self.context.active_application,
            "active_window": self.context.active_window,
            "visible_targets": list(self.visible_targets),
            "dialogs": list(self.dialogs),
            "task": self.task_ctx.task,
            "task_state": self.task_ctx.task_state,
            "confidence": round(self.confidence, 4),
            "timestamp": self.timestamp,
            "revision": self.revision,
            "completed_states": self.task_ctx.completed_steps,
            "failed_states": self.task_ctx.failed_steps,
            "observations": [obs.to_dict() for obs in self.observations],
            # Legacy compatibility
            "application": self.context.active_application,
            "window": self.context.active_window,
            "elements": list(self.visible_targets),
            "modal": self.context.active_modal,
            "current_state": self.task_ctx.task_state,
        }
    
    @classmethod
    def from_legacy(cls, legacy_data: Dict[str, Any]) -> "WorldStateV2":
        """Create from legacy WorldState format."""
        return cls.from_dict(legacy_data)
    
    def save(self, path: str) -> str:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        temporary.replace(target)
        return str(target)
    
    @classmethod
    def load(cls, path: str) -> "WorldStateV2":
        target = Path(path).expanduser().resolve()
        try:
            return cls.from_dict(json.loads(target.read_text(encoding="utf-8")))
        except Exception:
            return cls()


# Factory function for backward compatibility
def create_world_state(data: Optional[Dict[str, Any]] = None) -> WorldStateV2:
    """Create a WorldStateV2 from optional dict data."""
    if data is None:
        return WorldStateV2()
    return WorldStateV2.from_dict(data)
