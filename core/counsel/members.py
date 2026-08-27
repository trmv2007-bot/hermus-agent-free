"""Council Members — the roundtable roster.

Each member is an AgentPersona-style role with its own model/key/provider when
available (reusing multi_key + model_fleet discovery), so "all AIs talk to each
other" is real: Groq free key for the Critic, Ollama local for the Chair, etc.
If only one model exists, members still differ by persona.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..config import config
from ..llm import FreeLLM

# Priority order used to pick members when the budget allows fewer than the roster
_ROLE_PRIORITY = ["chair", "researcher", "critic", "synthesizer", "judge"]

ROLE_TEMPERATURES = {
    "critic": 0.1,
    "judge": 0.0,
    "chair": 0.3,
    "researcher": 0.5,
    "researcher2": 0.5,
    "synthesizer": 0.2,
}


@dataclass
class CounselMember:
    role: str
    name: str
    persona: str
    model: str
    api_key: str = ""
    base_url: str = ""
    weight: int = 1
    provider: str = ""
    temperature: Optional[float] = None

    def llm(self) -> FreeLLM:
        temp = self.temperature if self.temperature is not None else ROLE_TEMPERATURES.get(self.role, 0.3)
        return FreeLLM(self.model, api_key=self.api_key or None, base_url=self.base_url or None, temperature=temp)


def _discover_workers() -> List[Dict]:
    """Best-effort discovery of available free model/key workers."""
    try:
        from ..model_fleet import _available_workers

        return _available_workers(limit=12)
    except Exception:
        return []


def _assign_models(specs: List[Dict], model: Optional[str] = None) -> List[Dict]:
    """Assign diverse model refs to member specs; fall back to `model` or config.model."""
    workers = _discover_workers()
    used_providers = set()
    assigned = []
    for spec in specs:
        s = dict(spec)
        pinned = s.get("model")
        if pinned:
            s["model"] = pinned
            assigned.append(s)
            used_providers.add(pinned.split("/", 1)[0])
            continue
        pick = None
        for w in workers:
            if w.get("provider") not in used_providers:
                pick = w
                break
        if not pick and workers:
            pick = workers[len(assigned) % len(workers)]
        if pick:
            s["model"] = f"{pick['provider']}/{pick['model']}"
            s["api_key"] = pick.get("key") or ""
            s["base_url"] = pick.get("base_url") or ""
            used_providers.add(pick.get("provider"))
        else:
            s["model"] = model or config.model
        assigned.append(s)
    return assigned


def build_roster(
    constitution_doc: Dict,
    max_members: int = 5,
    model: Optional[str] = None,
) -> List[CounselMember]:
    """Build the debate roster (members only; judge is used later in voting)."""
    specs = [m for m in constitution_doc.get("members", []) if m.get("enabled")]
    specs = sorted(specs, key=lambda m: _ROLE_PRIORITY.index(m["role"]) if m["role"] in _ROLE_PRIORITY else 99)
    # Grow roster: chair + researcher x2 when room, then critic, synthesizer, judge
    ordered = []
    for role in _ROLE_PRIORITY:
        for m in specs:
            if m["role"] == role:
                ordered.append(m)
                if role == "researcher" and max_members >= 6:
                    ordered.append(dict(m, role="researcher2", name="researcher2"))
                break
    ordered = ordered[:max_members]
    if not ordered and specs:
        ordered = specs[:max_members]

    assigned = _assign_models(ordered, model=model)
    members = []
    for i, s in enumerate(assigned):
        role = s["role"]
        name = s.get("name") or role
        members.append(
            CounselMember(
                role=role,
                name=f"{name}",
                persona=s.get("persona", f"You are the {role} on the Hermus Council."),
                model=s["model"],
                api_key=s.get("api_key") or "",
                base_url=s.get("base_url") or "",
                weight=int(s.get("weight", 1)),
                provider=s["model"].split("/", 1)[0],
            )
        )
    return members


def describe_roster(members: List[CounselMember]) -> str:
    return ", ".join(f"{m.name} ({m.model})" for m in members)
