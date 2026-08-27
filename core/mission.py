"""Mission Engine for Hermus.

Unifies the Mission Lifecycle directly with the Agent DAG and executes the full
autonomous repair and replan loop:
Goal → Requirements → DAG Plan → Execute → Observe → Verify (Structural + Behavioral) → Critic Panel →
If verification fails: Diagnose → Repair → Re-plan → Re-execute → Repeat until success, blocked, or budget exhausted.
"""
from __future__ import annotations

import inspect
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable

from .agent_dag import AgentDAG, DAGNode, DAGNodeStatus
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
    evidence: list[str] = field(default_factory=list)
    verifier_domain: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SubGoal:
    id: str
    goal: str
    role: str = "specialist"
    status: str = DAGNodeStatus.PENDING.value
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MissionBudget:
    initial_steps: int = 25
    consumed_steps: int = 0
    max_repairs: int = 3
    repairs_used: int = 0
    max_extensions: int = 2
    extensions_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MissionReport:
    mission_id: str
    goal: str
    state: str = MissionState.PENDING.value
    domain: str = "generic"
    confidence_score: float = 0.0
    progress_pct: int = 0
    requirements: list[MissionRequirement] = field(default_factory=list)
    dag_state: dict[str, Any] = field(default_factory=dict)
    subgoals: list[SubGoal] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    blocker_reason: Optional[str] = None
    blocker_instructions: Optional[str] = None
    checkpoint_id: Optional[str] = None
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    finished_at: Optional[str] = None
    final_proof: str = ""
    budget: MissionBudget = field(default_factory=MissionBudget)
    repair_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "goal": self.goal,
            "state": self.state,
            "domain": self.domain,
            "confidence_score": self.confidence_score,
            "progress_pct": self.progress_pct,
            "requirements": [r.to_dict() for r in self.requirements],
            "dag_state": self.dag_state,
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
            "repair_history": self.repair_history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionReport:
        reqs = [MissionRequirement(**r) for r in data.get("requirements", [])]
        subgoals = [SubGoal(**s) if isinstance(s, dict) else s for s in data.get("subgoals", [])]
        budget = MissionBudget(**data.get("budget", {})) if "budget" in data else MissionBudget()
        return cls(
            mission_id=data["mission_id"],
            goal=data["goal"],
            state=data.get("state", MissionState.PENDING.value),
            domain=data.get("domain", "generic"),
            confidence_score=data.get("confidence_score", 0.0),
            progress_pct=data.get("progress_pct", 0),
            requirements=reqs,
            dag_state=data.get("dag_state", {}),
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
            repair_history=data.get("repair_history", []),
        )


class MissionEngine:
    """Unified Mission Lifecycle & DAG Controller with Autonomous Repair Loop."""

    def __init__(
        self,
        executor: Optional[Callable[[Any, dict[str, Any]], dict[str, Any]]] = None,
        storage_dir: Optional[Path] = None,
    ):
        self._raw_executor = executor or self._default_node_executor
        self.storage_dir = storage_dir or (workspace.root / "missions")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _default_node_executor(self, node: DAGNode, parent_ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": True,
            "output": f"Executed stage '{node.role}': {node.goal}",
            "evidence": [{"stage": node.role, "goal": node.goal, "status": "completed"}],
        }

    @staticmethod
    def _compute_progress(dag: AgentDAG) -> int:
        """Completion percentage over DAG nodes (0 when the graph is empty)."""
        total = len(dag.nodes)
        if total == 0:
            return 0
        completed = sum(1 for n in dag.nodes.values() if n.status == DAGNodeStatus.COMPLETED.value)
        return int(100 * completed / total)

    @staticmethod
    def _read_capped(path: Path, max_bytes: int = 64_000) -> str:
        """Read a text artifact capped at ``max_bytes`` so a huge build output
        cannot exhaust memory when assembling critic review context."""
        try:
            with open(path, "rb") as f:
                data = f.read(max_bytes)
            return data.decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def _call_executor(self, node: DAGNode, parent_ctx: dict[str, Any]) -> dict[str, Any]:
        try:
            sig = inspect.signature(self._raw_executor)
            params = list(sig.parameters.keys())
            if params and params[0] in ("goal", "task", "prompt", "subgoal"):
                return self._raw_executor(node.goal, parent_ctx)
        except Exception:
            pass
        return self._raw_executor(node, parent_ctx)

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

    def list_missions(self) -> list[MissionReport]:
        missions: list[MissionReport] = []
        if not self.storage_dir.exists():
            return missions
        for p in self.storage_dir.glob("*.json"):
            try:
                missions.append(MissionReport.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                continue
        missions.sort(key=lambda m: m.started_at, reverse=True)
        return missions

    def _build_mission_dag(self, goal: str, domain: str, subgoals: Optional[list[str]] = None) -> AgentDAG:
        dag = AgentDAG(name=f"Mission: {goal[:50]}")
        if subgoals:
            for idx, sg in enumerate(subgoals, start=1):
                deps = [f"node_{idx-1}"] if idx > 1 else []
                dag.add_node(f"node_{idx}", "specialist", sg, dependencies=deps, max_retries=0)
        else:
            dag.add_node("spec", "architect", f"Analyze requirements and design architecture for: {goal}", max_retries=0)
            dag.add_node("impl", "coder", f"Implement components, logic, and tests for: {goal}", dependencies=["spec"], max_retries=0)
            dag.add_node("review", "reviewer", "Review code changes and security adherence", dependencies=["impl"], max_retries=0)
            dag.add_node("verify", "verifier", f"Execute domain tests and verify deliverables for: {goal}", dependencies=["review"], max_retries=0)
        return dag

    def start_mission(
        self,
        goal: str,
        requirements: Optional[list[str]] = None,
        domain: Optional[str] = None,
        subgoals: Optional[list[str]] = None,
        budget_steps: int = 25,
    ) -> MissionReport:
        mid = f"msn_{int(time.time())}_{os.urandom(2).hex()}"
        detected_domain = domain or verifier_registry.auto_detect_domain(goal)

        req_objs = []
        raw_reqs = requirements or [f"Complete: {goal}"]
        for idx, r in enumerate(raw_reqs, start=1):
            req_objs.append(MissionRequirement(
                id=f"req_{idx}",
                description=r,
                verifier_domain=detected_domain,
            ))

        cp = rollback_manager.checkpoint(label=f"mission_start_{mid}")
        dag = self._build_mission_dag(goal, detected_domain, subgoals)

        report = MissionReport(
            mission_id=mid,
            goal=goal,
            domain=detected_domain,
            state=MissionState.PLANNING.value,
            requirements=req_objs,
            dag_state=dag.to_dict(),
            subgoals=[SubGoal(id=nid, role=n.role, goal=n.goal, status=n.status) for nid, n in dag.nodes.items()],
            checkpoint_id=cp.id,
            budget=MissionBudget(initial_steps=budget_steps),
        )
        self._save_mission(report)

        return self._run_autonomous_loop(report, dag)

    def _run_autonomous_loop(self, report: MissionReport, dag: AgentDAG) -> MissionReport:
        budget = report.budget
        mission_start_ts = datetime.fromisoformat(report.started_at).timestamp()
        prev_completed = -1

        while budget.consumed_steps < (budget.initial_steps + (budget.extensions_used * 10)):
            report.state = MissionState.EXECUTING.value
            self._save_mission(report)

            # A. EXECUTE DAG STAGES
            dag_round_res = dag.execute_dag(
                node_executor=self._call_executor,
                max_rounds=15,
            )
            budget.consumed_steps += dag_round_res.get("completed", 1)
            report.dag_state = dag.to_dict()
            report.subgoals = [SubGoal(id=nid, role=n.role, goal=n.goal, status=n.status) for nid, n in dag.nodes.items()]
            report.progress_pct = self._compute_progress(dag)
            self._save_mission(report)

            # Dynamic step budget (roadmap): when a round makes verified DAG
            # progress (more nodes completed than the previous round), grant
            # one budget extension (up to max_extensions, +10 steps each) so
            # promising missions are not cut off by a rigid turn count.
            completed_now = dag_round_res.get("completed", 0)
            if (
                prev_completed >= 0
                and completed_now > prev_completed
                and budget.extensions_used < budget.max_extensions
            ):
                budget.extensions_used += 1
            prev_completed = completed_now

            # Check for explicit external blockers
            blocked_nodes = [n for n in dag.nodes.values() if n.status == DAGNodeStatus.BLOCKED.value]
            if blocked_nodes:
                report.state = MissionState.BLOCKED.value
                report.blocker_reason = blocked_nodes[0].error or "External prerequisite or authorization required"
                report.blocker_instructions = "Please resolve the blocker and call `hermus mission resume <mission_id>`"
                self._save_mission(report)
                return report

            # B. OBSERVE & GATHER EVIDENCE
            report.state = MissionState.OBSERVING.value
            all_node_outputs = []
            for n in dag.nodes.values():
                if n.outputs:
                    out = n.outputs.get("output", n.outputs)
                    all_node_outputs.append(str(out))
                    # Also include any structured evidence emitted by nodes
                    if isinstance(n.outputs, dict) and "evidence" in n.outputs:
                        for ev in n.outputs["evidence"]:
                            if ev not in report.evidence:
                                report.evidence.append(ev)
            combined_log = "\n".join(all_node_outputs)

            artifacts = artifact_manager.scan_workspace(
                mission_id=report.mission_id,
                since_timestamp=mission_start_ts,
            )
            report.artifacts = [a.path for a in artifacts]

            # C. VERIFY (STRUCTURAL + BEHAVIORAL + CRITIC)
            report.state = MissionState.VERIFYING.value
            v_res = verifier_registry.verify(
                domain_or_auto=report.domain,
                context={
                    "task": report.goal,
                    "output": combined_log,
                    "artifacts": report.artifacts,
                },
            )
            for ev in v_res.evidence:
                if ev not in report.evidence:
                    report.evidence.append(ev)
            report.confidence_score = v_res.score

            files_content = {
                Path(a).name: self._read_capped(Path(a))
                for a in report.artifacts
                if Path(a).suffix in (".py", ".js", ".ts", ".html", ".json") and Path(a).exists()
            }

            critic_res = critic_manager.run_full_review(
                task=report.goal,
                files_content=files_content,
                execution_log=combined_log,
                artifacts=report.artifacts,
                requirements=[r.description for r in report.requirements],
                verification_evidence=v_res.evidence,
            )

            # Success condition
            all_dag_completed = all(n.status == DAGNodeStatus.COMPLETED.value for n in dag.nodes.values())
            if all_dag_completed and v_res.verified and critic_res.get("approved"):
                for req in report.requirements:
                    req.satisfied = True
                    req.evidence = [str(e) for e in v_res.evidence]

                report.state = MissionState.COMPLETED.value
                report.progress_pct = 100
                report.finished_at = datetime.now().isoformat()
                report.final_proof = (
                    f"Mission successfully verified with domain '{report.domain}' verifier "
                    f"(Structural: {int(v_res.structural_score*100)}%, Behavioral: {int(v_res.behavioral_score*100)}%, "
                    f"Critic Score: {critic_res['overall_score']}/100). "
                    f"Produced {len(report.artifacts)} verified deliverables."
                )
                self._save_mission(report)
                return report

            # D. DIAGNOSE & REPAIR LOOP
            if budget.repairs_used < budget.max_repairs:
                budget.repairs_used += 1
                report.state = MissionState.DIAGNOSING.value

                diagnosis = {
                    "repair_round": budget.repairs_used,
                    "verifier_errors": v_res.errors,
                    "critic_directives": critic_res.get("repair_directives", []),
                    "timestamp": datetime.now().isoformat(),
                }
                report.repair_history.append(diagnosis)

                report.state = MissionState.REPAIRING.value
                repair_hints = v_res.errors + critic_res.get("repair_directives", [])
                for node in dag.nodes.values():
                    node.status = DAGNodeStatus.READY.value
                    node.inputs["repair_hints"] = repair_hints
                    node.retries = 0

                report.state = MissionState.PLANNING.value
                self._save_mission(report)
                continue
            else:
                break

        report.state = MissionState.FAILED.value
        report.finished_at = datetime.now().isoformat()
        # Never report 100% for a failed mission: DAG stages may all have run,
        # but the mission only "completes" when verification passes.
        report.progress_pct = min(95, self._compute_progress(dag))
        report.final_proof = f"Mission failed to achieve verifiable behavioral completion after {budget.consumed_steps} steps and {budget.repairs_used} repair attempts."
        self._save_mission(report)
        return report

    def resume_mission(self, mission_id: str) -> MissionReport:
        report = self.get_mission(mission_id)
        if not report:
            raise ValueError(f"Mission {mission_id} not found")

        if report.state in (MissionState.COMPLETED.value, MissionState.FAILED.value):
            return report

        dag = AgentDAG.from_dict(report.dag_state) if report.dag_state else self._build_mission_dag(report.goal, report.domain)

        for node in dag.nodes.values():
            if node.status in (DAGNodeStatus.BLOCKED.value, DAGNodeStatus.FAILED.value, DAGNodeStatus.SKIPPED.value):
                node.status = DAGNodeStatus.READY.value
                node.retries = 0

        report.state = MissionState.CONTINUING.value
        report.blocker_reason = None
        report.blocker_instructions = None
        return self._run_autonomous_loop(report, dag)

    def extend_budget(self, mission_id: str, steps: int = 10) -> MissionReport:
        """Grant extra steps to a running/blocked/failed mission.

        Explicit counterpart to the automatic progress-based extension: the
        loop bound grows by ``10 * extensions_used``, capped at
        ``max_extensions``; each explicit extension consumes one slot.
        """
        report = self.get_mission(mission_id)
        if not report:
            raise ValueError(f"Mission {mission_id} not found")

        if report.state == MissionState.COMPLETED.value:
            return report

        if report.budget.extensions_used >= report.budget.max_extensions:
            raise ValueError(
                f"Mission {mission_id} already used its "
                f"{report.budget.max_extensions} budget extensions"
            )

        report.budget.extensions_used += 1
        report.budget.initial_steps += max(1, int(steps))
        self._save_mission(report)
        return report


mission_engine = MissionEngine()
