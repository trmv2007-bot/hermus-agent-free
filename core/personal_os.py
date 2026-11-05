"""Phase 15 personal operating system layer.

Provides a durable personal control-plane view over goals, projects, tasks,
routines, world state and scheduled/proactive work. It does not execute tools
itself; execution is delegated to the canonical JobQueue/runtime.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass
class POSTask:
    id: str
    title: str
    status: str = "open"
    priority: str = "normal"
    area: str = "general"
    project: str | None = None
    due: str | None = None
    notes: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PersonalOS:
    """Unified personal control plane backed by durable local state."""

    def __init__(self, path: str | Path = "data/personal_os.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.tasks: dict[str, POSTask] = {}
        self._load()

    def add_task(
        self,
        title: str,
        *,
        priority: str = "normal",
        area: str = "general",
        project: str | None = None,
        due: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            raise ValueError("task title is required")
        task = POSTask(
            id=f"task_{uuid4().hex[:12]}",
            title=title[:500],
            priority=str(priority or "normal")[:30],
            area=str(area or "general")[:80],
            project=str(project)[:120] if project else None,
            due=str(due)[:100] if due else None,
            notes=str(notes or "")[:2000],
        )
        with self._lock:
            self.tasks[task.id] = task
            self._save()
        return task.to_dict()

    def update_task(self, task_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            task = self.tasks.get(str(task_id))
            if task is None:
                raise KeyError("task not found")
            for key in ("title", "priority", "area", "project", "due", "notes", "status"):
                if key in changes and changes[key] is not None:
                    setattr(task, key, str(changes[key])[:2000])
            if task.status in {"done", "completed"} and task.completed_at is None:
                task.completed_at = time.time()
                task.status = "done"
            elif task.status not in {"done", "completed"}:
                task.completed_at = None
            self._save()
            return task.to_dict()

    def complete_task(self, task_id: str) -> dict[str, Any]:
        return self.update_task(task_id, status="done")

    def list_tasks(
        self,
        *,
        status: str | None = None,
        area: str | None = None,
        project: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self.tasks.values())
        if status:
            rows = [t for t in rows if t.status == status]
        if area:
            rows = [t for t in rows if t.area.lower() == str(area).lower()]
        if project:
            rows = [t for t in rows if (t.project or "").lower() == str(project).lower()]
        priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
        rows.sort(key=lambda t: (priority_order.get(t.priority.lower(), 2), t.due or "9999", t.created_at))
        return [t.to_dict() for t in rows[: max(1, min(500, int(limit)))]]

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            if str(task_id) not in self.tasks:
                return False
            del self.tasks[str(task_id)]
            self._save()
            return True

    def execute_task(self, task_id: str) -> dict[str, Any]:
        """Queue a task through the canonical runtime; never execute tools here."""
        with self._lock:
            task = self.tasks.get(str(task_id))
            if task is None:
                return {"success": False, "error": "task_not_found"}
            if task.status in {"done", "completed"}:
                return {"success": False, "error": "task_already_complete", "task": task.to_dict()}
            payload = {
                "text": task.title,
                "task": task.title,
                "platform": "personal_os",
                "personal_os_task_id": task.id,
                "area": task.area,
                "project": task.project,
                "priority": task.priority,
            }
        try:
            from gateway.queue import job_queue

            job = job_queue.submit(
                "runtime.turn",
                payload,
                session_key="personal_os",
                priority=0 if task.priority == "urgent" else 1,
            )
            return {"success": True, "task": task.to_dict(), "job_id": job.id, "run_id": job.run_id}
        except Exception as exc:
            return {"success": False, "error": str(exc)[:300], "task": task.to_dict()}

    def snapshot(self, *, query: str = "", area: str | None = None, project: str | None = None) -> dict[str, Any]:
        from scheduler.cron import cron_manager

        from .personal_context import personal_context

        try:
            from core.proactive_runtime import automation

            automations = automation.list_rules()
        except Exception:
            automations = []
        try:
            from .world_awareness import world_awareness

            world_status = world_awareness.status()
        except Exception as exc:
            world_status = {"error": str(exc)[:200]}

        context = personal_context.snapshot(query=query, project=project, limit=8).as_dict()
        tasks = self.list_tasks(area=area, project=project, limit=100)
        open_tasks = [t for t in tasks if t["status"] not in {"done", "completed"}]
        return {
            "generated_at": time.time(),
            "focus": context.get("current_focus", ""),
            "goals": context.get("goals", []),
            "projects": context.get("projects", []),
            "tasks": tasks,
            "open_task_count": len(open_tasks),
            "schedules": cron_manager.list_jobs(),
            "automations": automations,
            "world": world_status,
            "relevant_memories": context.get("relevant_memories", []),
        }

    def briefing(self, *, query: str = "", area: str | None = None) -> dict[str, Any]:
        snap = self.snapshot(query=query, area=area)
        open_tasks = [t for t in snap["tasks"] if t["status"] not in {"done", "completed"}]
        urgent = [t for t in open_tasks if t["priority"].lower() in {"urgent", "high"}]
        due = [t for t in open_tasks if t.get("due")]
        active_goals = [g for g in snap["goals"] if str(g.get("status", "active")).lower() in {"active", "in_progress"}]
        return {
            "generated_at": snap["generated_at"],
            "focus": snap["focus"],
            "priority_tasks": urgent[:10],
            "due_tasks": due[:10],
            "active_goals": active_goals[:10],
            "open_task_count": len(open_tasks),
            "schedule_count": len(snap["schedules"]),
            "automation_count": len(snap["automations"]),
            "world": snap["world"],
        }

    def _load(self) -> None:
        try:
            rows = json.loads(self.path.read_text())
        except (OSError, ValueError):
            rows = []
        if not isinstance(rows, list):
            rows = []
        for row in rows:
            if isinstance(row, dict) and row.get("id") and row.get("title"):
                self.tasks[str(row["id"])] = POSTask(
                    id=str(row["id"]),
                    title=str(row["title"]),
                    status=str(row.get("status", "open")),
                    priority=str(row.get("priority", "normal")),
                    area=str(row.get("area", "general")),
                    project=row.get("project"),
                    due=row.get("due"),
                    notes=str(row.get("notes", "")),
                    created_at=float(row.get("created_at", time.time())),
                    completed_at=row.get("completed_at"),
                )

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps([t.to_dict() for t in self.tasks.values()], indent=2, default=str))
            tmp.replace(self.path)
        except OSError:
            pass


personal_os = PersonalOS()

__all__ = ["POSTask", "PersonalOS", "personal_os"]
