"""Agent personality / profile system — personas with independent memories.

Each profile is a permanent agent with its own persona (system prompt), its own
Memory 2.0 store, and optional per-profile model. Profiles live under
``~/.hermus/profiles/<name>/``.

    hermus profile create code-reviewer --persona "You are a strict code reviewer"
    hermus profile use code-reviewer
    hermus profile list
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .memory2 import Memory2
from .workspace import workspace

PRESETS: Dict[str, str] = {
    "assistant": "You are Hermus, a helpful, general-purpose AI assistant.",
    "coder": "You are a senior software engineer. Prioritize correct, tested, maintainable code.",
    "researcher": "You are a meticulous researcher. Cite sources, note uncertainty, and cross-check claims.",
    "reviewer": "You are a strict code reviewer. Find bugs, risks, and improvements; be concise and actionable.",
    "planner": "You are a strategic planner. Break goals into concrete, ordered steps with acceptance criteria.",
}


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "profile"


class ProfileManager:
    def __init__(self, profiles_dir: Optional[Path] = None):
        self.profiles_dir = Path(profiles_dir) if profiles_dir else workspace.dirs["profiles"]
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    def _profile_dir(self, name: str) -> Path:
        return self.profiles_dir / _safe_name(name)

    def _memory(self, name: str) -> Memory2:
        return Memory2(db_path=str(self._profile_dir(name) / "memory2.db"))

    def create(self, name: str, persona: Optional[str] = None,
               model: Optional[str] = None) -> Dict[str, Any]:
        pdir = self._profile_dir(name)
        if (pdir / "profile.json").exists():
            return {"success": False, "error": f"profile '{name}' already exists"}
        pdir.mkdir(parents=True, exist_ok=True)
        data = {
            "name": name,
            "persona": persona or PRESETS.get(name, PRESETS["assistant"]),
            "model": model,
            "created": datetime.now().isoformat(),
        }
        (pdir / "profile.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        # init independent memory
        self._memory(name)
        return {"success": True, "name": name, "persona": data["persona"]}

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        path = self._profile_dir(name) / "profile.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list(self) -> List[Dict[str, Any]]:
        out = []
        for p in sorted(self.profiles_dir.iterdir()):
            if p.is_dir() and (p / "profile.json").exists():
                data = self.get(p.name)
                if data:
                    out.append(data)
        return out

    def delete(self, name: str) -> Dict[str, Any]:
        pdir = self._profile_dir(name)
        if not pdir.exists():
            return {"success": False, "error": f"profile '{name}' not found"}
        import shutil

        shutil.rmtree(pdir)
        return {"success": True, "name": name}

    def system_prompt(self, name: str) -> str:
        data = self.get(name)
        if data:
            return data.get("persona") or PRESETS["assistant"]
        return PRESETS.get(name, PRESETS["assistant"])

    def remember(self, name: str, kind: str, content: str, **kwargs) -> Dict[str, Any]:
        return self._memory(name).remember(kind, content, **kwargs)

    def recall(self, name: str, query: str, **kwargs) -> List[Dict[str, Any]]:
        return self._memory(name).recall(query, **kwargs)


profile_manager = ProfileManager()
