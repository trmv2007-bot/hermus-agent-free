"""Unified HERMUS learning projection.

Read-only view over the existing learning owners. This module does not create a
second memory database, skill registry, episode store, or self-improvement loop.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class LearningFabric:
    def snapshot(self, *, limit: int = 12) -> dict[str, Any]:
        limit = max(1, min(int(limit), 50))
        out: dict[str, Any] = {
            "version": 1,
            "generated_at": _now(),
            "skills": {},
            "lessons": {},
            "episodes": {},
            "self_improvement": {},
        }

        try:
            from .skill_forge import skill_forge
            out["skills"] = {
                "stats": skill_forge.stats(),
                "recent": list(skill_forge.index().get("skills", {}).items())[-limit:],
            }
        except Exception as exc:
            out["skills"] = {"error": f"{type(exc).__name__}: {exc}"}

        try:
            from .reasoning.lessons import lessons_store
            out["lessons"] = {
                "stats": lessons_store.stats(),
                "recent": lessons_store.recent(limit=limit),
            }
        except Exception as exc:
            out["lessons"] = {"error": f"{type(exc).__name__}: {exc}"}

        try:
            from .computer import get_episode_store
            store = get_episode_store()
            out["episodes"] = {
                "stats": store.stats(),
                "recent": store.list(limit=limit),
            }
        except Exception as exc:
            out["episodes"] = {"error": f"{type(exc).__name__}: {exc}"}

        try:
            from .self_improvement import self_improvement
            history = self_improvement._load_history()
            out["self_improvement"] = {
                "active": bool(self_improvement.is_reflecting),
                "current": self_improvement.current_reflection,
                "last": self_improvement.last_reflection,
                "recent": history[-limit:],
            }
        except Exception as exc:
            out["self_improvement"] = {"error": f"{type(exc).__name__}: {exc}"}

        totals = {
            "skills": out["skills"].get("stats", {}).get("registered_skills", 0)
            if isinstance(out["skills"], dict) else 0,
            "lessons": out["lessons"].get("stats", {}).get("total", 0)
            if isinstance(out["lessons"], dict) else 0,
            "episodes": out["episodes"].get("stats", {}).get("total", 0)
            if isinstance(out["episodes"], dict) else 0,
        }
        out["totals"] = totals
        return out


learning_fabric = LearningFabric()

__all__ = ["LearningFabric", "learning_fabric"]
