"""Explicit HERMUS Teach Mode.

Teach Mode observes an actual RunBus execution and hands the resulting
trajectory to the existing SkillForge. It never writes an executable skill
directly and never bypasses verification, dedupe or quarantine.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from .run_events import run_bus
from .workspace import workspace


class TeachMode:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else workspace.dirs["logs"] / "teach_sessions.json"
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            self._sessions = raw

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._sessions, indent=2, default=str), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def start(
        self, goal: str, *, run_id: str | None = None, user_id: str = "default", project: str | None = None
    ) -> dict[str, Any]:
        goal = str(goal or "").strip()
        if not goal:
            raise ValueError("goal required")
        if run_id and run_bus.get(run_id) is None:
            raise ValueError(f"run '{run_id}' not found")
        sid = f"teach_{uuid4().hex[:12]}"
        record = {
            "id": sid,
            "goal": goal[:1000],
            "run_id": run_id,
            "user_id": str(user_id or "default"),
            "project": project,
            "status": "recording" if run_id else "waiting_for_run",
        }
        with self._lock:
            self._sessions[sid] = record
            self._save()
        return record

    def attach(self, session_id: str, run_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(str(session_id))
            if session is None:
                raise ValueError("teach session not found")
            if run_bus.get(run_id) is None:
                raise ValueError(f"run '{run_id}' not found")
            session["run_id"] = run_id
            session["status"] = "recording"
            self._save()
            return dict(session)

    def _capture(self, session: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
        run_id = str(session.get("run_id") or "")
        if not run_id:
            raise ValueError("teach session has no run_id")
        events = run_bus.history(run_id, after=0, limit=2000)
        trajectory: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        verification: dict[str, Any] | None = None

        tool_calls: dict[int, list[dict[str, Any]]] = {}
        for event in events:
            kind = str(event.get("type") or "")
            data = dict(event.get("data") or {})
            step = int(data.get("step") or 0)
            if kind == "tool_call":
                tool_calls.setdefault(step, []).append(
                    {
                        "name": data.get("tool") or data.get("name"),
                        "arguments": data.get("args") or {},
                    }
                )
            elif kind == "tool_result":
                tool_results.append(
                    {
                        "tool": data.get("tool") or data.get("name"),
                        "args": data.get("args") or {},
                        "result": {
                            "success": not bool(data.get("error")),
                            "error": data.get("error"),
                            "preview": data.get("preview"),
                        },
                        "step": step,
                    }
                )
            elif kind in {"verification", "mission_verification"}:
                verification = data

        for step in sorted(tool_calls):
            trajectory.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": tool_calls[step],
                }
            )

        final = run_bus.snapshot(run_id)
        result = final.get("result") if isinstance(final, dict) else None
        answer = ""
        if isinstance(result, dict):
            answer = str(result.get("response") or result.get("final_answer") or result.get("final_proof") or "")
        if answer:
            trajectory.append({"role": "assistant", "content": answer, "tool_calls": []})
        return trajectory, tool_results, verification

    def preview(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = dict(self._sessions.get(str(session_id)) or {})
        if not session:
            raise ValueError("teach session not found")
        trajectory, tool_results, verification = self._capture(session)
        return {
            "session": session,
            "trajectory": trajectory,
            "tool_results": tool_results,
            "verification": verification,
            "run": run_bus.snapshot(str(session.get("run_id") or "")),
        }

    def harvest(self, session_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        preview = self.preview(session_id)
        from .skill_forge import skill_forge

        result = skill_forge.harvest(
            str(preview["session"].get("goal") or ""),
            preview["trajectory"],
            verification=preview.get("verification"),
            tool_results=preview.get("tool_results"),
            session_id=str(preview["session"].get("id") or session_id),
            dry_run=bool(dry_run),
        )
        with self._lock:
            session = self._sessions[str(session_id)]
            session["status"] = "harvested" if result.get("created") else "evaluated"
            session["result"] = {
                "created": result.get("created"),
                "name": result.get("name"),
                "stage": result.get("stage"),
                "evaluation": result.get("evaluation"),
                "proof": result.get("proof"),
            }
            self._save()
        return {"session": dict(session), "harvest": result}

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._sessions.values())[-max(1, int(limit)) :]
            return [dict(row) for row in reversed(rows)]


teach_mode = TeachMode()

__all__ = ["TeachMode", "teach_mode"]
