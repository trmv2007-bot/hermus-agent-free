"""Workspace — per-project isolation for Hermus (the "agent operating system").

Layout (default ``~/.hermus``, overridable via ``HERMUS_HOME`` or ``workspace_dir``):

    ~/.hermus/
        agents/        # persistent background agent state
        projects/      # one directory per project
        memory/        # global memory artifacts
        skills/        # global skills
        sessions/      # session artifacts
        credentials/   # secret/credential storage (git-ignored, mode 0700)
        logs/          # audit + run logs

Each project:

    project.yaml      # name/description/config
    memory.db         # project-scoped memory (SQLite)
    skills/           # project skills
    tasks/            # queued/running/finished tasks
    context/          # project context files
    artifacts/        # produced files

Everything is dependency-free: project.yaml uses a minimal flat YAML subset
(no PyYAML required).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import config


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        # Quote strings that could be ambiguous
        if v == "" or re.search(r"[:#\-\s]", v):
            return json.dumps(v)
        return v
    return json.dumps(v)


def dump_yaml(d: Dict[str, Any]) -> str:
    """Minimal flat-YAML writer (scalars + lists only)."""
    lines: List[str] = []
    for k, v in d.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {_scalar(item)}")
        elif isinstance(v, dict):
            lines.append(f"{k}:")
            for kk, vv in v.items():
                lines.append(f"  {kk}: {_scalar(vv)}")
        else:
            lines.append(f"{k}: {_scalar(v)}")
    return "\n".join(lines) + "\n"


def load_yaml(text: str) -> Dict[str, Any]:
    """Parse the flat-YAML subset emitted by :func:`dump_yaml`."""
    out: Dict[str, Any] = {}
    current_list: Optional[str] = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith(" ") or line.startswith("\t"):
            # list item under current key
            if current_list and line.lstrip().startswith("- "):
                item = line.strip()[2:].strip()
                out.setdefault(current_list, []).append(_parse_scalar(item))
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "":
            current_list = key
            out.setdefault(key, [])
        else:
            current_list = None
            out[key] = _parse_scalar(val)
    return out


def _parse_scalar(v: str) -> Any:
    if v in ("true", "false"):
        return v == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return v


class Project:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.name = self.path.name

    @property
    def config_path(self) -> Path:
        return self.path / "project.yaml"

    @property
    def memory_db(self) -> Path:
        return self.path / "memory.db"

    def load(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {"name": self.name, "description": "", "created": ""}
        try:
            return load_yaml(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return {"name": self.name, "description": "", "created": ""}

    def save(self, data: Dict[str, Any]) -> None:
        self.config_path.write_text(dump_yaml(data), encoding="utf-8")

    def to_dict(self) -> Dict[str, Any]:
        data = self.load()
        data.setdefault("name", self.name)
        data.setdefault("path", str(self.path))
        return data


class Workspace:
    def __init__(self, base_dir: Optional[str] = None):
        raw = base_dir or os.environ.get("HERMUS_HOME") or getattr(config, "workspace_dir", "~/.hermus")
        self.base_dir = Path(raw).expanduser()
        self.ensure_layout()

    # -- layout ---------------------------------------------------------
    @property
    def dirs(self) -> Dict[str, Path]:
        return {
            "agents": self.base_dir / "agents",
            "projects": self.base_dir / "projects",
            "memory": self.base_dir / "memory",
            "skills": self.base_dir / "skills",
            "sessions": self.base_dir / "sessions",
            "credentials": self.base_dir / "credentials",
            "logs": self.base_dir / "logs",
            "profiles": self.base_dir / "profiles",
        }

    def ensure_layout(self) -> None:
        for d in self.dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        try:
            self.dirs["credentials"].chmod(0o700)
        except Exception:
            pass

    def log(self, name: str, line: str) -> Path:
        path = self.dirs["logs"] / f"{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(), "line": line}) + "\n")
        return path

    # -- projects -------------------------------------------------------
    def project_dir(self, name: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "default"
        return self.dirs["projects"] / safe

    def create_project(self, name: str, description: str = "") -> Dict[str, Any]:
        path = self.project_dir(name)
        if path.exists():
            return {"success": False, "error": f"project '{name}' already exists", "path": str(path)}
        for sub in ("skills", "tasks", "context", "artifacts"):
            (path / sub).mkdir(parents=True, exist_ok=True)
        Project(path).save(
            {
                "name": name,
                "description": description,
                "created": datetime.now().isoformat(),
                "version": 1,
            }
        )
        # bare SQLite db
        conn = sqlite3.connect(str(path / "memory.db"))
        conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts TEXT)")
        conn.commit()
        conn.close()
        return {"success": True, "name": name, "path": str(path)}

    def list_projects(self) -> List[Dict[str, Any]]:
        out = []
        if not self.dirs["projects"].exists():
            return out
        for p in sorted(self.dirs["projects"].iterdir()):
            if p.is_dir() and (p / "project.yaml").exists():
                out.append(Project(p).to_dict())
        return out

    def get_project(self, name: str) -> Optional[Project]:
        path = self.project_dir(name)
        if not (path / "project.yaml").exists():
            return None
        return Project(path)

    def delete_project(self, name: str) -> Dict[str, Any]:
        path = self.project_dir(name)
        if not path.exists():
            return {"success": False, "error": f"project '{name}' not found"}
        import shutil

        shutil.rmtree(path)
        return {"success": True, "name": name}

    # -- current project ------------------------------------------------
    def set_current_project(self, name: str) -> Dict[str, Any]:
        p = self.get_project(name)
        if not p:
            return {"success": False, "error": f"project '{name}' not found"}
        (self.base_dir / "current_project").write_text(name, encoding="utf-8")
        return {"success": True, "name": name}

    def current_project(self) -> Optional[str]:
        p = self.base_dir / "current_project"
        if p.exists():
            name = p.read_text(encoding="utf-8").strip()
            if self.get_project(name):
                return name
        return None

    def active_project(self) -> str:
        """Resolve the effective project: explicit config.project, else the
        workspace's current project, else 'default'."""
        cfg_project = getattr(config, "project", "default") or "default"
        if cfg_project and cfg_project != "default":
            return cfg_project
        return self.current_project() or "default"


workspace = Workspace()
