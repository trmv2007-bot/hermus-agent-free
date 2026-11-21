"""HERMUS Focus OS: a concise, read-only priority projection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# Lazy dependency seams keep imports cycle-safe while allowing focused runtime tests
# to replace the canonical providers without being overwritten by local imports.
personal_os = None
presence_kernel = None
device_fabric = None
learning_fabric = None


class FocusOS:
    def snapshot(self, *, query: str = "", area: str | None = None) -> dict[str, Any]:
        global personal_os, presence_kernel, device_fabric, learning_fabric
        if personal_os is None:
            from .personal_os import personal_os as _personal_os

            personal_os = _personal_os
        if presence_kernel is None:
            from .presence_kernel import presence_kernel as _presence_kernel

            presence_kernel = _presence_kernel
        if device_fabric is None:
            from .device_fabric import device_fabric as _device_fabric

            device_fabric = _device_fabric
        if learning_fabric is None:
            from .learning_fabric import learning_fabric as _learning_fabric

            learning_fabric = _learning_fabric

        personal = personal_os.briefing(query=query, area=area)

        try:
            presence = presence_kernel.snapshot(user_id="default", include_events=False)
        except Exception:
            presence = {"attention": [], "runtime": {}, "summary": {}}

        try:
            devices = device_fabric.snapshot()
        except Exception:
            devices = {"safety": {}, "computer": {}, "browser": {}}

        try:
            learning = learning_fabric.snapshot(limit=5)
        except Exception:
            learning = {"totals": {}}

        attention = list(presence.get("attention") or [])
        active_runs = list((presence.get("runtime") or {}).get("active_runs") or [])
        active_goals = list(personal.get("active_goals") or [])
        priority_tasks = list(personal.get("priority_tasks") or [])
        due_tasks = list(personal.get("due_tasks") or [])

        priorities: list[dict[str, Any]] = []
        for item in attention[:4]:
            priorities.append(
                {
                    "kind": "attention",
                    "severity": item.get("severity", "info"),
                    "title": item.get("title", "Attention"),
                    "detail": item.get("detail", ""),
                }
            )
        for item in priority_tasks[:3]:
            priorities.append(
                {
                    "kind": "task",
                    "severity": "high" if str(item.get("priority")).lower() in {"urgent", "high"} else "medium",
                    "title": item.get("title", ""),
                    "detail": item.get("due") or "",
                }
            )
        for item in active_goals[:3]:
            priorities.append(
                {
                    "kind": "goal",
                    "severity": "medium",
                    "title": item.get("title") or item.get("goal") or "",
                    "detail": item.get("status", "active"),
                }
            )

        safety = (devices.get("safety") or {}).get("emergency_stop") or {}
        if safety.get("active"):
            headline = "Emergency stop is active"
        elif priorities:
            headline = str(priorities[0]["title"])
        elif active_runs:
            headline = f"{len(active_runs)} task(s) in progress"
        else:
            headline = "Nothing needs your attention"

        return {
            "version": 1,
            "generated_at": _now(),
            "headline": headline,
            "priorities": priorities[:10],
            "active_runs": active_runs[:10],
            "active_goals": active_goals[:8],
            "due_tasks": due_tasks[:8],
            "world": presence.get("world") or {},
            "safety": safety,
            "learning_totals": learning.get("totals") or {},
            "source": {
                "personal_os": True,
                "presence_kernel": True,
                "device_fabric": True,
                "learning_fabric": True,
            },
        }


focus_os = FocusOS()

__all__ = ["FocusOS", "focus_os"]
