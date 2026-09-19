"""Persistent shared state for the HERMUS control-room layout.

The dashboard is a projection of this state, not the owner of it. Agents can
read the same snapshot that the browser reads and can safely change tabs or
workspace panels through the registered dashboard tools.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import config


TAB_DEFAULTS = (
    ("overview", "Overview"),
    ("agents", "Agents"),
    ("presence", "Presence"),
    ("voice", "Voice"),
    ("jobs", "Jobs"),
    ("missions", "Missions"),
    ("telemetry", "Telemetry"),
    ("computer", "Computer"),
    ("remote", "Remote"),
    ("safety", "Safety"),
    ("systems", "Systems"),
)

PANEL_KINDS = frozenset({"text", "kpi", "feed", "link"})
MAX_PANELS = 100
PROTECTED_TABS = frozenset({"overview", "safety", "systems"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DashboardState:
    """Thread-safe JSON-backed dashboard state."""

    def __init__(self, path: str | None = None):
        self.path = Path(path or config.resolve_path("data/dashboard.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._state = self._load()

    def _default(self) -> dict[str, Any]:
        return {
            "version": 1,
            "revision": 0,
            "updated_at": _now(),
            "tabs": [
                {"id": tab_id, "label": label, "visible": True, "order": index}
                for index, (tab_id, label) in enumerate(TAB_DEFAULTS)
            ],
            "panels": [],
        }

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("dashboard state must be an object")
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
            return self._default()

        state = self._default()
        state.update({key: value.get(key, state[key]) for key in ("version", "revision", "updated_at")})
        state["panels"] = [panel for panel in value.get("panels", []) if isinstance(panel, dict) and panel.get("id")]
        saved_tabs = {str(tab.get("id")): tab for tab in value.get("tabs", []) if isinstance(tab, dict) and tab.get("id")}
        state["tabs"] = []
        for index, (tab_id, label) in enumerate(TAB_DEFAULTS):
            tab = dict(saved_tabs.get(tab_id) or {})
            tab.update({"id": tab_id, "label": tab.get("label") or label, "visible": tab.get("visible", True), "order": tab.get("order", index)})
            state["tabs"].append(tab)
        return state

    def _write(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._state, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def _touch(self) -> None:
        self._state["revision"] = int(self._state.get("revision", 0)) + 1
        self._state["updated_at"] = _now()
        self._write()

    def _copy(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._state))

    def snapshot(self) -> dict[str, Any]:
        """Return dashboard state plus the canonical systems inventory."""
        with self._lock:
            state = self._copy()
        try:
            from . import console

            manifest = console.manifest()
            state["systems"] = [
                {"id": panel["id"], "label": panel["label"], "group": panel["group"], "summary": panel["summary"]}
                for panel in manifest.get("panels", [])
            ]
            state["system_panel_count"] = manifest.get("panel_count", len(state["systems"]))
        except Exception:
            state["systems"] = []
            state["system_panel_count"] = 0
        state["tabs"] = sorted(state["tabs"], key=lambda item: (int(item.get("order", 0)), item["id"]))
        state["panels"] = sorted(
            state.get("panels", []),
            key=lambda item: (int(item.get("order", 0)), item.get("id", "")),
        )
        return state

    def add_panel(
        self,
        title: str,
        content: str = "",
        kind: str = "text",
        order: int | None = None,
        width: int = 6,
        source: str = "agent",
    ) -> dict[str, Any]:
        title = str(title or "Untitled panel").strip()[:120]
        content = str(content or "").strip()[:8000]
        kind = str(kind or "text").strip().lower()
        if kind not in PANEL_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(sorted(PANEL_KINDS))}")
        with self._lock:
            if len(self._state["panels"]) >= MAX_PANELS:
                raise ValueError(f"dashboard panel limit reached ({MAX_PANELS})")
            panel = {
                "id": f"panel_{uuid.uuid4().hex[:12]}",
                "title": title,
                "content": content,
                "kind": kind,
                "width": max(3, min(int(width or 6), 12)),
                "order": int(order) if order is not None else len(self._state["panels"]),
                "visible": True,
                "source": str(source or "agent")[:80],
                "created_at": _now(),
                "updated_at": _now(),
            }
            self._state["panels"].append(panel)
            self._touch()
            return dict(panel)

    def update_panel(self, panel_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            panel = next((item for item in self._state["panels"] if item.get("id") == panel_id), None)
            if panel is None:
                raise KeyError(f"dashboard panel not found: {panel_id}")
            allowed = {"title", "content", "kind", "order", "width", "visible"}
            for key, value in changes.items():
                if key not in allowed or value is None:
                    continue
                if key == "kind":
                    value = str(value).lower()
                    if value not in PANEL_KINDS:
                        raise ValueError(f"kind must be one of: {', '.join(sorted(PANEL_KINDS))}")
                elif key in {"order", "width"}:
                    value = int(value)
                    value = max(3, min(value, 12)) if key == "width" else value
                elif key == "title":
                    value = str(value)[:120]
                elif key == "content":
                    value = str(value)[:8000]
                elif key == "visible":
                    value = bool(value)
                panel[key] = value
            panel["updated_at"] = _now()
            self._touch()
            return dict(panel)

    def move_panel(self, panel_id: str, order: int, width: int | None = None) -> dict[str, Any]:
        changes: dict[str, Any] = {"order": order}
        if width is not None:
            changes["width"] = width
        return self.update_panel(panel_id, **changes)

    def remove_panel(self, panel_id: str) -> dict[str, Any]:
        with self._lock:
            before = len(self._state["panels"])
            self._state["panels"] = [item for item in self._state["panels"] if item.get("id") != panel_id]
            if len(self._state["panels"]) == before:
                raise KeyError(f"dashboard panel not found: {panel_id}")
            self._touch()
            return {"removed": panel_id, "revision": self._state["revision"]}

    def update_tab(self, tab_id: str, label: str | None = None, visible: bool | None = None, order: int | None = None) -> dict[str, Any]:
        with self._lock:
            tab = next((item for item in self._state["tabs"] if item.get("id") == tab_id), None)
            if tab is None:
                raise KeyError(f"dashboard tab not found: {tab_id}")
            if label is not None:
                tab["label"] = str(label)[:80]
            if visible is not None:
                if tab_id in PROTECTED_TABS and not visible:
                    raise ValueError(f"tab {tab_id!r} is protected and cannot be hidden")
                tab["visible"] = bool(visible)
            if order is not None:
                tab["order"] = int(order)
            self._touch()
            return dict(tab)

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._state = self._default()
            self._touch()
            return self._copy()


dashboard_state = DashboardState()
