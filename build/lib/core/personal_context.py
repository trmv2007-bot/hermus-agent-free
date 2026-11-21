"""Phase 9 personal context layer built on the canonical MemoryFacade.

This is a context/index layer, not a second memory writer. Explicit preferences,
goals, project facts and focus are persisted through MemoryFacade; the layer
combines them with relevant typed memories and recent session history for each
turn. It never infers personal facts from silence or hidden telemetry.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PersonalContextSnapshot:
    preferences: dict[str, Any]
    goals: list[Any]
    projects: list[Any]
    current_focus: str
    relevant_memories: list[dict[str, Any]]
    recent_sessions: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PersonalContext:
    """Persistent personal context composed from the canonical memory facade."""

    def __init__(self, memory=None):
        self.memory = memory

    def _memory(self):
        if self.memory is None:
            from .memory import memory

            self.memory = memory
        return self.memory

    def snapshot(self, *, query: str = "", project: str | None = None, limit: int = 5) -> PersonalContextSnapshot:
        mem = self._memory()
        model = mem.load_user_model() or {}
        query = str(query or "").strip()
        project = project or None

        relevant = []
        recent = []
        if query:
            try:
                relevant = list(mem.recall(query, project=project, limit=max(1, int(limit))))[:limit]
            except Exception:
                relevant = []
            try:
                recent = list(mem.search_sessions(query, limit=max(1, int(limit)), project=project))[:limit]
            except Exception:
                recent = []

        goals = model.get("goals", [])
        projects = model.get("projects", [])
        return PersonalContextSnapshot(
            preferences=dict(model.get("preferences") or {}),
            goals=list(goals) if isinstance(goals, list) else [],
            projects=list(projects) if isinstance(projects, list) else [],
            current_focus=str(model.get("current_focus") or ""),
            relevant_memories=relevant,
            recent_sessions=recent,
        )

    def remember_preference(
        self, key: str, value: Any, *, project: str | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        key = str(key or "").strip()
        if not key:
            return {"success": False, "error": "preference key is required"}
        mem = self._memory()
        mem.update_user_model({"preferences": {key: value}})
        try:
            mem.remember(
                "semantic",
                f"User preference: {key} = {value}",
                project=project,
                importance=8,
                metadata={"context_type": "preference", "session_id": session_id or ""},
            )
        except Exception:
            pass
        return {"success": True, "key": key, "value": value}

    def add_goal(
        self,
        title: str,
        *,
        priority: str = "normal",
        status: str = "active",
        project: str | None = None,
        deadline: str | None = None,
    ) -> dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            return {"success": False, "error": "goal title is required"}
        mem = self._memory()
        model = mem.load_user_model() or {}
        goals = [g for g in (model.get("goals") or []) if isinstance(g, dict)]
        row = {"title": title, "priority": str(priority or "normal"), "status": str(status or "active")}
        if project:
            row["project"] = str(project)
        if deadline:
            row["deadline"] = str(deadline)
        replaced = False
        for i, existing in enumerate(goals):
            if str(existing.get("title", "")).strip().lower() == title.lower():
                goals[i] = {**existing, **row}
                replaced = True
                break
        if not replaced:
            goals.append(row)
        mem.update_user_model({"goals": goals})
        try:
            mem.remember(
                "semantic",
                f"Personal goal: {title} (status={row['status']}, priority={row['priority']})",
                project=project,
                importance=8,
                metadata={"context_type": "goal"},
            )
        except Exception:
            pass
        return {"success": True, "goal": row, "replaced": replaced}

    def set_focus(self, focus: str, *, project: str | None = None) -> dict[str, Any]:
        focus = str(focus or "").strip()
        mem = self._memory()
        payload = {"current_focus": focus}
        if project:
            payload["current_project"] = str(project)
        mem.update_user_model(payload)
        if focus:
            try:
                mem.remember(
                    "working",
                    f"Current focus: {focus}",
                    project=project,
                    importance=7,
                    ttl_hours=24,
                    pinned=False,
                    metadata={"context_type": "focus"},
                )
            except Exception:
                pass
        return {"success": True, "current_focus": focus, "project": project}

    def upsert_project(self, name: str, **details: Any) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            return {"success": False, "error": "project name is required"}
        mem = self._memory()
        model = mem.load_user_model() or {}
        projects = [p for p in (model.get("projects") or []) if isinstance(p, dict)]
        row = {"name": name, **{k: v for k, v in details.items() if v is not None}}
        replaced = False
        for i, existing in enumerate(projects):
            if str(existing.get("name", "")).strip().lower() == name.lower():
                projects[i] = {**existing, **row}
                replaced = True
                break
        if not replaced:
            projects.append(row)
        mem.update_user_model({"projects": projects})
        return {"success": True, "project": row, "replaced": replaced}

    def observe_turn(self, text: str, *, session_id: str = "", project: str | None = None) -> list[dict[str, Any]]:
        """Persist only explicit, recognizable self-statements from a user turn."""
        text = str(text or "").strip()
        if not text:
            return []
        changes: list[dict[str, Any]] = []

        name = re.search(r"^\s*my name is\s+(.+?)\s*[.!?]?\s*$", text, re.I)
        if name:
            changes.append(self.remember_preference("name", name.group(1).strip(" .!?"), project=project, session_id=session_id))

        preference = re.search(r"^\s*i prefer\s+(.+?)\s*[.!?]?\s*$", text, re.I)
        if preference:
            value = preference.group(1).strip(" .!?")
            mem = self._memory()
            model = mem.load_user_model() or {}
            prefs = dict(model.get("preferences") or {})
            items = list(prefs.get("explicit_preferences") or [])
            if value not in items:
                items.append(value)
            prefs["explicit_preferences"] = items[-20:]
            mem.update_user_model({"preferences": prefs})
            try:
                mem.remember(
                    "semantic",
                    f"User explicitly prefers: {value}",
                    project=project,
                    importance=8,
                    metadata={"context_type": "explicit_preference", "session_id": session_id},
                )
            except Exception:
                pass
            changes.append({"success": True, "preference": value})

        like = re.search(r"^\s*i like\s+(.+?)\s*[.!?]?\s*$", text, re.I)
        if like:
            value = like.group(1).strip(" .!?")
            mem = self._memory()
            model = mem.load_user_model() or {}
            prefs = dict(model.get("preferences") or {})
            items = list(prefs.get("likes") or [])
            if value not in items:
                items.append(value)
            prefs["likes"] = items[-20:]
            mem.update_user_model({"preferences": prefs})
            changes.append({"success": True, "like": value})

        use = re.search(r"^\s*i use\s+(.+?)\s*[.!?]?\s*$", text, re.I)
        if use:
            value = use.group(1).strip(" .!?")
            mem = self._memory()
            model = mem.load_user_model() or {}
            prefs = dict(model.get("preferences") or {})
            items = list(prefs.get("tools_or_stack") or [])
            if value not in items:
                items.append(value)
            prefs["tools_or_stack"] = items[-20:]
            mem.update_user_model({"preferences": prefs})
            changes.append({"success": True, "use": value})

        favorite = re.search(r"^\s*my favorite\s+(.+?)\s+is\s+(.+?)\s*[.!?]?\s*$", text, re.I)
        if favorite:
            key = re.sub(r"[^a-z0-9_]+", "_", favorite.group(1).strip().lower()).strip("_") or "item"
            value = favorite.group(2).strip(" .!?")
            changes.append(self.remember_preference(f"favorite_{key}", value, project=project, session_id=session_id))

        remembered = re.search(r"^\s*remember that\s+(.+?)\s*[.!?]?\s*$", text, re.I)
        if remembered:
            fact = remembered.group(1).strip(" .!?")
            mem = self._memory()
            result = mem.remember(
                "semantic",
                fact,
                project=project,
                importance=8,
                metadata={"context_type": "explicit_fact", "session_id": session_id},
            )
            changes.append({"success": bool(result.get("success", True)), "remembered": fact})

        return changes

    def prompt_block(self, *, query: str = "", project: str | None = None, limit: int = 5, max_chars: int = 5000) -> str:
        snap = self.snapshot(query=query, project=project, limit=limit).as_dict()
        if snap["relevant_memories"]:
            snap["relevant_memories"] = [
                {
                    "id": item.get("id"),
                    "kind": item.get("kind"),
                    "project": item.get("project"),
                    "content": str(item.get("content") or "")[:500],
                    "score": item.get("score"),
                }
                for item in snap["relevant_memories"]
            ]
        if snap["recent_sessions"]:
            snap["recent_sessions"] = [
                {
                    "session_id": row.get("session_id"),
                    "role": row.get("role"),
                    "content": str(row.get("content") or "")[:400],
                    "timestamp": row.get("timestamp"),
                }
                for row in snap["recent_sessions"]
            ]
        text = json.dumps(snap, ensure_ascii=False, indent=2, default=str)
        return text[: max(500, int(max_chars))]


personal_context = PersonalContext()

__all__ = ["PersonalContext", "PersonalContextSnapshot", "personal_context"]
