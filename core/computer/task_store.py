"""Crash-safe persistence and resume support for computer tasks."""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .world_state import WorldState


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    if not safe:
        raise ValueError("task id must contain a letter or number")
    return safe


def _unique(values: List[Any]) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


@dataclass
class TaskCheckpoint:
    task_id: str
    task: str
    status: str = "created"  # created|planning|running|interrupted|failed|success|cancelled
    plan: List[Dict[str, Any]] = field(default_factory=list)
    graph: Dict[str, Any] = field(default_factory=dict)
    current_state: Optional[str] = None
    completed_states: List[str] = field(default_factory=list)
    pending_states: List[str] = field(default_factory=list)
    failed_states: List[str] = field(default_factory=list)
    known_failures: List[Dict[str, Any]] = field(default_factory=list)
    repairs: List[Dict[str, Any]] = field(default_factory=list)
    world_state: Dict[str, Any] = field(default_factory=dict)
    recordings: List[str] = field(default_factory=list)
    attempts: int = 0
    resume_count: int = 0
    last_event: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskCheckpoint":
        fields = cls.__dataclass_fields__
        clean = {key: value for key, value in (data or {}).items() if key in fields}
        clean.setdefault("task_id", str(data.get("task_id") or ""))
        clean.setdefault("task", str(data.get("task") or ""))
        return cls(**clean)


class TaskStore:
    """Atomically persist task graphs, checkpoints, and resume metadata."""

    def __init__(self, root: str = "data/recordings"):
        root_path = Path(root).expanduser()
        if not root_path.is_absolute() and root == "data/recordings":
            root_path = Path(__file__).resolve().parents[2] / root_path
        self.root = root_path.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, task_id: str) -> Path:
        return self.root / _safe_id(task_id)

    def state_path(self, task_id: str) -> Path:
        return self.directory(task_id) / "state.json"

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
            path.parent.chmod(0o700)
        except OSError:
            pass

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def initialize(
        self,
        task_id: str,
        task: str,
        plan: List[Dict[str, Any]],
        graph: Optional[Dict[str, Any]] = None,
        world_state: Optional[WorldState] = None,
        resume: bool = False,
    ) -> TaskCheckpoint:
        task_id = _safe_id(task_id)
        existing = self.load(task_id)
        if resume and existing is not None:
            existing.resume_count += 1
            existing.attempts += 1
            existing.status = "running"
            existing.error = None
            existing.updated_at = _now()
            if world_state is not None:
                existing.world_state = world_state.to_dict()
            self.save(existing)
            return existing

        names = [str(step.get("name") or f"STATE_{index}") for index, step in enumerate(plan)]
        checkpoint = TaskCheckpoint(
            task_id=task_id,
            task=task,
            status="running",
            plan=list(plan),
            graph=dict(graph or {}),
            current_state=names[0] if names else None,
            pending_states=names,
            world_state=(world_state or WorldState(task=task, task_state="PLANNING")).to_dict(),
            attempts=1,
        )
        self.directory(task_id).mkdir(parents=True, exist_ok=True)
        self._write_json(self.directory(task_id) / "plan.json", checkpoint.graph or {"task": task, "nodes": plan})
        self.save(checkpoint)
        return checkpoint

    def save(self, checkpoint: TaskCheckpoint) -> str:
        checkpoint.updated_at = _now()
        checkpoint.completed_states = _unique(checkpoint.completed_states)
        checkpoint.pending_states = _unique(checkpoint.pending_states)
        checkpoint.failed_states = _unique(checkpoint.failed_states)
        checkpoint.recordings = _unique(checkpoint.recordings)
        checkpoint.known_failures = [item for item in checkpoint.known_failures if isinstance(item, dict)][-100:]
        checkpoint.repairs = [item for item in checkpoint.repairs if isinstance(item, dict)][-100:]
        path = self.state_path(checkpoint.task_id)
        self._write_json(path, checkpoint.to_dict())
        # Keep the v2 filename readable by older tooling.
        self._write_json(self.directory(checkpoint.task_id) / "task_state.json", checkpoint.to_dict())
        return str(path)

    def load(self, task_id: str) -> Optional[TaskCheckpoint]:
        path = self.state_path(task_id)
        if not path.exists():
            legacy = self.directory(task_id) / "task_state.json"
            path = legacy if legacy.exists() else path
        data = self._read_json(path, None)
        return TaskCheckpoint.from_dict(data) if isinstance(data, dict) else None

    def load_graph(self, task_id: str) -> Dict[str, Any]:
        checkpoint = self.load(task_id)
        if checkpoint and checkpoint.graph:
            return dict(checkpoint.graph)
        return self._read_json(self.directory(task_id) / "plan.json", {})

    def checkpoint_event(
        self,
        checkpoint: TaskCheckpoint,
        event: Dict[str, Any],
        world_state: Optional[WorldState] = None,
    ) -> TaskCheckpoint:
        state = str(event.get("state") or "")
        phase = event.get("phase")
        outcome = event.get("outcome")
        checkpoint.last_event = dict(event)
        checkpoint.status = "running"
        if state:
            checkpoint.current_state = state

        if phase == "original_action" and outcome == "success" and state:
            checkpoint.completed_states.append(state)
            checkpoint.pending_states = [item for item in checkpoint.pending_states if item != state]
            checkpoint.failed_states = [item for item in checkpoint.failed_states if item != state]
        elif phase == "original_action" and outcome == "failure" and state:
            checkpoint.failed_states.append(state)
        elif phase == "transition" and event.get("next_state"):
            checkpoint.current_state = str(event["next_state"])
        elif phase == "diagnose":
            checkpoint.known_failures.append({
                "state": state,
                "attempt": event.get("attempt"),
                "reason": event.get("failure_reason"),
                "diagnosis": event.get("diagnosis", {}),
                "timestamp": _now(),
            })
        elif phase == "repair":
            checkpoint.repairs.append({
                "state": event.get("repair_state"),
                "repair_for": event.get("repair_for"),
                "action": event.get("action_spec"),
                "verification": event.get("verification"),
                "outcome": outcome,
                "timestamp": _now(),
            })
        elif phase == "terminal":
            checkpoint.status = "success" if event.get("success") else "failed"
            checkpoint.current_state = state or checkpoint.current_state

        if world_state is not None:
            checkpoint.world_state = world_state.to_dict()
        self.save(checkpoint)
        return checkpoint

    def complete(
        self,
        checkpoint: TaskCheckpoint,
        success: bool,
        result: Dict[str, Any],
        world_state: Optional[WorldState] = None,
        recording: Optional[str] = None,
    ) -> TaskCheckpoint:
        checkpoint.status = "success" if success else "failed"
        checkpoint.error = result.get("error")
        checkpoint.result = {
            "success": success,
            "result": result.get("result"),
            "error": result.get("error"),
            "failure": result.get("failure"),
            "duration": result.get("duration"),
        }
        if world_state is not None:
            checkpoint.world_state = world_state.to_dict()
        if recording:
            checkpoint.recordings.append(recording)
        if success:
            checkpoint.pending_states = []
            checkpoint.current_state = "SUCCESS"
        self.save(checkpoint)
        return checkpoint

    def mark_interrupted(self, task_id: str, reason: str = "process interrupted") -> Optional[TaskCheckpoint]:
        checkpoint = self.load(task_id)
        if checkpoint is None:
            return None
        checkpoint.status = "interrupted"
        checkpoint.error = reason
        self.save(checkpoint)
        return checkpoint

    def next_state(self, checkpoint: TaskCheckpoint) -> Optional[str]:
        names = [str(step.get("name") or f"STATE_{index}") for index, step in enumerate(checkpoint.plan)]
        completed = set(checkpoint.completed_states)
        if checkpoint.current_state in names and checkpoint.current_state not in completed:
            return checkpoint.current_state
        return next((name for name in names if name not in completed), None)

    def list(self) -> List[Dict[str, Any]]:
        output: List[Dict[str, Any]] = []
        if not self.root.exists():
            return output
        for path in sorted(self.root.iterdir(), reverse=True):
            if not path.is_dir():
                continue
            checkpoint = self.load(path.name)
            if checkpoint is None:
                continue
            output.append({
                "task_id": checkpoint.task_id,
                "task": checkpoint.task,
                "status": checkpoint.status,
                "current_state": checkpoint.current_state,
                "completed": len(checkpoint.completed_states),
                "pending": len(checkpoint.pending_states),
                "resume_count": checkpoint.resume_count,
                "updated_at": checkpoint.updated_at,
            })
        return output
