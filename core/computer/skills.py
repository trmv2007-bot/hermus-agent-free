"""Turn successful recordings into reusable computer skills.

A successful autonomous task yields a procedure (ordered actions with their
verification results) plus evidence that points back into the recording.  This
module persists that procedure as a *skill* so the next run of a similar task
does not start from zero: it recalls the successful steps and adapts them to
the current screen through the same vision-driven action engine.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _slug(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", (value or "").strip()).strip(".-")
    return safe.lower() or "skill"


@dataclass
class ComputerSkill:
    name: str
    task: str
    procedure: List[Dict[str, Any]] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    created: str = field(default_factory=_now)
    uses: int = 0
    success_rate: float = 1.0
    runs: int = 0
    typical_failures: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ComputerSkill":
        return cls(
            name=data.get("name", ""),
            task=data.get("task", ""),
            procedure=data.get("procedure", []),
            evidence=data.get("evidence", {}),
            created=data.get("created", _now()),
            uses=int(data.get("uses", 0)),
            success_rate=float(data.get("success_rate", 1.0)),
            runs=int(data.get("runs", 0)),
            typical_failures=data.get("typical_failures", []),
        )


class ComputerSkillStore:
    """Persist and recall computer skills from a JSON directory."""

    def __init__(self, root: str = "data/recordings/skills"):
        root_path = Path(root).expanduser()
        if not root_path.is_absolute() and root == "data/recordings/skills":
            root_path = Path(__file__).resolve().parents[2] / root_path
        self.root = root_path.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def _path(self, name: str) -> Path:
        return self.root / f"{_slug(name)}.json"

    def save_skill(
        self,
        task: str,
        procedure: List[Dict[str, Any]],
        evidence: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist a successful procedure as a reusable skill."""
        skill = ComputerSkill(
            name=name or _slug(task),
            task=task,
            procedure=procedure,
            evidence=evidence or {},
        )
        path = self._path(skill.name)
        path.write_text(json.dumps(skill.to_dict(), indent=2, default=str), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return {"success": True, "name": skill.name, "path": str(path), "skill": skill.to_dict()}

    def list_skills(self) -> List[Dict[str, Any]]:
        skills: List[Dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                skills.append({"name": data.get("name"), "task": data.get("task"),
                               "steps": len(data.get("procedure", [])),
                               "created": data.get("created"), "uses": data.get("uses", 0)})
            except Exception:
                continue
        return skills

    def get_skill(self, name: str) -> Optional[ComputerSkill]:
        path = self._path(name)
        if not path.exists():
            return None
        try:
            return ComputerSkill.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def recall(self, task: str) -> Optional[ComputerSkill]:
        """Find the best-matching skill for a task (keyword overlap)."""
        words = set(re.findall(r"[a-z0-9]+", task.lower()))
        best: Optional[ComputerSkill] = None
        best_score = 0
        for path in self.root.glob("*.json"):
            try:
                skill = ComputerSkill.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            candidate = set(re.findall(r"[a-z0-9]+", f"{skill.task} {skill.name}".lower()))
            score = len(words & candidate)
            if score > best_score:
                best, best_score = skill, score
        return best if best_score > 0 else None

    def record_run(self, name: str, success: bool, error: Optional[str] = None) -> None:
        """Update skill stats after an execution attempt."""
        skill = self.get_skill(name)
        if skill is None:
            return
        
        skill.runs += 1
        skill.uses += 1
        
        # Incremental success rate calculation
        old_successes = skill.success_rate * (skill.runs - 1)
        new_successes = old_successes + (1 if success else 0)
        skill.success_rate = round(new_successes / skill.runs, 3)
        
        if error:
            skill.typical_failures.append(str(error))
            skill.typical_failures = skill.typical_failures[-5:] # Keep last 5
            
        path = self._path(name)
        path.write_text(json.dumps(skill.to_dict(), indent=2, default=str), encoding="utf-8")

    def record_use(self, name: str) -> None:
        self.record_run(name, success=True)
