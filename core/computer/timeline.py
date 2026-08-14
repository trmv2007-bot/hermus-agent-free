"""Chronological visual-event timelines and task artifact bundles."""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class TimelineEvent:
    offset: float
    type: str
    description: str
    confidence: float = 0.0
    timestamp: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["offset"] = round(float(data["offset"]), 3)
        data["confidence"] = round(max(0.0, min(float(data["confidence"]), 1.0)), 3)
        return data


class Timeline:
    def __init__(self, task: str = "", recording: Optional[str] = None, started: Optional[str] = None):
        self.task = task
        self.recording = recording
        self.started = started or datetime.now().astimezone().isoformat()
        self.events: List[TimelineEvent] = []

    def add(
        self,
        offset: float,
        event_type: str,
        description: str,
        confidence: float = 0.0,
        timestamp: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> TimelineEvent:
        event = TimelineEvent(
            offset=max(0.0, float(offset or 0.0)),
            type=event_type,
            description=str(description or ""),
            confidence=confidence,
            timestamp=timestamp,
            evidence=evidence or {},
        )
        self.events.append(event)
        self.events.sort(key=lambda item: item.offset)
        return event

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "recording": self.recording,
            "started": self.started,
            "generated_at": datetime.now().astimezone().isoformat(),
            "events": [event.to_dict() for event in self.events],
            "count": len(self.events),
        }

    def save(self, path: str) -> str:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_json(target, self.to_dict())
        return str(target)

    def render_text(self) -> str:
        lines = [f"Task: {self.task or 'Screen recording'}", ""]
        for event in self.events:
            minutes, seconds = divmod(max(0, int(event.offset)), 60)
            lines.append(f"{minutes:02d}:{seconds:02d} ─ {event.description}")
        return "\n".join(lines)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return f"<{len(value)} compressed bytes>"
    return str(value)


def _write_json(path: Path, data: Any) -> None:
    """Write JSON atomically so interrupted recording services leave no half-file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, default=_json_default), encoding="utf-8")
    temporary.replace(path)


class TaskArtifacts:
    """Persist the debuggable recording/timeline/actions/result task layout."""

    FILES = ("timeline.json", "events.json", "actions.json", "result.json")

    def __init__(self, task_id: str, root: str = "data/recordings"):
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", task_id.strip()).strip(".-")
        if not safe:
            raise ValueError("task_id must contain a letter or number")
        self.task_id = safe
        root_path = Path(root).expanduser()
        if not root_path.is_absolute() and root == "data/recordings":
            root_path = Path(__file__).resolve().parents[2] / root_path
        self.directory = (root_path.resolve() / safe)
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass

    def write(
        self,
        timeline: Optional[Any] = None,
        events: Optional[Iterable[Dict[str, Any]]] = None,
        actions: Optional[Iterable[Dict[str, Any]]] = None,
        result: Optional[Dict[str, Any]] = None,
        recording_path: Optional[str] = None,
        copy_recording: bool = True,
    ) -> Dict[str, Any]:
        if isinstance(timeline, Timeline):
            timeline_data = timeline.to_dict()
        else:
            timeline_data = timeline or {"task": self.task_id, "events": [], "count": 0}
        _write_json(self.directory / "timeline.json", timeline_data)
        _write_json(self.directory / "events.json", list(events or []))
        _write_json(self.directory / "actions.json", list(actions or []))
        _write_json(self.directory / "result.json", result or {})

        recording = None
        if recording_path:
            source = Path(recording_path).expanduser().resolve()
            if not source.exists():
                raise FileNotFoundError(str(source))
            target = self.directory / f"recording{source.suffix.lower()}"
            if source != target:
                if copy_recording:
                    shutil.copy2(source, target)
                else:
                    shutil.move(str(source), str(target))
            recording = str(target)
            try:
                target.chmod(0o600)
            except OSError:
                pass

        manifest = {
            "success": True,
            "task_id": self.task_id,
            "directory": str(self.directory),
            "recording": recording,
            "timeline": str(self.directory / "timeline.json"),
            "events": str(self.directory / "events.json"),
            "actions": str(self.directory / "actions.json"),
            "result": str(self.directory / "result.json"),
            "manifest": str(self.directory / "manifest.json"),
        }
        _write_json(self.directory / "manifest.json", manifest)
        return manifest
