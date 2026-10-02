"""Explicit, durable HERMUS personalization profile."""
from __future__ import annotations
import json, threading
from pathlib import Path
from typing import Any
from .config import config

class PersonalProfile:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or config.resolve_path("data/personal_profile.json")); self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = {"name":"", "assistant_name":"HERMUS", "style":"concise", "language":"en", "voice":{}, "routines":[], "trusted_devices":[], "allowed_capabilities":[]}
        self._load()
    def _load(self):
        try:
            data=json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data,dict): self._data.update(data)
        except (OSError, ValueError): pass
    def _save(self):
        tmp=self.path.with_suffix(".tmp"); tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"); tmp.replace(self.path)
    def update(self, **values: Any) -> dict[str, Any]:
        with self._lock:
            for key,value in values.items():
                if key in self._data and value is not None: self._data[key]=value
            self._save(); return self.snapshot()
    def add_routine(self, routine: dict[str, Any]) -> dict[str, Any]:
        with self._lock: self._data["routines"].append(dict(routine)); self._save(); return self.snapshot()
    def snapshot(self) -> dict[str, Any]:
        with self._lock: return json.loads(json.dumps(self._data))

personal_profile=PersonalProfile()
