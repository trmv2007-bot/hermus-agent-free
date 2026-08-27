"""Autonomous task loop — plan → execute → observe → verify → diagnose → repair → finish.

A real state machine on top of the existing ReAct loop. Instead of "call
tools until the model stops", a task is driven through explicit phases and is
only marked *finished* once a verifier confirms the goal is met. Failed
verifications trigger a diagnose→repair→re-execute cycle (bounded).

The runner is decoupled from any specific LLM: you inject an ``executor``
(a callable that turns a step/plan into an observation) and optionally a
``verifier`` / ``diagnoser``. Defaults are deterministic so the loop is
usable and testable offline; plug the real agent in via ``executor=``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from collections.abc import Callable


class Phase(str, Enum):
    UNDERSTAND = "understand"
    PLAN = "plan"
    EXECUTE = "execute"
    OBSERVE = "observe"
    VERIFY = "verify"
    DIAGNOSE = "diagnose"
    REPAIR = "repair"
    FINISH = "finish"


@dataclass
class Step:
    goal: str
    status: str = "pending"  # pending | running | done | failed
    result: Optional[str] = None
    attempts: int = 0
    hint: Optional[str] = None


@dataclass
class TaskReport:
    task: str
    phases: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    repairs: int = 0
    verified: bool = False
    status: str = "pending"
    evidence: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str = ""
    started: str = field(default_factory=lambda: datetime.now().isoformat())
    finished: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "phases": self.phases,
            "steps": [{"goal": s.goal, "status": s.status, "result": s.result, "attempts": s.attempts} for s in self.steps],
            "repairs": self.repairs,
            "verified": self.verified,
            "status": self.status,
            "final_answer": self.final_answer,
            "started": self.started,
            "finished": self.finished,
        }


class Verifier:
    """Default rule-based verifier.

    Considers a result verified when it is non-empty and does not contain an
    error/failure marker. Override ``verify`` for domain-specific checks.
    """

    ERROR_MARKERS = ("error", "exception", "traceback", "failed", "failure", "no such file",
                     "command not found", "refused", "timeout", "not running",
                     "no api key", "not installed", "permission denied", "denied")

    def verify(self, task: str, result: Any) -> dict[str, Any]:
        text = str(result or "").strip()
        low = text.lower()
        problems = [m for m in self.ERROR_MARKERS if m in low]
        ok = bool(text) and not problems
        return {
            "ok": ok,
            "problems": problems,
            "reason": "" if ok else f"markers found: {problems}" if problems else "empty result",
        }


class Diagnoser:
    """Default rule-based diagnoser: surfaces the failing marker as a hint."""

    def diagnose(self, task: str, result: Any, verification: dict[str, Any]) -> dict[str, Any]:
        problems = verification.get("problems") or []
        hint = f"Previous attempt failed ({', '.join(problems) or 'unsatisfactory result'})."
        return {"hint": hint, "retry": True}


class AutonomousRunner:
    def __init__(
        self,
        executor: Optional[Callable[[str], str]] = None,
        verifier: Optional[Any] = None,
        diagnoser: Optional[Any] = None,
        planner: Optional[Callable[[str], list[str]]] = None,
        max_repairs: int = 2,
    ):
        self.executor = executor or (lambda step: f"done: {step}")
        self.verifier = verifier or Verifier()
        self.diagnoser = diagnoser or Diagnoser()
        self.planner = planner or self._default_plan
        self.max_repairs = max_repairs

    @staticmethod
    def _default_plan(task: str) -> list[str]:
        return [task]

    def _call_executor(self, step: Step) -> str:
        """Call the executor with (goal, hint); fall back to (goal) for simple executors."""
        try:
            return self.executor(step.goal, step.hint)
        except TypeError:
            return self.executor(step.goal)

    def run(self, task: str) -> TaskReport:
        report = TaskReport(task=task)
        report.phases.append(Phase.UNDERSTAND.value)

        # PLAN
        report.phases.append(Phase.PLAN.value)
        plan = self.planner(task) or [task]
        report.steps = [Step(goal=s) for s in plan]

        repairs = 0
        while True:
            # EXECUTE
            report.phases.append(Phase.EXECUTE.value)
            for step in report.steps:
                if step.status == "done":
                    continue
                step.status = "running"
                step.attempts += 1
                try:
                    step.result = str(self._call_executor(step))
                    step.status = "done"
                except Exception as e:  # noqa: BLE001
                    step.result = f"error: {e}"
                    step.status = "failed"

            # OBSERVE — verify against *results*, not goals (goals may carry hints)
            report.phases.append(Phase.OBSERVE.value)
            combined = "\n".join(f"[{s.status}] {s.result}" for s in report.steps)
            report.evidence.append({"phase": "observe", "content": combined[:2000]})

            # VERIFY
            report.phases.append(Phase.VERIFY.value)
            verification = self.verifier.verify(task, combined)
            if verification.get("ok"):
                report.verified = True
                break

            # DIAGNOSE / REPAIR (bounded)
            if repairs >= self.max_repairs:
                break
            repairs += 1
            report.repairs = repairs
            report.phases.append(Phase.DIAGNOSE.value)
            diag = self.diagnoser.diagnose(task, combined, verification)
            report.evidence.append({"phase": "diagnose", "content": diag.get("hint", "")})
            report.phases.append(Phase.REPAIR.value)
            # re-open steps with the diagnosis attached as a hint (goal unchanged)
            hint = diag.get("hint", "")
            for step in report.steps:
                if step.status in ("failed", "done"):
                    step.status = "pending"
                    step.hint = hint

        report.phases.append(Phase.FINISH.value)
        report.status = "done" if report.verified else "failed"
        report.finished = datetime.now().isoformat()
        report.final_answer = combined if report.verified else (
            f"Task not verified after {report.repairs} repair(s).\n" + combined
        )
        return report


autonomous_runner = AutonomousRunner()
