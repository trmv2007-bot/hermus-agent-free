"""Evidence-backed, self-improving desktop skills.

Skills retain the successful procedure *and* operational knowledge accumulated
across runs: reliability, durations, visual states, known failures, and repairs
that resolved those failures.  This lets the planner prefer proven skills and
the repair system reuse evidence instead of replaying raw clicks.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _slug(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", (value or "").strip()).strip(".-")
    return safe.lower() or "skill"


def _unique_text(values: Iterable[Any], limit: int = 25) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output[-limit:]


def _aggregate_repairs(values: Iterable[Dict[str, Any]], limit: int = 50) -> List[Dict[str, Any]]:
    aggregated: Dict[str, Dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        action = value.get("action") or value.get("action_spec") or {}
        if isinstance(action, dict) and isinstance(action.get("action_spec"), dict):
            action = action["action_spec"]
        signature = json.dumps({
            "failure": value.get("failure") or value.get("diagnosis") or value.get("repair_for"),
            "action": action,
        }, sort_keys=True, default=str)
        success = bool(value.get("success", value.get("outcome") == "success"))
        runs = max(1, int(value.get("runs", 1) or 1))
        successes = int(value.get("successes", runs if success else 0) or 0)
        if signature not in aggregated:
            aggregated[signature] = {**value, "runs": runs, "successes": successes}
        else:
            current = aggregated[signature]
            current["runs"] = int(current.get("runs", 0)) + runs
            current["successes"] = int(current.get("successes", 0)) + successes
            for key in ("verification", "failure_reason", "outcome", "success"):
                if key in value:
                    current[key] = value[key]
        current = aggregated[signature]
        current["failures"] = max(0, int(current["runs"]) - int(current["successes"]))
        current["success_rate"] = round(int(current["successes"]) / int(current["runs"]), 4)
    return list(aggregated.values())[-limit:]


@dataclass
class ComputerSkill:
    name: str
    task: str
    procedure: List[Dict[str, Any]] = field(default_factory=list)
    success_rate: float = 0.0
    runs: int = 0
    successes: int = 0
    failures: int = 0
    repairs: List[Dict[str, Any]] = field(default_factory=list)
    average_duration: float = 0.0
    total_duration: float = 0.0
    visual_states: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    typical_failures: List[str] = field(default_factory=list)
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    uses: int = 0

    def normalize(self) -> "ComputerSkill":
        self.runs = max(0, int(self.runs))
        self.successes = max(0, int(self.successes))
        self.failures = max(0, int(self.failures))
        if self.successes + self.failures < self.runs:
            # Migrate the original schema, which persisted only success_rate.
            inferred_successes = round(float(self.success_rate) * self.runs)
            self.successes = max(self.successes, inferred_successes)
            self.failures = max(self.failures, self.runs - self.successes)
        self.runs = max(self.runs, self.successes + self.failures)
        self.success_rate = round(self.successes / self.runs, 4) if self.runs else float(self.success_rate or 0.0)
        self.uses = max(int(self.uses), self.runs)
        self.total_duration = max(0.0, float(self.total_duration or 0.0))
        if self.runs and not self.total_duration and self.average_duration:
            self.total_duration = float(self.average_duration) * self.runs
        self.average_duration = round(self.total_duration / self.runs, 3) if self.runs else 0.0
        self.typical_failures = _unique_text(self.typical_failures)
        self.visual_states = _unique_text(self.visual_states, limit=100)
        self.repairs = _aggregate_repairs(self.repairs, limit=50)
        return self

    def to_dict(self) -> Dict[str, Any]:
        self.normalize()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ComputerSkill":
        runs = int(data.get("runs", data.get("uses", 0)) or 0)
        rate = float(data.get("success_rate", 0.0 if runs else 1.0))
        successes = int(data.get("successes", round(rate * runs)) or 0)
        failures = int(data.get("failures", max(0, runs - successes)) or 0)
        skill = cls(
            name=str(data.get("name", "")),
            task=str(data.get("task", "")),
            procedure=list(data.get("procedure") or []),
            success_rate=rate,
            runs=runs,
            successes=successes,
            failures=failures,
            repairs=list(data.get("repairs") or []),
            average_duration=float(data.get("average_duration", 0.0) or 0.0),
            total_duration=float(data.get("total_duration", 0.0) or 0.0),
            visual_states=list(data.get("visual_states") or []),
            evidence=dict(data.get("evidence") or {}),
            typical_failures=list(data.get("typical_failures") or []),
            created=str(data.get("created") or _now()),
            updated=str(data.get("updated") or data.get("created") or _now()),
            uses=int(data.get("uses", runs) or 0),
        )
        return skill.normalize()

    def known_repair(self, failure: str) -> Optional[Dict[str, Any]]:
        words = set(re.findall(r"[a-z0-9]+", str(failure).lower()))
        ranked: List[Tuple[int, float, Dict[str, Any]]] = []
        for repair in self.repairs:
            failure_text = str(repair.get("failure") or repair.get("diagnosis") or "")
            score = len(words & set(re.findall(r"[a-z0-9]+", failure_text.lower())))
            success_rate = float(repair.get("success_rate", 1.0 if repair.get("success", True) else 0.0))
            ranked.append((score, success_rate, repair))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return dict(ranked[0][2]) if ranked and ranked[0][0] > 0 else None


class ComputerSkillStore:
    """Persist, rank, update, and query evidence-backed computer skills."""

    STOPWORDS = {"a", "an", "and", "the", "to", "of", "in", "on", "this", "that", "it", "please"}

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

    @staticmethod
    def _merge_evidence(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(old or {})
        for key, value in (new or {}).items():
            if key == "runs":
                prior = merged.get("runs") if isinstance(merged.get("runs"), list) else []
                incoming = value if isinstance(value, list) else [value]
                merged["runs"] = [*prior, *incoming][-25:]
            else:
                merged[key] = value
        return merged

    def _write(self, skill: ComputerSkill) -> str:
        path = self._path(skill.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(skill.to_dict(), indent=2, default=str), encoding="utf-8")
        temporary.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return str(path)

    def save_skill(
        self,
        task: str,
        procedure: List[Dict[str, Any]],
        evidence: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        duration: Optional[float] = None,
        repairs: Optional[List[Dict[str, Any]]] = None,
        visual_states: Optional[List[str]] = None,
        record_success: bool = True,
    ) -> Dict[str, Any]:
        """Create or refresh a skill and record the successful source run."""
        skill_name = name or _slug(task)
        skill = self.get_skill(skill_name) or ComputerSkill(name=skill_name, task=task)
        skill.task = task
        if procedure:
            skill.procedure = list(procedure)
        skill.evidence = self._merge_evidence(skill.evidence, evidence or {})
        skill.visual_states = _unique_text([*skill.visual_states, *(visual_states or [])], limit=100)
        if repairs:
            skill.repairs.extend(item for item in repairs if isinstance(item, dict))
            skill.repairs = skill.repairs[-50:]
        if record_success:
            skill.runs += 1
            skill.uses += 1
            skill.successes += 1
            if duration is not None:
                skill.total_duration += max(0.0, float(duration))
        skill.updated = _now()
        skill.normalize()
        path = self._write(skill)
        return {"success": True, "name": skill.name, "path": path, "skill": skill.to_dict()}

    def list_skills(self) -> List[Dict[str, Any]]:
        skills: List[Dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                skill = ComputerSkill.from_dict(json.loads(path.read_text(encoding="utf-8")))
                skills.append({
                    "name": skill.name,
                    "task": skill.task,
                    "steps": len(skill.procedure),
                    "created": skill.created,
                    "updated": skill.updated,
                    "uses": skill.uses,
                    "runs": skill.runs,
                    "successes": skill.successes,
                    "failures": skill.failures,
                    "success_rate": skill.success_rate,
                    "average_duration": skill.average_duration,
                    "known_failures": len(skill.typical_failures),
                    "known_repairs": len(skill.repairs),
                })
            except Exception:
                continue
        return sorted(skills, key=lambda item: (item["success_rate"], item["runs"]), reverse=True)

    def get_skill(self, name: str) -> Optional[ComputerSkill]:
        path = self._path(name)
        if not path.exists():
            return None
        try:
            return ComputerSkill.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    @classmethod
    def _words(cls, text: str) -> set:
        return {
            word for word in re.findall(r"[a-z0-9]+", str(text).lower())
            if word not in cls.STOPWORDS and len(word) > 1
        }

    def rank(self, task: str, limit: int = 5) -> List[Dict[str, Any]]:
        words = self._words(task)
        ranked: List[Dict[str, Any]] = []
        for path in self.root.glob("*.json"):
            try:
                skill = ComputerSkill.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            candidate = self._words(f"{skill.task} {skill.name}")
            overlap = len(words & candidate)
            if overlap <= 0:
                continue
            lexical = overlap / max(1, len(words | candidate))
            reliability = skill.success_rate if skill.runs else 0.6
            experience = min(skill.runs, 20) / 20.0
            score = 0.65 * lexical + 0.25 * reliability + 0.10 * experience
            ranked.append({"skill": skill, "score": round(score, 4), "overlap": overlap})
        return sorted(ranked, key=lambda item: item["score"], reverse=True)[: max(1, int(limit))]

    def recall(self, task: str, minimum_score: float = 0.12) -> Optional[ComputerSkill]:
        ranked = self.rank(task, limit=1)
        return ranked[0]["skill"] if ranked and ranked[0]["score"] >= minimum_score else None

    def record_run(
        self,
        name: str,
        success: bool,
        error: Optional[str] = None,
        duration: Optional[float] = None,
        repairs: Optional[List[Dict[str, Any]]] = None,
        visual_states: Optional[List[str]] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        skill = self.get_skill(name)
        if skill is None:
            return
        skill.runs += 1
        skill.uses += 1
        if success:
            skill.successes += 1
        else:
            skill.failures += 1
        if duration is not None:
            skill.total_duration += max(0.0, float(duration))
        if error:
            skill.typical_failures = _unique_text([*skill.typical_failures, str(error)])
        if repairs:
            skill.repairs.extend(item for item in repairs if isinstance(item, dict))
            skill.repairs = skill.repairs[-50:]
        if visual_states:
            skill.visual_states = _unique_text([*skill.visual_states, *visual_states], limit=100)
        if evidence:
            skill.evidence = self._merge_evidence(skill.evidence, evidence)
        skill.updated = _now()
        skill.normalize()
        self._write(skill)

    def record_use(self, name: str) -> None:
        self.record_run(name, success=True)

    def known_repair(self, skill_name: str, failure: str) -> Optional[Dict[str, Any]]:
        skill = self.get_skill(skill_name)
        return skill.known_repair(failure) if skill else None

    def profile(self, name: str) -> Optional[Dict[str, Any]]:
        """Human-readable reliability profile for one skill.

        Example::

            Install Firefox — 28 runs / 26 success / 92.9% / avg 39.8s
        """
        skill = self.get_skill(name)
        if skill is None:
            return None
        skill.normalize()
        rate = (skill.success_rate * 100.0) if skill.runs else None
        return {
            "name": skill.name,
            "task": skill.task,
            "runs": skill.runs,
            "successes": skill.successes,
            "failures": skill.failures,
            "success_rate": skill.success_rate,
            "success_percent": round(rate, 1) if rate is not None else None,
            "average_duration": skill.average_duration,
            "total_duration": round(skill.total_duration, 1),
            "steps": len(skill.procedure),
            "known_failures": list(skill.typical_failures),
            "known_repairs": len(skill.repairs),
            "visual_states": list(skill.visual_states),
            "updated": skill.updated,
            "summary": (
                f"{skill.task} — {skill.runs} runs / {skill.successes} success / "
                f"{round(rate, 1) if rate is not None else 'n/a'}% / "
                f"avg {round(skill.average_duration, 1)}s"
            ),
        }
