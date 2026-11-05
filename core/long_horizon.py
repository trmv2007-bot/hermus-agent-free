"""Long-horizon mission planning for HERMUS.

Builds a resumable dependency graph and checkpoint contract above MissionEngine.
It plans; MissionEngine remains the only execution lifecycle.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .agent_dag import AgentDAG


@dataclass
class PlanStep:
    id: str
    objective: str
    dependencies: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    checkpoint: bool = True
    status: str = "pending"


@dataclass
class LongHorizonPlan:
    goal: str
    steps: list[PlanStep]
    replanning_triggers: list[str] = field(
        default_factory=lambda: ["dependency_failed", "verification_failed", "world_changed", "new_constraint", "budget_pressure"]
    )
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": [asdict(step) for step in self.steps],
            "replanning_triggers": list(self.replanning_triggers),
            "created_at": self.created_at,
        }


class LongHorizonPlanner:
    """Create bounded plans with explicit dependencies and completion criteria."""

    def build(
        self, goal: str, *, success_criteria: list[str] | None = None, subgoals: list[str] | None = None
    ) -> LongHorizonPlan:
        goal = str(goal or "").strip()
        if not goal:
            raise ValueError("goal must not be empty")
        criteria = [str(x) for x in (success_criteria or []) if str(x).strip()]
        objectives = [str(x) for x in (subgoals or []) if str(x).strip()]
        if not objectives:
            objectives = [
                f"Define an actionable execution plan for: {goal}",
                f"Execute the required work for: {goal}",
                f"Verify the outcome against: {goal}",
            ]
        steps: list[PlanStep] = []
        previous: str | None = None
        for index, objective in enumerate(objectives, 1):
            step_id = f"plan_step_{index}"
            step_criteria = criteria if index == len(objectives) else [f"Complete step {index}: {objective}"]
            steps.append(
                PlanStep(
                    id=step_id,
                    objective=objective,
                    dependencies=[previous] if previous else [],
                    success_criteria=step_criteria,
                )
            )
            previous = step_id
        return LongHorizonPlan(goal=goal, steps=steps)

    def to_dag(self, plan: LongHorizonPlan) -> AgentDAG:
        dag = AgentDAG(name=f"Long Horizon: {plan.goal[:60]}")
        for step in plan.steps:
            dag.add_node(
                step.id,
                "planner",
                step.objective,
                dependencies=step.dependencies,
                inputs={"success_criteria": step.success_criteria, "checkpoint": step.checkpoint},
            )
        if not dag.validate():
            raise ValueError("long-horizon plan contains a dependency cycle")
        return dag

    @staticmethod
    def replan(
        plan: LongHorizonPlan, *, failed_step: str, reason: str, replacement_objective: str | None = None
    ) -> LongHorizonPlan:
        replacement = replacement_objective or f"Recover from {failed_step}: {reason}"
        steps = list(plan.steps)
        for step in steps:
            if step.id == failed_step:
                step.status = "needs_replan"
                index = steps.index(step)
                new_step = PlanStep(
                    id=f"{failed_step}_recovery",
                    objective=replacement,
                    dependencies=list(step.dependencies),
                    success_criteria=[f"Resolve failure in {failed_step}", replacement],
                )
                steps.insert(index, new_step)
                break
        return LongHorizonPlan(plan.goal, steps, list(plan.replanning_triggers), plan.created_at)


long_horizon_planner = LongHorizonPlanner()

__all__ = ["LongHorizonPlan", "LongHorizonPlanner", "PlanStep", "long_horizon_planner"]
