"""Durable model-selection preferences.

The preferences layer stores what the user selected; it does not own model
discovery or provider execution. Validation is performed against the canonical
ModelGateway catalog before a preference is accepted.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .models.model_catalog import model_catalog


class ModelPreferences:
    """Persistent role-aware model selections with an explicit Auto mode."""

    ROLES = ("default", "reasoning", "vision", "coding", "background", "doctor", "voice")

    def __init__(self, path: str = "data/model_preferences.json") -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {"version": 1, "selections": {}}
        self._load()

    def _load(self) -> None:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    self._data = payload
            except Exception:
                self._data = {"version": 1, "selections": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    @classmethod
    def _role(cls, role: str | None) -> str:
        value = str(role or "default").strip().lower()
        return value if value in cls.ROLES else "default"

    def get(self, role: str | None = "default") -> str | None:
        with self._lock:
            value = self._data.get("selections", {}).get(self._role(role))
            if value in (None, "", "auto"):
                return None
            return str(value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            selections = dict(self._data.get("selections") or {})
            return {
                "version": int(self._data.get("version", 1)),
                "selections": selections,
                "roles": list(self.ROLES),
            }

    def set(self, role: str, model_ref: str | None, *, validate: bool = True) -> dict[str, Any]:
        role_name = self._role(role)
        value = str(model_ref or "").strip()
        with self._lock:
            if value.lower() in ("", "auto"):
                self._data.setdefault("selections", {}).pop(role_name, None)
                self._save()
                return {"role": role_name, "model": "auto", "validated": True}

            if "/" not in value:
                raise ValueError("model must use provider/model format")

            if validate and model_catalog.selectable(value, probe=True) is None:
                raise ValueError(f"model '{value}' is not currently discoverable")
            self._data.setdefault("selections", {})[role_name] = value
            self._save()
            return {"role": role_name, "model": value, "validated": bool(validate)}

    def clear(self, role: str = "default") -> dict[str, Any]:
        return self.set(role, "auto", validate=False)


model_preferences = ModelPreferences()
