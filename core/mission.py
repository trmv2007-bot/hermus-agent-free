"""Mission Engine for Hermus.

Implements the objective-driven mission lifecycle:
Goal → Requirements → Plan (DAG) → Execute → Observe → Verify → Repair → Continue → Final Proof.
Features dynamic step budgets, structured evidence collection, checkpointing, auto-resumption,
domain verification, and explicit BLOCKED states.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .agent_dag import AgentDAG, DAGNodeStatus
from .artifact_manager import artifact_manager
from .critic import critic_manager
from .rollback import rollback_manager
from .verifier_registry import verifier_registry
from .workspace import workspace


class MissionState(str, Enum):
    PENDING = "pending"
    REQUIREMENTS = "requirements"
    PLANNING = "planning"
    EXECUTING = "executing"
    OBSERVING = "observing"
    VERIFYING = "verifying"
    DIAGNOSING = "diagnosing"
    REPAIRING = "repairing"
    CONTINUING = "continuing"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class MissionRequirement:
    id: str
    description: str
    satisfied: bool = False
    evidence: List[str] = field(default_factory=list)
    verifier_domain: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MissionSubGoal:
    id: str
    goal: str
    dependencies: List[str] = field(default_factory=list)
    status: str = DAGNodeStatus.PENDING.value
    attempts: int = 0
    result: Optional[str] = None
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    repair_hints: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MissionBudget:
    initial_steps: int = 20
    consumed_steps: int = 0
    max_repairs: int = 3
    repairs_used: int = 0
    max_extensions: int = 3
    extensions_used: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MissionReport:
    mission_id: str
    goal: str
    state: str = MissionState.PENDING.value
    domain: str = "generic"
    confidence_score: float = 0.0
    progress_pct: int = 0
    requirements: List[MissionRequirement] = field(default_factory=list)
    subgoals: List[MissionSubGoal] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    blocker_reason: Optional[str] = None
    blocker_instructions: Optional[str] = None
    checkpoint_id: Optional[str] = None
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    finished_at: Optional[str] = None
    final_proof: str = ""
    budget: MissionBudget = field(default_factory=MissionBudget)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "goal": self.goal,
            "state": self.state,
            "domain": self.domain,
            "confidence_score": self.confidence_score,
            "progress_pct": self.progress_pct,
            "requirements": [r.to_dict() for r in self.requirements],
            "subgoals": [s.to_dict() for s in self.subgoals],
            "evidence": self.evidence,
            "artifacts": self.artifacts,
            "blocker_reason": self.blocker_reason,
            "blocker_instructions": self.blocker_instructions,
            "checkpoint_id": self.checkpoint_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "final_proof": self.final_proof,
            "budget": self.budget.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MissionReport:
        reqs = [MissionRequirement(**r) for r in data.get("requirements", [])]
        subgoals = [MissionSubGoal(**s) for s in data.get("subgoals", [])]
        budget = MissionBudget(**data.get("budget", {})) if "budget" in data else MissionBudget()
        return cls(
            mission_id=data["mission_id"],
            goal=data["goal"],
            state=data.get("state", MissionState.PENDING.value),
            domain=data.get("domain", "generic"),
            confidence_score=data.get("confidence_score", 0.0),
            progress_pct=data.get("progress_pct", 0),
            requirements=reqs,
            subgoals=subgoals,
            evidence=data.get("evidence", []),
            artifacts=data.get("artifacts", []),
            blocker_reason=data.get("blocker_reason"),
            blocker_instructions=data.get("blocker_instructions"),
            checkpoint_id=data.get("checkpoint_id"),
            started_at=data.get("started_at", datetime.now().isoformat()),
            finished_at=data.get("finished_at"),
            final_proof=data.get("final_proof", ""),
            budget=budget,
        )


class MissionEngine:
    """Mission Lifecycle Orchestrator."""

    def __init__(
        self,
        executor: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
        storage_dir: Optional[Path] = None,
    ):
        self.executor = executor or self._default_executor
        self.storage_dir = storage_dir or (workspace.root / "missions")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _default_executor(self, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Default mock/deterministic step executor."""
        return {
            "success": True,
            "output": f"Executed step: {goal}",
            "evidence": [{"step": goal, "status": "completed"}],
        }

    def _save_mission(self, report: MissionReport) -> None:
        p = self.storage_dir / f"{report.mission_id}.json"
        p.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    def get_mission(self, mission_id: str) -> Optional[MissionReport]:
        p = self.storage_dir / f"{mission_id}.json"
        if not p.exists():
            return None
        try:
            return MissionReport.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def list_missions(self) -> List[MissionReport]:
        missions: List[MissionReport] = []
        if not self.storage_dir.exists():
            return missions
        for p in self.storage_dir.glob("*.json"):
            try:
                missions.append(MissionReport.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                continue
        missions.sort(key=lambda m: m.started_at, reverse=True)
        return missions

    def start_mission(
        self,
        goal: str,
        requirements: Optional[List[str]] = None,
        domain: Optional[str] = None,
        subgoals: Optional[List[str]] = None,
        budget_steps: int = 20,
    ) -> MissionReport:
        mid = f"msn_{int(time.time())}_{os.urandom(2).hex()}"
        detected_domain = domain or verifier_registry.auto_detect_domain(goal)

        # 1. Establish Requirements
        req_objs = []
        raw_reqs = requirements or [f"Complete: {goal}"]
        for idx, r in enumerate(raw_reqs, start=1):
            req_objs.append(MissionRequirement(
                id=f"req_{idx}",
                description=r,
                verifier_domain=detected_domain,
            ))

        # Checkpoint workspace before starting
        cp = rollback_manager.checkpoint(label=f"mission_start_{mid}")

        # 2. Plan Sub-goals
        subgoal_objs = []
        if subgoals:
            for idx, sg in enumerate(subgoals, start=1):
                deps = [f"sg_{idx-1}"] if idx > 1 else []
                subgoal_objs.append(MissionSubGoal(id=f"sg_{idx}", goal=sg, dependencies=deps))
        else:
            # Generate default plan steps
            subgoal_objs = [
                MissionSubGoal(id="sg_1", goal=f"Analyze and design solution for {goal}"),
                MissionSubGoal(id="sg_2", goal=f"Implement components and deliverables for {goal}", dependencies=["sg_1"]),
                MissionSubGoal(id="sg_3", goal=f"Verify outputs, execute tests, and package deliverables", dependencies=["sg_2"]),
            ]

        report = MissionReport(
            mission_id=mid,
            goal=goal,
            domain=detected_domain,
            state=MissionState.PLANNING.value,
            requirements=req_objs,
            subgoals=subgoal_objs,
            checkpoint_id=cp.id,
            budget=MissionBudget(initial_steps=budget_steps),
        )
        self._save_mission(report)

        # Run Mission Loop
        return self._run_mission_lifecycle(report)

    def _run_mission_lifecycle(self, report: MissionReport) -> MissionReport:
        budget = report.budget

        while budget.consumed_steps < (budget.initial_steps + (budget.extensions_used * 10)):
            # Check for uncompleted subgoals
            pending_subgoals = [sg for sg in report.subgoals if sg.status in (DAGNodeStatus.PENDING.value, DAGNodeStatus.READY.value)]
            if not pending_subgoals:
                break

            for sg in pending_subgoals:
                # Check dependencies
                deps_ok = all(
                    any(s.id == dep and s.status == DAGNodeStatus.COMPLETED.value for s in report.subgoals)
                    for dep in sg.dependencies
                )
                if not deps_ok:
                    continue

                report.state = MissionState.EXECUTING.value
                sg.status = DAGNodeStatus.RUNNING.value
                sg.attempts += 1
                budget.consumed_steps += 1
                self._save_mission(report)

                # Execute subgoal
                try:
                    ctx = {
                        "mission_id": report.mission_id,
                        "goal": report.goal,
                        "subgoal": sg.goal,
                        "repair_hints": sg.repair_hints,
                        "evidence": report.evidence,
                    }
                    res = self.executor(sg.goal, ctx)
                    sg.result = str(res.get("output", res))
                    if res.get("evidence"):
                        sg.evidence.extend(res["evidence"])
                        report.evidence.extend(res["evidence"])

                    # Observe & Verify Subgoal
                    report.state = MissionState.VERIFYING.value
                    if res.get("blocked"):
                        report.state = MissionState.BLOCKED.value
                        report.blocker_reason = res.get("blocker_reason", "External blocker encountered")
                        report.blocker_instructions = res.get("blocker_instructions", "Please provide required permissions or resources")
                        sg.status = DAGNodeStatus.BLOCKED.value
                        self._save_mission(report)
                        return report

                    if res.get("success", True) and not res.get("error"):
                        sg.status = DAGNodeStatus.COMPLETED.value
                    else:
                        sg.status = DAGNodeStatus.FAILED.value
                        # Auto-repair loop
                        if budget.repairs_used < budget.max_repairs:
                            budget.repairs_used += 1
                            report.state = MissionState.REPAIRING.value
                            sg.repair_hints.append(f"Attempt {sg.attempts} failed: {res.get('error', 'Unsatisfactory result')}")
                            sg.status = DAGNodeStatus.READY.value

                except Exception as e:
                    sg.status = DAGNodeStatus.FAILED.value
                    sg.result = f"Error: {e}"
                    if budget.repairs_used < budget.max_repairs:
                        budget.repairs_used += 1
                        sg.repair_hints.append(f"Exception encountered: {e}")
                        sg.status = DAGNodeStatus.READY.value

                # Update progress percentage
                completed_count = sum(1 for s in report.subgoals if s.status == DAGNodeStatus.COMPLETED.value)
                report.progress_pct = int((completed_count / max(1, len(report.subgoals))) * 80)
                self._save_mission(report)

        # Final Verification Phase (Domain Verifier + Critic Review)
        report.state = MissionState.VERIFYING.value
        all_artifacts = artifact_manager.scan_workspace(mission_id=report.mission_id)
        report.artifacts = [a.path for a in all_artifacts]

        v_res = verifier_registry.verify(
            domain_or_auto=report.domain,
            context={
                "task": report.goal,
                "output": "\n".join(str(s.result or "") for s in report.subgoals),
                "artifacts": report.artifacts,
            },
        )
        report.evidence.extend(v_res.evidence)

        # Mark requirements satisfied
        for req in report.requirements:
            req.satisfied = v_res.verified
            req.evidence = [str(e) for e in v_res.evidence]

        report.confidence_score = v_res.score

        if v_res.verified:
            report.state = MissionState.COMPLETED.value
            report.progress_pct = 100
            report.finished_at = datetime.now().isoformat()
            report.final_proof = f"Mission successfully verified with domain '{report.domain}' verifier (Confidence: {int(v_res.score*100)}%). Artifacts: {len(report.artifacts)}."
        else:
            if budget.extensions_used < budget.max_extensions:
                budget.extensions_used += 1
                report.state = MissionState.CONTINUING.value
                # Dynamic budget extension when partial verification is observed
            else:
                report.state = MissionState.FAILED.value
                report.finished_at = datetime.now().isoformat()
                report.final_proof = f"Verification failed after {budget.consumed_steps} steps and {budget.repairs_used} repairs. Errors: {'; '.join(v_res.errors)}"

        self._save_mission(report)
        return report

    def resume_mission(self, mission_id: str) -> MissionReport:
        report = self.get_mission(mission_id)
        if not report:
            raise ValueError(f"Mission {mission_id} not found")

        if report.state in (MissionState.COMPLETED.value, MissionState.FAILED.value):
            return report

        # Reopen blocked / failed subgoals if unblocked
        for sg in report.subgoals:
            if sg.status in (DAGNodeStatus.BLOCKED.value, DAGNodeStatus.FAILED.value):
                sg.status = DAGNodeStatus.READY.value

        report.state = MissionState.CONTINUING.value
        report.blocker_reason = None
        report.blocker_instructions = None
        return self._run_mission_lifecycle(report)


mission_engine = MissionEngine()
