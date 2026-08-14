"""Privacy and path policy for screen recordings.

Tool-level consent remains enforced by :mod:`core.permissions`.  This module
adds storage-specific safeguards: private file modes, bounded capture settings,
and a default rule that agent-created recordings stay under data/recordings.
"""
from __future__ import annotations

import re
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
