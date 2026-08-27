"""DeepThink Scaffold — explicit, visible, resumable plans (Phase 0).

- `PlanStep` / `Plan`: structured plan the agent writes BEFORE acting.
- `PlanBuilder`: one free LLM call (or deterministic fallback) that turns a goal
  into numbered steps with goals + actions + verification hints.
- Plans are saved to data/plans/<session>.json so long tasks survive restarts
  (`hermus plan resume` comes in a later phase; the file is the contract).

Design note: small free models (llama3.1:8b) reason better against an explicit
written plan than with an invisible chain-of-thought — the plan is scaffolded
OUTSIDE the model, not trusted to it.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import config


@dataclass
class PlanStep:
    goal: str
    action: str = "investigate"
    verify: str = ""
    status: str = "pending"  # pending | active | done | failed | skipped
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PlanStep":
        return cls(
            goal=str(d.get("goal", "")),
            action=str(d.get("action", "investigate")),
            verify=str(d.get("verify", "")),
            status=str(d.get("status", "pending")),
            evidence=list(d.get("evidence") or []),
        )


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    strategy: str = "react"
    difficulty: int = 3
    status: str = "drafted"  # drafted | active | done | aborted
    session_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "strategy": self.strategy,
            "difficulty": self.difficulty,
            "status": self.status,
            "session_id": self.session_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        return cls(
            goal=str(d.get("goal", "")),
            steps=[PlanStep.from_dict(s) for s in d.get("steps") or []],
            strategy=str(d.get("strategy", "react")),
            difficulty=int(d.get("difficulty", 3)),
            status=str(d.get("status", "drafted")),
            session_id=str(d.get("session_id", "")),
            created_at=str(d.get("created_at", "")),
        )

    def to_prompt(self) -> str:
        """Render the plan for injection into an LLM system prompt."""
        lines = [f"Goal: {self.goal}", f"Strategy: {self.strategy}", "Steps:"]
        for i, s in enumerate(self.steps, 1):
            lines.append(f"  {i}. [{s.status}] {s.goal} (action: {s.action})")
            if s.verify:
                lines.append(f"     verify: {s.verify}")
        return "\n".join(lines)

    def save(self, path: Optional[str] = None) -> Path:
        p = Path(path) if path else config.resolve_path(f"data/plans/plan_{self.session_id or uuid.uuid4().hex[:8]}.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))
        return p

    @classmethod
    def load(cls, path: str) -> Optional["Plan"]:
        p = Path(path)
        if not p.exists():
            return None
        try:
            return cls.from_dict(json.loads(p.read_text()))
        except Exception:
            return None


_PLAN_PROMPT = """You are Hermus, a careful planner. Given a goal, produce a numbered plan.

Return ONLY JSON, no markdown, no commentary:
{"steps": [{"goal": "what this step achieves", "action": "concrete action e.g. web_search X / file_read Y / write code / ask user", "verify": "how to check this step succeeded"}]}

Rules:
- 3 to 6 steps, ordered, each step one clear deliverable
- Actions must be concrete and tool-friendly (web_search, browser, file ops, shell, skill_use, ask user)
- Include a final verification step when the goal implies checking quality (security, correctness, tests)
"""


class PlanBuilder:
    """Build a structured Plan from a goal — one free LLM call, safe fallback."""

    def __init__(self, model: Optional[str] = None):
        self.model = model or config.model

    def build_plan(self, goal: str, session_id: str = "", difficulty: int = 3) -> Plan:
        plan = Plan(goal=goal, session_id=session_id, difficulty=difficulty)
        try:
            from ..llm import FreeLLM

            llm = FreeLLM(self.model)
            resp = llm.chat(
                [
                    {"role": "system", "content": _PLAN_PROMPT},
                    {"role": "user", "content": f"Goal: {goal}"},
                ]
            )
            parsed = self._parse_steps(resp.content or "")
            if parsed:
                plan.steps = parsed
                plan.status = "drafted"
                return plan
        except Exception as e:
            print(f"[PlanBuilder] LLM plan failed ({e}) - heuristic fallback")

        # Deterministic fallback: bullets / sentences -> steps
        plan.steps = self._heuristic_steps(goal)
        plan.strategy = "react"
        return plan

    @staticmethod
    def _parse_steps(content: str) -> Optional[list[PlanStep]]:
        text = content.strip()
        # Strip code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except Exception:
            # Try to find the JSON object inside the text
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                return None
            try:
                data = json.loads(m.group(0))
            except Exception:
                return None
        steps_raw = data.get("steps") if isinstance(data, dict) else None
        if not isinstance(steps_raw, list) or not steps_raw:
            return None
        steps = []
        for s in steps_raw:
            if not isinstance(s, dict) or not s.get("goal"):
                continue
            steps.append(
                PlanStep(
                    goal=str(s.get("goal", ""))[:300],
                    action=str(s.get("action", "investigate"))[:200],
                    verify=str(s.get("verify", ""))[:200],
                )
            )
        return steps[:8] if steps else None

    @staticmethod
    def _heuristic_steps(goal: str) -> list[PlanStep]:
        lines = [ln.strip(" -*\t") for ln in goal.splitlines() if ln.strip()]
        bullets = [
            re.sub(r"^(\d+[\.\):])\s+", "", ln).strip()
            for ln in lines
            if re.match(r"^\s*(\d+[\.\):]|[-*])\s+", ln)
        ]
        if len(bullets) >= 2:
            return [
                PlanStep(goal=b[:200], action="investigate", verify="")
                for b in bullets[:6]
            ]
        parts = re.split(r"(?<=[.!?])\s+|\band then\b|\bthen\b|;", goal)
        parts = [p.strip() for p in parts if p and len(p.strip()) > 15]
        if len(parts) >= 2:
            return [PlanStep(goal=p[:200], action="investigate") for p in parts[:6]]
        return [
            PlanStep(goal=f"Research and gather facts about: {goal[:150]}", action="web_search"),
            PlanStep(goal="Analyze findings and outline the answer", action="analyze"),
            PlanStep(goal="Deliver the final answer with verification", action="synthesize", verify="check claims against gathered evidence"),
        ]


plan_builder = PlanBuilder()


# ---------------------------------------------------------------- Phase 4: plan persistence & resume (P1)


def list_plans(limit: int = 10) -> list[dict]:
    """List saved plans from data/plans/."""
    d = config.resolve_path("data/plans")
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]:
        try:
            plan = Plan.load(str(p))
            if plan:
                done = sum(1 for s in plan.steps if s.status == "done")
                out.append(
                    {
                        "session_id": plan.session_id,
                        "path": str(p),
                        "goal": plan.goal[:120],
                        "steps": len(plan.steps),
                        "done": done,
                        "status": plan.status,
                        "created_at": plan.created_at[:19],
                    }
                )
        except Exception:
            continue
    return out


def show_plan(session_id: str) -> Optional[Plan]:
    p = config.resolve_path(f"data/plans/plan_{session_id}.json")
    if not p.exists():
        return None
    return Plan.load(str(p))


def resume_plan(session_id: str, model: Optional[str] = None) -> dict:
    """Resume a saved plan: mark failed steps pending, run remaining steps with the agent."""
    plan = show_plan(session_id)
    if not plan:
        return {"success": False, "error": f"no plan found for {session_id}"}
    remaining = [s for s in plan.steps if s.status != "done"]
    if not remaining:
        return {"success": True, "message": "plan already complete", "plan": plan.to_dict()}
    for s in plan.steps:
        if s.status == "failed":
            s.status = "pending"
    plan.status = "active"
    plan.save()

    from ..agent import HermusAgent

    agent = HermusAgent(model=model or config.model, mode="agent")
    agent.plan_override = plan
    instruction = (
        "Resume this plan and complete the remaining steps:\n"
        + "\n".join(f"{i+1}. {s.goal} (action: {s.action})" for i, s in enumerate(plan.steps) if s.status != "done")
        + "\nExecute the remaining steps and give the final result."
    )
    result = agent.chat(instruction)
    return {
        "success": True,
        "plan": plan.to_dict(),
        "response": result.get("response"),
        "session_id": result.get("session_id"),
        "remaining_before": len(remaining),
    }
