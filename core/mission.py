"""Mission Engine for Hermus.

Unifies the Mission Lifecycle directly with the Agent DAG and executes the full
autonomous repair and replan loop:
Goal → Requirements → DAG Plan → Execute → Observe → Verify (Structural + Behavioral) → Critic Panel →
If verification fails: Diagnose → Repair → Re-plan → Re-execute → Repeat until success, blocked, or budget exhausted.

This module is the **universal execution core**: every autonomous surface
(``HermusAgent.autonomous``, ``/command?autonomous``, the gateway queue, the
CLI, channels, the scheduler) runs through :class:`MissionEngine` via
``core.runtime``, so behavior no longer depends on the entry point.

Node executors are **evidence-gated**, and the gate is driven by the *expected
output type* of the stage rather than by its role name:

===============  ==========================================================
expected output  what counts as doing the job
===============  ==========================================================
``change``       files created/modified (code, configs, deliverables)
``execution``    commands/tests actually run (shell, sandbox, browser, …)
``analysis``     a substantive written finding (review, diagnosis, report)
===============  ==========================================================

A verifier that reports *"tests failed because X"* has done its job: it is an
``analysis`` stage and no file change is required. A coder that writes a
beautiful description of the code it *would* write has not: it is a ``change``
stage and prose is not evidence. Supporting actions (``memory_add``,
``slack_notify``, ``embeddings_add``, …) never satisfy a goal-completion gate.

Failure semantics (see :mod:`core.runtime`)
-------------------------------------------
A mission that crashes does **not** degrade into a chat answer. The engine
persists a ``failed`` report carrying the error, the stage it died in and its
recoverability, so the run can be diagnosed, repaired and resumed::

    MISSION ERROR → MISSION FAILED (diagnostics + resume handle)
                ↛  never a silent chat turn

Budgets are a hierarchy (planning → execution → verification → repair →
emergency), and each mission gets its own isolated workspace
(``<HERMUS_HOME>/missions/<mission_id>/workspace``) plus a precise per-mission
file baseline, so concurrent autonomous jobs cannot leak evidence into each
other.
"""
from __future__ import annotations

import inspect
import json
import os
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable

from .agent_dag import AgentDAG, DAGNode, DAGNodeStatus
from .artifact_manager import artifact_manager
from .atomic_io import atomic_write_json, file_lock
from .critic import critic_manager
from .mission_files import MissionFileScope
from .rollback import rollback_manager
from .run_events import record_issue
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
    CANCELLED = "cancelled"
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


# ------------------------------------------------------------------ budgets
#: mission lifecycle phases that draw on the budget
PHASE_PLANNING = "planning"
PHASE_EXECUTION = "execution"
PHASE_VERIFICATION = "verification"
PHASE_REPAIR = "repair"
PHASE_EMERGENCY = "emergency"
MISSION_PHASES = (PHASE_PLANNING, PHASE_EXECUTION, PHASE_VERIFICATION,
                  PHASE_REPAIR, PHASE_EMERGENCY)

#: how the total budget is split across the lifecycle (fractions of total)
PHASE_SHARES = {
    PHASE_PLANNING: 0.08,
    PHASE_EXECUTION: 0.55,
    PHASE_VERIFICATION: 0.12,
    PHASE_REPAIR: 0.20,
    PHASE_EMERGENCY: 0.05,
}
#: never allocate less than this to a phase, however small the mission
PHASE_MINIMUM = {
    PHASE_PLANNING: 1,
    PHASE_EXECUTION: 4,
    PHASE_VERIFICATION: 2,
    PHASE_REPAIR: 1,
    PHASE_EMERGENCY: 1,
}

#: Default mission budget. A mission owns the whole lifecycle (plan →
#: implement → test → inspect → repair → retest), so it must be strictly larger
#: than a single agent turn (``config.max_tool_steps`` = 32), not smaller: with
#: the old default of 25 a real coding mission ran out of steps before the
#: first repair round finished.
DEFAULT_MISSION_BUDGET = 48


@dataclass
class MissionBudget:
    """Hierarchical step budget.

    The overall loop bound (``total_steps()``) is the sum of the phase
    allocations below it::

        Mission budget
          ├─ planning       requirement analysis + DAG build
          ├─ execution      DAG rounds (implement / write / run)
          ├─ verification   observe + verify + critic panel
          ├─ repair         diagnose + repair replans
          └─ emergency      reserve a phase borrows from when it runs dry

    Phase accounting is additive to the global counter: consuming a step in a
    phase consumes one global step too. When a phase runs dry but the mission
    still has budget, it borrows from the emergency reserve
    (:meth:`borrow`) instead of failing the whole mission.
    """

    initial_steps: int = DEFAULT_MISSION_BUDGET
    consumed_steps: int = 0
    max_repairs: int = 3
    repairs_used: int = 0
    max_extensions: int = 2
    extensions_used: int = 0
    # Extra steps granted by explicit/auto extensions. Previously the loop
    # bound was `initial_steps + extensions_used*10` while extend_budget() also
    # added to initial_steps, double-counting every extension.
    bonus_steps: int = 0
    # Emergency extensions are outside the normal extension slots: they exist
    # so a mission that is *provably* still making progress can be rescued
    # after the ordinary budget is gone (see MissionEngine.extend_budget).
    emergency_extensions: int = 0
    max_emergency_extensions: int = 2
    #: phase → {"limit": int, "used": int}
    phases: dict[str, dict[str, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.allocate()

    # -- allocation ----------------------------------------------------
    def allocate(self, total: Optional[int] = None) -> None:
        """Split ``total`` across the lifecycle phases (never shrinks a limit)."""
        total = int(total if total is not None else self.total_steps())
        for name in MISSION_PHASES:
            limit = max(
                PHASE_MINIMUM.get(name, 1),
                int(round(total * PHASE_SHARES.get(name, 0.0))),
            )
            entry = self.phases.setdefault(name, {"limit": limit, "used": 0})
            entry["limit"] = max(int(entry.get("limit") or 0), limit)

    def phase(self, name: str) -> dict[str, int]:
        if name not in self.phases:
            self.allocate()
        return self.phases.setdefault(name, {"limit": 1, "used": 0})

    # -- global --------------------------------------------------------
    def total_steps(self) -> int:
        return self.initial_steps + self.bonus_steps

    def steps_left(self) -> int:
        return max(0, self.total_steps() - self.consumed_steps)

    # -- per-phase accounting -----------------------------------------
    def remaining(self, name: str) -> int:
        entry = self.phase(name)
        return max(0, int(entry.get("limit") or 0) - int(entry.get("used") or 0))

    def exhausted(self, name: str) -> bool:
        return self.remaining(name) <= 0

    def consume(self, name: str, steps: int = 1) -> None:
        """Spend ``steps`` in phase ``name`` (and on the global counter)."""
        steps = max(0, int(steps))
        entry = self.phase(name)
        entry["used"] = int(entry.get("used") or 0) + steps
        self.consumed_steps += steps

    def borrow(self, name: str, steps: int = 1) -> bool:
        """Move ``steps`` from the emergency reserve into phase ``name``."""
        steps = max(1, int(steps))
        if self.remaining(PHASE_EMERGENCY) < steps:
            return False
        self.phase(PHASE_EMERGENCY)["limit"] -= steps
        self.phase(name)["limit"] += steps
        return True

    # -- extensions ----------------------------------------------------
    def grant_extension(self, steps: int = 10) -> None:
        """Consume one extension slot and add exactly ``steps`` budget steps.

        The steps are not added to a single counter: ~60% go to execution and
        the rest to the emergency reserve, so an extension actually reaches the
        phase that ran out.
        """
        self.extensions_used += 1
        steps = max(1, int(steps))
        self.bonus_steps += steps
        exec_share = max(1, int(steps * 0.6))
        self.phase(PHASE_EXECUTION)["limit"] += exec_share
        self.phase(PHASE_EMERGENCY)["limit"] += max(1, steps - exec_share)
        return True

    def grant_emergency_extension(self, steps: int = 8) -> bool:
        """Last-resort extension (does not consume a normal extension slot)."""
        if self.emergency_extensions >= self.max_emergency_extensions:
            return False
        self.emergency_extensions += 1
        steps = max(1, int(steps))
        self.bonus_steps += steps
        self.phase(PHASE_EMERGENCY)["limit"] += steps
        return True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["total_steps"] = self.total_steps()
        d["steps_left"] = self.steps_left()
        d["phases"] = {
            name: {
                "limit": int(self.phase(name).get("limit") or 0),
                "used": int(self.phase(name).get("used") or 0),
                "remaining": self.remaining(name),
            }
            for name in MISSION_PHASES
        }
        return d


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
    approval_request: Optional[dict[str, Any]] = None
    preflight: Optional[dict[str, Any]] = None
    create_prompts_action: Optional[dict[str, Any]] = None
    checkpoint_id: Optional[str] = None
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    finished_at: Optional[str] = None
    final_proof: str = ""
    budget: MissionBudget = field(default_factory=MissionBudget)
    repair_history: list[dict[str, Any]] = field(default_factory=list)
    # ---- failure / recovery -------------------------------------------------
    # A crashed mission is recorded, not swallowed: ``error`` carries the
    # exception, the lifecycle stage it happened in and whether a restart is
    # likely to help. See MissionEngine.start_mission / resume_mission.
    error: Optional[dict[str, Any]] = None
    recoverable: bool = True
    restarts_used: int = 0

    # -- state helpers ------------------------------------------------------
    TERMINAL_STATES = (MissionState.COMPLETED.value, MissionState.CANCELLED.value)
    #: states a plain ``resume_mission()`` will pick up again
    RESUMABLE_STATES = (
        MissionState.PENDING.value, MissionState.REQUIREMENTS.value,
        MissionState.PLANNING.value, MissionState.EXECUTING.value,
        MissionState.OBSERVING.value, MissionState.VERIFYING.value,
        MissionState.DIAGNOSING.value, MissionState.REPAIRING.value,
        MissionState.CONTINUING.value, MissionState.BLOCKED.value,
    )

    def is_terminal(self) -> bool:
        return self.state in self.TERMINAL_STATES

    def is_resumable(self, *, allow_restart: bool = False) -> bool:
        """Can this mission be picked up again right now?

        ``blocked`` / ``paused`` / interrupted runs are resumable; ``failed`` is
        terminal *by default* and needs an explicit restart
        (``resume_mission(..., restart_failed=True)``) so a crash-looping
        mission is never auto-resumed by accident.
        """
        if self.state == MissionState.FAILED.value:
            return bool(allow_restart and self.recoverable)
        return self.state in self.RESUMABLE_STATES

    def failure_summary(self) -> dict[str, Any]:
        """Structured diagnostics for a non-completed mission."""
        err = self.error or {}
        return {
            "mission_id": self.mission_id,
            "state": self.state,
            "stage": err.get("stage") or self.state,
            "reason": (
                err.get("message")
                or self.blocker_reason
                or self.final_proof
                or f"mission ended in state '{self.state}'"
            ),
            "error_type": err.get("type"),
            "recoverable": bool(self.recoverable) and not self.is_terminal(),
            "resumable": self.is_resumable(),
            "resume_with_restart": self.is_resumable(allow_restart=True),
            "restarts_used": self.restarts_used,
            "budget": self.budget.to_dict(),
            "approval_request": self.approval_request,
            "preflight": self.preflight,
            "create_prompts_action": self.create_prompts_action,
            "resume_command": f"hermus mission resume {self.mission_id}"
            + (" --restart-failed" if self.state == MissionState.FAILED.value else ""),
        }

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
            "approval_request": self.approval_request,
            "preflight": self.preflight,
            "create_prompts_action": self.create_prompts_action,
            "checkpoint_id": self.checkpoint_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "final_proof": self.final_proof,
            # Canonical response field used across /command and the dashboard.
            # ``final_proof`` is retained as the human-readable mission summary.
            "response": self.final_proof,
            "budget": self.budget.to_dict(),
            "repair_history": self.repair_history,
            "error": self.error,
            "recoverable": self.recoverable,
            "restarts_used": self.restarts_used,
            "resumable": self.is_resumable(),
            # diagnostics for every non-completed mission (stage/reason/resume)
            "failure": (self.failure_summary()
                        if self.state != MissionState.COMPLETED.value else None),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionReport:
        reqs = [MissionRequirement(**r) for r in data.get("requirements", [])]
        subgoals = [SubGoal(**s) if isinstance(s, dict) else s for s in data.get("subgoals", [])]
        if "budget" in data and data["budget"]:
            # Drop computed/derived keys so older/newer payloads (e.g. the
            # serialized ``total_steps`` helper) never break deserialization.
            _budget_fields = {f for f in MissionBudget.__dataclass_fields__}
            _budget_data = {k: v for k, v in data["budget"].items() if k in _budget_fields}
            budget = MissionBudget(**_budget_data)
        else:
            budget = MissionBudget()
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
            approval_request=data.get("approval_request"),
            preflight=data.get("preflight"),
            create_prompts_action=data.get("create_prompts_action"),
            checkpoint_id=data.get("checkpoint_id"),
            started_at=data.get("started_at", datetime.now().isoformat()),
            finished_at=data.get("finished_at"),
            final_proof=data.get("final_proof", ""),
            budget=budget,
            repair_history=data.get("repair_history", []),
            error=data.get("error"),
            recoverable=bool(data.get("recoverable", True)),
            restarts_used=int(data.get("restarts_used") or 0),
        )


# ============================================================================
# Evidence-gated agent-backed node executor
# ============================================================================
# The executor must distinguish "the model described the work" from "the model
# performed the work". A coder node that answers with a plan but never touches
# a tool, a file, or a command is NOT a completed stage.

#: the three kinds of evidence a stage can produce
EVIDENCE_CHANGE = "change"        # files/code created or modified
EVIDENCE_EXECUTION = "execution"  # commands/tests actually run
EVIDENCE_ANALYSIS = "analysis"    # substantive written finding

#: minimum characters for a written finding to count as analysis evidence
MIN_ANALYSIS_CHARS = 120
#: shorter, but still concrete — used for test verdicts / observations
MIN_FINDING_CHARS = 40

#: roles whose job is to change the world (files/code/tests), not just analyze
CHANGE_ROLES = {
    "coder", "developer", "implementer", "implementation", "engineer",
    "integrator", "builder", "fixer", "deployer", "operator", "patcher",
    "migrator", "installer",
}

#: roles whose job is to observe, judge or analyse — a written finding IS the
#: deliverable, and demanding a file change from them (the old rule) punished
#: a verifier for correctly reporting "tests failed because X".
OBSERVATION_ROLES = {
    "verifier", "tester", "reviewer", "auditor", "inspector", "analyst",
    "researcher", "architect", "spec", "specifier", "planner", "critic",
    "observer", "qa", "validator", "monitor",
}

#: kept for backwards compatibility with older callers/injected executors
ACTION_ROLES = CHANGE_ROLES | OBSERVATION_ROLES | {"specialist"}

#: goal verbs that mean "produce/change an artifact"
CHANGE_GOAL_VERBS = (
    "implement", "build", "write", "create", "fix", "repair", "refactor",
    "deploy", "generate", "develop", "integrate", "patch", "migrate",
    "install", "scaffold", "add a", "modify", "update the", "apply",
)

#: goal verbs that make a stage an *analysis* stage (a report is the product)
ANALYSIS_GOAL_VERBS = (
    "analyz", "analyse", "review", "research", "investigate", "design",
    "summarize", "summarise", "compare", "audit", "inspect", "evaluate",
    "assess", "document", "explain", "plan", "draft", "report", "describe",
    "diagnose", "check whether", "verify", "validate", "critique",
)

#: goal verbs that ask for something to be *executed* (tests, builds, commands)
EXEC_GOAL_VERBS = (
    "run ", "run the", "execute", "pytest", "run tests", "test the",
    "benchmark", "compile", "build the", "start the", "launch", "smoke test",
)

#: legacy alias: any verb that implies performing (not describing) work
ACTION_GOAL_VERBS = CHANGE_GOAL_VERBS + EXEC_GOAL_VERBS + ("test",)

# ---- tool taxonomy ------------------------------------------------------------
# The old gate counted *any* "action tool" as proof of work, so an agent could
# satisfy a coding stage by writing a memory entry or posting a Slack message.
# Goal-completion tools and supporting tools are now separated.

#: tools that produce or mutate the deliverable (goal-completion evidence)
GOAL_EVIDENCE_TOOLS = {
    "file_write", "file_edit", "file_delete", "file_move", "file_copy",
    "swe_develop", "git_apply_patch", "git_commit", "patch_apply",
    "sandbox_run", "shell_execute", "backend_execute",
    "mission_start", "mission_resume", "rollback_checkpoint", "rollback_restore",
}
#: tools that literally execute commands/tests (strongest evidence)
EXEC_TOOLS = {"sandbox_run", "shell_execute", "backend_execute"}
#: tool families that perform real (domain-specific) actions
GOAL_TOOL_PREFIXES = ("pentest_", "browser_", "screen_", "sast_", "dast_", "custom_")
#: auxiliary actions: useful, but they never prove the goal was accomplished
SUPPORTING_TOOLS = {
    "memory_add", "memory2_remember", "memory_search", "embeddings_add",
    "embeddings_ingest", "embeddings_search", "slack_notify",
    "jira_create_issue", "linear_create_issue",
    "github_integration_pr_comment", "github_integration_pr_create",
    "skill_harvest", "skill_use", "subagent_spawn", "delegate_tasks",
    "fleet_distribute_task", "notion_create_page", "email_send_draft",
}
#: legacy alias (kept so third-party executors importing it keep working)
ACTION_TOOLS = GOAL_EVIDENCE_TOOLS | SUPPORTING_TOOLS
ACTION_TOOL_PREFIXES = GOAL_TOOL_PREFIXES

#: directories never counted as workspace evidence
_SCAN_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", "target", ".next", ".cache",
    "data", ".tox", "site-packages",
}
_SCAN_MAX_FILES = 6000


def _is_goal_tool(name: str) -> bool:
    """Does this tool produce/mutate the deliverable (goal-completion proof)?"""
    n = str(name or "")
    return n in GOAL_EVIDENCE_TOOLS or any(n.startswith(p) for p in GOAL_TOOL_PREFIXES)


def _is_supporting_tool(name: str) -> bool:
    """Auxiliary action: recorded, but never accepted as proof of the goal."""
    n = str(name or "")
    if n in SUPPORTING_TOOLS:
        return True
    # anything that is neither a goal tool nor a known read tool is supporting
    return not (_is_goal_tool(n) or n.endswith("_read") or n.endswith("_get")
                or n.endswith("_list") or n.endswith("_search"))


def _is_action_tool(name: str) -> bool:
    """Legacy predicate: goal-completion tools (kept for older callers)."""
    return _is_goal_tool(name)


def expected_output_type(node: Any) -> dict[str, Any]:
    """What kind of output does this stage owe us, and what satisfies it?

    The model is driven by the **expected output type**, not by the role name:
    a verifier/reviewer/analyst delivers a *finding*; a coder delivers a
    *change*; a test-runner delivers *execution*. Returns::

        {"primary": kind, "acceptable": [kinds], "min_chars": int, "why": str}
    """
    role = str(getattr(node, "role", "") or "").strip().lower()
    goal = str(getattr(node, "goal", "") or "").strip().lower()

    is_observation_role = role in OBSERVATION_ROLES
    is_change_role = role in CHANGE_ROLES
    has_change_verb = any(v in goal for v in CHANGE_GOAL_VERBS)
    has_analysis_verb = any(v in goal for v in ANALYSIS_GOAL_VERBS)
    has_exec_verb = any(v in goal for v in EXEC_GOAL_VERBS)

    # 1. Observation roles (verifier/tester/reviewer/analyst/…) deliver a
    #    finding. Only an explicit change verb ("fix", "implement", …) turns
    #    them into change stages — the old rule demanded a file change from
    #    every verifier and punished a correct "tests failed because X".
    if is_observation_role:
        if has_change_verb:
            return {
                "primary": EVIDENCE_CHANGE,
                "acceptable": [EVIDENCE_CHANGE, EVIDENCE_EXECUTION],
                "min_chars": MIN_ANALYSIS_CHARS,
                "why": f"role '{role}' was asked to change something",
            }
        if has_exec_verb:
            return {
                "primary": EVIDENCE_EXECUTION,
                "acceptable": [EVIDENCE_EXECUTION, EVIDENCE_ANALYSIS],
                "min_chars": MIN_FINDING_CHARS,
                "why": f"role '{role}' must run the checks it reports on",
            }
        return {
            "primary": EVIDENCE_ANALYSIS,
            "acceptable": [EVIDENCE_ANALYSIS, EVIDENCE_EXECUTION, EVIDENCE_CHANGE],
            "min_chars": MIN_ANALYSIS_CHARS,
            "why": f"stage delivers a finding ({role})",
        }

    # 2. everyone else: the goal text decides
    if has_change_verb:
        return {
            "primary": EVIDENCE_CHANGE,
            "acceptable": [EVIDENCE_CHANGE, EVIDENCE_EXECUTION],
            "min_chars": MIN_ANALYSIS_CHARS,
            "why": f"goal contains an action verb ({role or 'specialist'})",
        }
    if has_exec_verb:
        return {
            "primary": EVIDENCE_EXECUTION,
            "acceptable": [EVIDENCE_EXECUTION, EVIDENCE_ANALYSIS],
            "min_chars": MIN_FINDING_CHARS,
            "why": f"goal asks for execution ({role or 'specialist'})",
        }
    if has_analysis_verb:
        return {
            "primary": EVIDENCE_ANALYSIS,
            "acceptable": [EVIDENCE_ANALYSIS, EVIDENCE_EXECUTION, EVIDENCE_CHANGE],
            "min_chars": MIN_ANALYSIS_CHARS,
            "why": f"goal asks for analysis ({role or 'specialist'})",
        }
    if is_change_role:
        return {
            "primary": EVIDENCE_CHANGE,
            "acceptable": [EVIDENCE_CHANGE, EVIDENCE_EXECUTION],
            "min_chars": MIN_ANALYSIS_CHARS,
            "why": f"role '{role}' changes the world",
        }
    # unknown/specialist stages default to "produce something" — the historical
    # behaviour, which keeps generic subgoals honest.
    return {
        "primary": EVIDENCE_CHANGE,
        "acceptable": [EVIDENCE_CHANGE, EVIDENCE_EXECUTION, EVIDENCE_ANALYSIS],
        "min_chars": MIN_ANALYSIS_CHARS,
        "why": "generic stage: any concrete output counts",
    }


def _node_requires_action(node: Any) -> bool:
    """Does this node owe us performed work (change/execution), not just prose?

    Kept for backwards compatibility; ``expected_output_type`` is the
    authoritative model used by the executor.
    """
    return expected_output_type(node)["primary"] in (EVIDENCE_CHANGE, EVIDENCE_EXECUTION)


#: markers that make a short written answer a real *finding* (test verdicts …)
_FINDING_MARKERS = (
    "passed", "failed", "failure", "error", "traceback", "exit code",
    "assert", "test result", "tests:", "ok", "broken", "missing", "blocked",
    "vulnerability", "warning", "regression", "coverage", "verified",
)


def _has_findings(text: str, min_chars: int = MIN_FINDING_CHARS) -> bool:
    """Does this text carry a concrete finding (not just filler)?"""
    body = str(text or "").strip()
    if len(body) < min_chars:
        return False
    low = body.lower()
    return any(m in low for m in _FINDING_MARKERS)


def classify_evidence(
    *,
    tool_calls: list[str],
    files_changed: list[str],
    text: str,
    expectation: dict[str, Any],
) -> dict[str, Any]:
    """Turn raw signals into an evidence verdict for one stage.

    Returns ``{"kinds": set, "satisfied": bool, "missing": [..], ...}`` where
    ``kinds`` ⊆ {change, execution, analysis} and supporting actions are
    reported separately so a repair round can name them.
    """
    tool_calls = list(tool_calls or [])
    goal_tools = sorted({t for t in tool_calls if _is_goal_tool(t)})
    supporting = sorted({t for t in tool_calls if _is_supporting_tool(t)})
    exec_tools = sorted({t for t in tool_calls if t in EXEC_TOOLS})
    files_changed = list(files_changed or [])

    change_tools = sorted(set(goal_tools) - set(exec_tools))
    kinds: set[str] = set()
    if change_tools or files_changed:
        kinds.add(EVIDENCE_CHANGE)
    if exec_tools:
        kinds.add(EVIDENCE_EXECUTION)
    min_chars = int(expectation.get("min_chars") or MIN_ANALYSIS_CHARS)
    if len(str(text or "").strip()) >= min_chars or (
        expectation.get("primary") == EVIDENCE_ANALYSIS and _has_findings(text, MIN_FINDING_CHARS)
    ):
        kinds.add(EVIDENCE_ANALYSIS)

    acceptable = set(expectation.get("acceptable") or [expectation.get("primary")])
    satisfied = bool(kinds & acceptable)
    missing = [k for k in (expectation.get("acceptable") or []) if k not in kinds]
    return {
        "kinds": kinds,
        "satisfied": satisfied,
        "missing": missing,
        "goal_tools": goal_tools,
        "supporting_tools": supporting,
        "exec_tools": exec_tools,
        "files_changed": files_changed,
        "primary": expectation.get("primary"),
        "why": expectation.get("why", ""),
    }


def _scan_changed_files(since_ts: float, roots: Optional[list[Path]] = None) -> list[str]:
    """Legacy timestamp scan: files under ``roots`` modified since ``since_ts``.

    Kept for standalone executors and offline callers (and as the seam tests
    patch). **Mission runs do not rely on it**: they use
    :class:`core.mission_files.MissionFileScope`, whose baseline + snapshot
    diff is scoped to the mission's own directories and is immune to another
    process touching a file inside a shared root.
    """
    if roots is None:
        roots = []
        try:
            roots.append(Path(workspace.root))
        except Exception:
            pass
        cwd = Path.cwd()
        if cwd not in roots:
            roots.append(cwd)
    changed: list[str] = []
    seen = 0
    try:
        for root in roots:
            if not root or not Path(root).exists():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP_DIRS]
                for fname in filenames:
                    seen += 1
                    if seen > _SCAN_MAX_FILES:
                        return changed[:50]
                    fp = Path(dirpath) / fname
                    try:
                        if fp.stat().st_mtime >= since_ts:
                            changed.append(str(fp))
                            if len(changed) >= 50:
                                return changed
                    except OSError:
                        continue
    except Exception as exc:
        record_issue("mission", "scan_changed_files", exc, fallback="skipped file evidence scan")
    return changed


def build_node_prompt(node: Any, parent_ctx: Optional[dict[str, Any]] = None,
                      workspace_dir: Optional[str] = None) -> str:
    """Compose the prompt for one DAG node, injecting upstream (parent) results.

    Previously the executor only passed the node's own goal (+ repair hints), so
    the 'coder' never received the researcher's/architect's actual output and
    the DAG was a chain in name only. Upstream outputs and artifacts are now a
    first-class section of the child prompt.
    """
    role = str(getattr(node, "role", "specialist") or "specialist")
    parts = [
        f"You are the '{role}' stage of a mission. Accomplish this goal using "
        f"the available tools and report concrete results:\n{getattr(node, 'goal', '')}"
    ]

    parent_ctx = parent_ctx or {}
    if parent_ctx:
        blocks = []
        for dep_id, ctx in parent_ctx.items():
            dep_role = ""
            outputs = ctx.get("outputs") if isinstance(ctx, dict) else None
            if isinstance(outputs, dict):
                dep_role = str(outputs.get("stage") or outputs.get("role") or "")
                text = str(outputs.get("output") or outputs.get("result") or "")
            else:
                text = str(outputs or "")
            if not text and isinstance(ctx, dict):
                text = json.dumps(ctx, default=str)[:1500]
            block = f"[upstream node '{dep_id}'" + (f" ({dep_role})" if dep_role else "") + "]"
            if text.strip():
                block += "\n" + text.strip()[:4000]
            arts = ctx.get("artifacts") if isinstance(ctx, dict) else None
            if arts:
                block += "\nArtifacts produced: " + ", ".join(str(a) for a in arts[:10])
            if block != f"[upstream node '{dep_id}']":
                blocks.append(block)
        if blocks:
            parts.append(
                "## Upstream results (already completed by previous stages — build on "
                "these, do NOT redo or contradict them)\n" + "\n\n".join(blocks)
            )

    if workspace_dir:
        parts.append(
            "## Mission workspace\n"
            f"Write every deliverable for this mission inside: {workspace_dir}\n"
            "Files created anywhere else may not be collected as mission evidence."
        )

    hints = (getattr(node, "inputs", None) or {}).get("repair_hints")
    if hints:
        parts.append(
            "## Problems found by the previous verification round (fix these)\n- "
            + "\n- ".join(str(h) for h in hints)
        )
    return "\n\n".join(parts)


def _chat_compat(
    agent: Any,
    prompt: str,
    *,
    on_event: Optional[Callable[..., None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    steer_source: Optional[Callable[[], list[str]]] = None,
) -> dict[str, Any]:
    """Call ``agent.chat`` with only the kwargs that agent actually supports."""
    kwargs: dict[str, Any] = {}
    try:
        params = inspect.signature(agent.chat).parameters
    except (TypeError, ValueError):
        params = {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        params = {name: None for name in ("on_event", "should_cancel", "steer_source")}
    if on_event is not None and "on_event" in params:
        kwargs["on_event"] = on_event
    if should_cancel is not None and "should_cancel" in params:
        kwargs["should_cancel"] = should_cancel
    if steer_source is not None and "steer_source" in params:
        kwargs["steer_source"] = steer_source
    try:
        return agent.chat(prompt, **kwargs) if kwargs else agent.chat(prompt)
    except TypeError:
        return agent.chat(prompt)


def make_agent_backed_executor(
    agent: Any = None,
    *,
    on_event: Optional[Callable[..., None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    steer_source: Optional[Callable[[], list[str]]] = None,
    model: Optional[str] = None,
    scope: Optional[MissionFileScope] = None,
    workspace_dir: Optional[str] = None,
) -> Callable[[Any, dict[str, Any]], dict[str, Any]]:
    """Build an executor that actually runs each DAG node goal via an agent.

    * ``agent`` — reuse a live agent (session memory/model/profile preserved
      across stages). When omitted a fresh agent is created per node.
    * ``on_event`` / ``should_cancel`` / ``steer_source`` — forwarded into every
      node's ReAct loop so SSE streaming, cooperative cancellation and mid-run
      steering work inside missions exactly like they do in plain chat.
    * ``scope`` — the mission's :class:`~core.mission_files.MissionFileScope`.
      File evidence then comes from a precise per-mission baseline diff inside
      the mission's own roots, so concurrent jobs cannot leak evidence into
      each other (without it the executor falls back to the legacy timestamp
      scan).
    * ``workspace_dir`` — the mission workspace advertised to the model so it
      writes deliverables where the mission can see them.

    Never fabricates success:
    * no usable model/key/Ollama backend → the node is blocked, not completed;
    * a stage must produce the **expected kind** of output (change / execution /
      analysis) — a prose description of a coding task fails with
      ``no_evidence_of_work``, and supporting actions (memory_add,
      slack_notify, …) never satisfy the gate.
    """
    from .config import config as _config

    def _emit(event_type: str, data: Optional[dict[str, Any]] = None) -> None:
        if on_event is not None:
            try:
                on_event(event_type, data or {})
            except Exception:
                pass

    def executor(node: Any, parent_ctx: dict[str, Any]) -> dict[str, Any]:
        stage = str(getattr(node, "role", "?") or "?")
        node_started = time.time()
        _emit("node_started", {"stage": stage, "goal": str(getattr(node, "goal", ""))[:200]})
        try:
            if should_cancel is not None and should_cancel():
                return {
                    "success": False,
                    "output": "",
                    "error": "cancelled",
                    "evidence": [{"stage": stage, "status": "cancelled"}],
                }
            if agent is not None:
                node_agent = agent
            else:
                from .agent import HermusAgent

                node_agent = HermusAgent(
                    model=model or getattr(_config, "model", None) or "ollama/llama3.1:8b",
                    session_id=f"mission_{os.urandom(4).hex()}",
                    mode="agent",
                )

            # Per-node file baseline: precise diff, scoped to this mission.
            node_snapshot = scope.snapshot() if scope is not None else None
            prompt = build_node_prompt(
                node, parent_ctx, workspace_dir=workspace_dir,
            )
            res = _chat_compat(
                node_agent, prompt,
                on_event=on_event, should_cancel=should_cancel,
                steer_source=steer_source,
            )
            text = str(res.get("response") or "")
            provider = str(getattr(getattr(node_agent, "llm", None), "provider", "") or "")

            if res.get("status") == "waiting_for_approval" or res.get("waiting_for_approval"):
                approval_request = res.get("waiting_for_approval") or {}
                req_id = approval_request.get("id") if isinstance(approval_request, dict) else None
                reason = "Approval required before continuing"
                if req_id:
                    reason += f" ({req_id})"
                _emit("node_finished", {"stage": stage, "status": "blocked",
                                        "reason": reason,
                                        "approval_request": approval_request})
                return {
                    "success": False,
                    "blocked": True,
                    "blocker_reason": reason,
                    "blocker_instructions": "Approve or deny the pending request in the Safety tab, then resume the mission.",
                    "approval_request": approval_request,
                    "output": text,
                    "error": "approval_required",
                    "evidence": [{"stage": stage, "status": "blocked", "reason": reason,
                                  "approval_request": approval_request}],
                }

            # ---- honest no-backend detection (never fake success) ----
            is_mock = (
                provider == "mock"
                or "Fallback mock" in text
                or text.strip().startswith("⚠️")
            )
            if is_mock:
                reason = ("No model backend reachable (no Ollama and no API key). "
                          "Configure a model before running missions.")
                _emit("node_finished", {"stage": stage, "status": "blocked"})
                return {
                    "success": False,
                    "blocked": True,
                    "blocker_reason": reason,
                    "output": text,
                    "error": "no_model_backend",
                    "evidence": [{"stage": stage, "status": "blocked", "reason": reason}],
                }

            # ---- evidence collection: did the agent deliver what it owed? ----
            tool_calls = list(res.get("tool_calls") or [])
            if scope is not None and node_snapshot is not None:
                # precise, mission-scoped diff (baseline → now)
                files_changed = scope.changed_since(node_snapshot)
            else:
                # legacy timestamp scan (standalone executors / offline callers)
                files_changed = _scan_changed_files(node_started)
            # evidence the agent itself reported (tool_results carry per-tool outcomes)
            tool_results = res.get("tool_results") or []

            expectation = expected_output_type(node)
            verdict = classify_evidence(
                tool_calls=tool_calls,
                files_changed=files_changed,
                text=text,
                expectation=expectation,
            )
            performed_work = bool(verdict["kinds"] & {EVIDENCE_CHANGE, EVIDENCE_EXECUTION})
            evidence = [{
                "stage": stage,
                "status": "executed" if verdict["satisfied"] else "needs_evidence",
                "model": provider,
                "tools_used": len(tool_results),
                "expected_output": expectation["primary"],
                "acceptable_evidence": list(expectation["acceptable"]),
                "evidence_found": sorted(verdict["kinds"]),
                "evidence_missing": list(verdict["missing"]),
                "why": expectation["why"],
                # legacy keys (older dashboards/tests read these)
                "action_tools": verdict["goal_tools"],
                "supporting_actions": verdict["supporting_tools"],
                "commands_executed": verdict["exec_tools"],
                "files_changed": files_changed[:20],
                "performed_work": performed_work,
            }]

            if not verdict["satisfied"]:
                empty_analysis = (
                    expectation["primary"] == EVIDENCE_ANALYSIS
                    and not (verdict["kinds"] & {EVIDENCE_CHANGE, EVIDENCE_EXECUTION})
                )
                reason = "empty_analysis" if empty_analysis else "no_evidence_of_work"
                _emit("node_finished", {"stage": stage, "status": "needs_evidence",
                                        "reason": reason,
                                        "expected": expectation["primary"]})
                if empty_analysis:
                    instructions = (
                        f"This stage delivers a finding ('{stage}'): produce a "
                        "substantive result (review findings, test verdicts, research "
                        f"summary) of at least {expectation['min_chars']} characters "
                        "that states what you observed. The answer was empty or trivial."
                    )
                else:
                    supporting = verdict["supporting_tools"]
                    supporting_note = (
                        f" Only supporting actions were recorded ({', '.join(supporting)}); "
                        "those do not prove the goal was accomplished."
                        if supporting else ""
                    )
                    instructions = (
                        f"This stage must produce '{expectation['primary']}' evidence "
                        f"({expectation['why']}), but none was found. Re-run the stage "
                        "and actually use the tools to accomplish the goal — "
                        + ("write/modify the files" if expectation["primary"] == EVIDENCE_CHANGE
                           else "run the commands/tests")
                        + "; do not merely describe how to do it."
                        + supporting_note
                    )
                return {
                    "success": False,
                    "output": text,
                    "error": reason,
                    "evidence": evidence,
                    "expected_output": expectation["primary"],
                    "instructions": instructions,
                }

            _emit("node_finished", {
                "stage": stage, "status": "executed",
                "expected": expectation["primary"],
                "evidence": sorted(verdict["kinds"]),
                "tools": len(tool_results), "files_changed": len(files_changed),
                "ms": int((time.time() - node_started) * 1000),
            })
            return {
                "success": True,
                "output": text,
                "tool_calls": len(tool_calls),
                "tool_results": len(tool_results),
                "artifacts": files_changed[:20],
                "evidence": evidence,
            }
        except Exception as exc:  # never fabricate success on error
            record_issue("mission", "node_executor", exc,
                         mission_id=None, retryable=True,
                         fallback=f"node '{stage}' reported failed")
            _emit("node_finished", {"stage": stage, "status": "failed",
                                    "error": str(exc)[:200]})
            return {
                "success": False,
                "output": "",
                "error": f"{type(exc).__name__}: {exc}",
                "evidence": [{"stage": stage, "status": "failed",
                              "error": str(exc)[:300]}],
            }

    return executor


class MissionEngine:
    """Unified Mission Lifecycle & DAG Controller with Autonomous Repair Loop.

    ``start_mission`` accepts an optional bound ``agent`` plus streaming /
    cancellation / steering hooks; all of them are threaded down into every
    DAG node's ReAct loop (see :func:`make_agent_backed_executor`), so a
    mission observed over SSE behaves exactly like a plain chat turn.
    """

    def __init__(
        self,
        executor: Optional[Callable[[Any, dict[str, Any]], dict[str, Any]]] = None,
        storage_dir: Optional[Path] = None,
    ):
        # NOTE: a real agent-backed executor is wired lazily on first use
        # (see ``_raw_executor``). The old inline ``_default_node_executor``
        # merely *claimed* every stage succeeded without doing any work, which
        # let production missions "pass" without executing anything. It is now
        # only used as an explicit, clearly-labelled simulation for offline
        # tests that pass ``executor=`` themselves.
        self._injected_executor = executor
        self._real_executor: Optional[Callable[[Any, dict[str, Any]], dict[str, Any]]] = None
        self.storage_dir = storage_dir or (workspace.root / "missions")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        # per-run overrides (bound agent / events / control hooks). Missions run
        # to completion inside one thread, so thread-local state is safe even
        # though the engine singleton is shared across queue workers.
        self._tls = threading.local()

    @property
    def _raw_executor(self) -> Callable[[Any, dict[str, Any]], dict[str, Any]]:
        if self._injected_executor is not None:
            return self._injected_executor
        if self._real_executor is None:
            self._real_executor = self._build_real_executor()
        return self._real_executor

    @_raw_executor.setter
    def _raw_executor(self, value: Callable[[Any, dict[str, Any]], dict[str, Any]]) -> None:
        # Keep the long-standing monkeypatch/injection seam working: assigning
        # an explicit executor overrides the lazy real agent-backed executor.
        self._injected_executor = value
        self._real_executor = None

    @staticmethod
    def _simulated_node_executor(node: DAGNode, parent_ctx: dict[str, Any]) -> dict[str, Any]:
        """Offline simulation — does NO real work. Only for tests."""
        return {
            "success": True,
            "simulated": True,
            "output": f"Executed stage '{node.role}': {node.goal}",
            "evidence": [{"stage": node.role, "goal": node.goal, "status": "completed",
                          "simulated": True}],
        }

    def _build_real_executor(self) -> Callable[[Any, dict[str, Any]], dict[str, Any]]:
        """Lazily build the default agent-backed executor (fresh agent per node)."""
        return make_agent_backed_executor()

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
        except Exception as exc:
            record_issue("mission", "read_artifact", exc, retryable=False,
                         fallback=f"skipped unreadable artifact {path}")
            return ""

    def _call_executor(self, node: DAGNode, parent_ctx: dict[str, Any]) -> dict[str, Any]:
        executor = getattr(self._tls, "executor", None) or self._raw_executor
        try:
            sig = inspect.signature(executor)
            params = list(sig.parameters.keys())
            if params and params[0] in ("goal", "task", "prompt", "subgoal"):
                return executor(node.goal, parent_ctx)
        except Exception:
            pass
        return executor(node, parent_ctx)

    def _save_mission(self, report: MissionReport) -> None:
        """Persist mission state atomically (tmp → fsync → rename).

        Mission state is durable background state: a plain ``write_text``
        truncates the document first, so a crash between truncate and flush (or
        a concurrent reader) can observe invalid JSON and lose the mission
        entirely. Writers are also serialised with an advisory lock so two
        queue workers cannot interleave partial reads/writes.
        """
        p = self.storage_dir / f"{report.mission_id}.json"
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            with file_lock(p):
                atomic_write_json(p, report.to_dict(), indent=2)
        except Exception as exc:
            record_issue("mission", "save_report", exc,
                         mission_id=report.mission_id, retryable=True,
                         fallback="mission report not persisted (run continues)")

    @staticmethod
    def _bind_control_hooks(
        executor: Callable[[Any, dict[str, Any]], dict[str, Any]],
        *,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Callable[[Any, dict[str, Any]], dict[str, Any]]:
        """Layer cooperative cancellation on top of an injected executor."""
        if should_cancel is None:
            return executor

        def wrapped(node: Any, parent_ctx: dict[str, Any]) -> dict[str, Any]:
            try:
                if should_cancel():
                    stage = str(getattr(node, "role", "?") or "?")
                    return {"success": False, "output": "", "error": "cancelled",
                            "evidence": [{"stage": stage, "status": "cancelled"}]}
            except Exception:
                pass
            return executor(node, parent_ctx)

        return wrapped

    def _mission_file_scope(self, mission_id: str) -> MissionFileScope:
        """Open (and cache per thread) the mission's isolated file scope."""
        scope = getattr(self._tls, "scope", None)
        if scope is None or scope.mission_id != mission_id:
            scope = MissionFileScope.open(mission_id)
            self._tls.scope = scope
        return scope

    def _mission_workspace(self, mission_id: str) -> Path:
        try:
            return workspace.mission_workspace(mission_id)
        except Exception as exc:
            record_issue("mission", "workspace", exc, mission_id=mission_id,
                         retryable=False, fallback="mission workspace unavailable")
            return Path.cwd()

    def _preflight_blocked_report(self, mission_id: str, goal: str, domain: str, preflight_data: dict[str, Any], *, persist: bool) -> MissionReport:
        status = str(preflight_data.get("status") or "BLOCKED_BY_RED_LINE")
        suggested_prompts = list(preflight_data.get("suggested_approval_prompts") or [])
        create_action = None
        if suggested_prompts and status in {"NEEDS_APPROVAL", "MISSING_CAPABILITY"}:
            create_action = {
                "label": "Create approval prompts for this blocked mission",
                "method": "POST",
                "endpoint": f"/missions/{mission_id}/preflight/approvals",
                "fallback_endpoint": "/safety/preflight/approvals",
                "payload": {"goal": goal},
                "cli": f"hermus safety preflight {goal!r} --create-approval-prompts",
            }
        report = MissionReport(
            mission_id=mission_id,
            goal=goal,
            domain=domain,
            state=MissionState.BLOCKED.value,
            progress_pct=0,
            preflight=preflight_data,
            create_prompts_action=create_action,
            blocker_reason=f"Mission pre-flight status: {status}",
            blocker_instructions=(
                "Resolve pre-flight blockers before mission execution. "
                "Use `hermus safety preflight <goal>` for details. "
                + ("This blocker was recorded as an explicit planning-mode mission. " if persist else "Mission execution was refused before creation. ")
                + "NEEDS_APPROVAL/MISSING_CAPABILITY may be recorded with `--allow-planning-blocked`; red-line or emergency-stop blockers cannot be overridden."
            ),
            final_proof=f"MISSION BLOCKED BY PRE-FLIGHT: {status}",
            evidence=[{"stage": "preflight", "status": status, "persisted": persist}],
            budget=MissionBudget(initial_steps=0),
            recoverable=status in {"NEEDS_APPROVAL", "MISSING_CAPABILITY"},
        )
        if persist:
            self._save_mission(report)
        try:
            _emit = getattr(self._tls, "on_event", None)
            if _emit:
                _emit("mission_finished", {"mission_id": mission_id, "state": report.state, "preflight": preflight_data})
        except Exception:
            pass
        return report

    def get_mission(self, mission_id: str) -> Optional[MissionReport]:
        p = self.storage_dir / f"{mission_id}.json"
        if not p.exists():
            return None
        try:
            return MissionReport.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def load_mission(self, mission_id: str) -> MissionReport:
        """Load a persisted mission from a (potentially fresh) engine/process.

        This is the recovery entry point for restart/resume: a new engine (after a
        worker was killed) reads the durable report and hands it back so the caller
        can inspect it, then ``resume_mission`` continues it. Raises ``ValueError``
        when the mission does not exist (callers should surface that as
        ``mission_not_found``, not silently return a fabricated report).
        """
        report = self.get_mission(mission_id)
        if report is None:
            raise ValueError(f"Mission {mission_id} not found in {self.storage_dir}")
        return report

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
        budget_steps: Optional[int] = None,
        max_repairs: Optional[int] = None,
        executor: Optional[Callable[[Any, dict[str, Any]], dict[str, Any]]] = None,
        agent: Any = None,
        on_event: Optional[Callable[..., None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        steer_source: Optional[Callable[[], list[str]]] = None,
        preflight: bool = True,
        allow_preflight_planning: bool = False,
    ) -> MissionReport:
        """Plan and run a mission to completion.

        ``agent`` (or a custom ``executor``) binds this run to a specific
        execution surface — e.g. the runtime passes the user's live agent so
        mission stages share its session, model and profile. ``on_event`` /
        ``should_cancel`` / ``steer_source`` stream and control the mission the
        same way a plain chat turn is streamed and controlled.

        Failure contract: an unexpected exception inside the lifecycle is
        **recorded on the report** (``state='failed'``, ``error={type, stage,
        message, traceback}``) and returned — it is never swallowed, and it is
        never converted into a successful/chat-shaped answer. The mission
        remains restartable with ``resume_mission(..., restart_failed=True)``.
        """
        from .config import config as _cfg

        mid = f"msn_{int(time.time())}_{os.urandom(2).hex()}"
        detected_domain = domain or verifier_registry.auto_detect_domain(goal)
        preflight_data = None
        # The safety pre-flight guards the *agent-driven* autonomy path. When a
        # caller injects its own executor (offline tests, simulations, custom
        # runtimes that own their execution side effects), the caller has
        # already decided how the work will run, so pre-flight should not block
        # it with an approval prompt. Real gateway/CLI/dashboard runs that go
        # through `runtime.execute` or `make_agent_backed_executor` keep the
        # gate enabled (callers can still opt out explicitly).
        if preflight and executor is None and self._injected_executor is None:
            try:
                from .autonomy_preflight import preflight_goal

                preflight_report = preflight_goal(goal)
                preflight_data = preflight_report.to_dict()
                if preflight_report.status in {"EMERGENCY_STOP_ACTIVE", "BLOCKED_BY_RED_LINE"}:
                    return self._preflight_blocked_report(mid, goal, detected_domain, preflight_data, persist=False)
                if preflight_report.status in {"NEEDS_APPROVAL", "MISSING_CAPABILITY"}:
                    return self._preflight_blocked_report(
                        mid, goal, detected_domain, preflight_data,
                        persist=bool(allow_preflight_planning),
                    )
            except Exception as exc:
                preflight_data = {
                    "status": "EMERGENCY_STOP_ACTIVE",
                    "can_start": False,
                    "error": f"pre-flight failed closed: {exc}",
                    "generated_at": datetime.now().isoformat(),
                }
                return self._preflight_blocked_report(mid, goal, detected_domain, preflight_data, persist=False)

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
        # Mission-isolated workspace: the only file root that is always
        # attributable to this mission (see core.mission_files).
        try:
            workspace_dir = self._mission_workspace(mid)
        except Exception:
            workspace_dir = Path.cwd()
        if budget_steps is None:
            budget_steps = int(getattr(_cfg, "mission_budget_steps", DEFAULT_MISSION_BUDGET)
                               or DEFAULT_MISSION_BUDGET)

        report = MissionReport(
            mission_id=mid,
            goal=goal,
            domain=detected_domain,
            state=MissionState.PLANNING.value,
            requirements=req_objs,
            dag_state=dag.to_dict(),
            subgoals=[SubGoal(id=nid, role=n.role, goal=n.goal, status=n.status) for nid, n in dag.nodes.items()],
            checkpoint_id=cp.id,
            budget=MissionBudget(
                initial_steps=budget_steps,
                max_repairs=max_repairs if max_repairs is not None else 3,
            ),
            preflight=preflight_data,
        )
        self._save_mission(report)

        # per-run executor override (bound agent / hooks), scoped to this thread
        scope = self._mission_file_scope(mid)
        if executor is not None:
            self._tls.executor = executor
        elif self._injected_executor is not None:
            # An explicitly injected executor (tests, simulations, custom
            # runtimes) must not be replaced by the default agent-backed one
            # just because the caller also passed streaming/control hooks —
            # the hooks are layered on top of it instead.
            self._tls.executor = self._bind_control_hooks(
                self._injected_executor, should_cancel=should_cancel
            )
        elif agent is not None or on_event or should_cancel or steer_source:
            self._tls.executor = make_agent_backed_executor(
                agent=agent,
                on_event=on_event,
                should_cancel=should_cancel,
                steer_source=steer_source,
                scope=scope,
                workspace_dir=str(workspace_dir),
            )
        else:
            self._tls.executor = None
        try:
            try:
                return self._run_autonomous_loop(
                    report, dag,
                    on_event=on_event, should_cancel=should_cancel,
                )
            except Exception as exc:
                # ---- crash → recorded failure, never a silent downgrade ----
                crash_stage = str(report.state or MissionState.EXECUTING.value)
                report.state = MissionState.FAILED.value
                report.error = {
                    "type": type(exc).__name__,
                    "message": str(exc)[:1000],
                    "stage": crash_stage,
                    "traceback": traceback.format_exc(limit=8)[-2000:],
                    "recoverable": True,
                }
                report.recoverable = True
                report.finished_at = datetime.now().isoformat()
                report.progress_pct = min(95, self._compute_progress(dag))
                report.final_proof = (
                    f"MISSION FAILED: {type(exc).__name__}: {str(exc)[:300]} "
                    f"(mission {mid} can be repaired and resumed)"
                )
                record_issue(
                    "mission", "lifecycle", exc, mission_id=mid, retryable=True,
                    fallback="mission recorded as FAILED with diagnostics; "
                             "resume with restart_failed=True",
                )
                self._save_mission(report)
                if on_event is not None:
                    try:
                        on_event("mission_error", {
                            "mission_id": mid,
                            "stage": report.error["stage"],
                            "error": report.error["message"],
                            "error_type": report.error["type"],
                            "recoverable": True,
                            "resumable": True,
                        })
                        on_event("mission_finished", {
                            "mission_id": mid, "state": report.state,
                            "progress_pct": report.progress_pct,
                            "failure": report.failure_summary(),
                        })
                    except Exception:
                        pass
                return report
        finally:
            self._tls.executor = None
            self._tls.scope = None

    def _run_autonomous_loop(
        self,
        report: MissionReport,
        dag: AgentDAG,
        *,
        on_event: Optional[Callable[..., None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> MissionReport:
        budget = report.budget
        mission_start_ts = datetime.fromisoformat(report.started_at).timestamp()
        prev_completed = -1
        # mission-isolated file scope (baseline taken in start_mission)
        scope = self._mission_file_scope(report.mission_id)

        def _emit(event_type: str, data: Optional[dict[str, Any]] = None) -> None:
            if on_event is not None:
                try:
                    on_event(event_type, {"mission_id": report.mission_id, **(data or {})})
                except Exception:
                    pass

        def _spend(phase: str, steps: int = 1) -> None:
            """Charge a phase; over-drawing borrows from the emergency reserve.

            Phase budgets are the *planning* hierarchy the mission reports on.
            The hard stop stays the global budget, so a phase that legitimately
            needs more (e.g. extra verification rounds) borrows from the
            emergency reserve and, when that is empty, records an overdraw
            instead of aborting an otherwise healthy mission.
            """
            steps = max(1, int(steps))
            if budget.remaining(phase) < steps:
                if not budget.borrow(phase, steps):
                    entry = budget.phase(phase)
                    entry["overdraw"] = int(entry.get("overdraw") or 0) + steps
            budget.consume(phase, steps)

        _emit("mission_started", {"goal": report.goal[:200], "domain": report.domain,
                                  "budget_steps": budget.total_steps(),
                                  "budget": budget.to_dict()})

        # requirements analysis + DAG construction already happened: charge the
        # planning phase once (it is not repeated on every round).
        _spend(PHASE_PLANNING, 1)

        while budget.consumed_steps < budget.total_steps():
            if should_cancel is not None and should_cancel():
                report.state = MissionState.CANCELLED.value
                report.finished_at = datetime.now().isoformat()
                report.final_proof = "Mission cancelled before completion."
                self._save_mission(report)
                _emit("mission_finished", {"state": report.state})
                return report
            report.state = MissionState.EXECUTING.value
            _emit("mission_state", {"state": report.state,
                                    "progress_pct": self._compute_progress(dag),
                                    "steps_left": budget.steps_left(),
                                    "budget": budget.to_dict()})
            self._save_mission(report)

            # A. EXECUTE DAG STAGES
            dag_round_res = dag.execute_dag(
                node_executor=self._call_executor,
                max_rounds=15,
            )
            _spend(PHASE_EXECUTION, max(1, int(dag_round_res.get("completed", 1) or 1)))
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
                budget.grant_extension(10)
            prev_completed = completed_now

            # Check for explicit external blockers
            blocked_nodes = [n for n in dag.nodes.values() if n.status == DAGNodeStatus.BLOCKED.value]
            if blocked_nodes:
                report.state = MissionState.BLOCKED.value
                blocked = blocked_nodes[0]
                blocked_output = blocked.outputs if isinstance(blocked.outputs, dict) else {}
                approval_request = blocked_output.get("approval_request")
                report.approval_request = approval_request if isinstance(approval_request, dict) else None
                report.blocker_reason = blocked.error or "External prerequisite or authorization required"
                if report.approval_request:
                    req_id = report.approval_request.get("id")
                    report.blocker_instructions = (
                        "Approve or deny the pending request in the Safety tab "
                        f"or with `hermus perms resolve {req_id} approve --retry`, then call "
                        f"`hermus mission resume {report.mission_id}`."
                    )
                else:
                    report.blocker_instructions = "Please resolve the blocker and call `hermus mission resume <mission_id>`"
                self._save_mission(report)
                record_issue("mission", "node_blocked", report.blocker_reason,
                             mission_id=report.mission_id, retryable=True,
                             fallback="mission paused as BLOCKED; resume after resolving")
                _emit("mission_finished", {"state": report.state,
                                           "blocker_reason": report.blocker_reason,
                                           "approval_request": report.approval_request})
                return report

            # B. OBSERVE & GATHER EVIDENCE
            report.state = MissionState.OBSERVING.value
            _emit("mission_state", {"state": report.state})
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

            # Scope the scan to THIS mission's workspace root so concurrent
            # missions cannot discover (and re-attribute) each other's files.
            # Artifacts produced by the node executor are written under the
            # mission's own project directory; a shared whole-workspace scan
            # used to let a later mission claim an earlier mission's outputs.
            # Scan the workspace for deliverables produced since this mission
            # started. Ownership is protected in ArtifactManager: a file that
            # already belongs to another mission is never reattributed, so
            # concurrent missions cannot claim each other's outputs.
            artifacts = artifact_manager.scan_workspace(
                mission_id=report.mission_id,
                since_timestamp=mission_start_ts,
            )
            # Concurrency guard: only files inside THIS mission's scope (its own
            # workspace + the project root, minus every other mission's
            # directory) can be claimed as deliverables, and only when the
            # per-mission baseline says they appeared/changed during this run.
            scoped_changed = set(scope.changed_since_baseline())
            report.artifacts = [
                a.path for a in artifacts
                if (not getattr(a, "mission_id", None) or a.mission_id == report.mission_id)
                and (str(getattr(a, "path", "")) in scoped_changed or scope.contains(str(getattr(a, "path", ""))))
            ]

            # C. VERIFY (STRUCTURAL + BEHAVIORAL + CRITIC)
            report.state = MissionState.VERIFYING.value
            _spend(PHASE_VERIFICATION, 1)
            _emit("mission_state", {"state": report.state,
                                    "budget": budget.to_dict()})
            try:
                v_res = verifier_registry.verify(
                    domain_or_auto=report.domain,
                    context={
                        "task": report.goal,
                        "output": combined_log,
                        "artifacts": report.artifacts,
                    },
                )
            except Exception as exc:
                record_issue("mission", "verifier", exc, mission_id=report.mission_id,
                             retryable=True, fallback="verification treated as failed")
                raise
            for ev in v_res.evidence:
                if ev not in report.evidence:
                    report.evidence.append(ev)
            report.confidence_score = v_res.score

            files_content = {
                Path(a).name: self._read_capped(Path(a))
                for a in report.artifacts
                if Path(a).suffix in (".py", ".js", ".ts", ".html", ".json") and Path(a).exists()
            }

            try:
                critic_res = critic_manager.run_full_review(
                    task=report.goal,
                    files_content=files_content,
                    execution_log=combined_log,
                    artifacts=report.artifacts,
                    requirements=[r.description for r in report.requirements],
                    verification_evidence=v_res.evidence,
                )
            except Exception as exc:
                record_issue("mission", "critic", exc, mission_id=report.mission_id,
                             retryable=True, fallback="critic panel unavailable; treated as not approved")
                critic_res = {"approved": False, "overall_score": 0, "verdict": "error",
                              "summary": f"critic panel failed: {exc}"[:200],
                              "repair_directives": [f"critic panel failed: {exc}"[:200]]}
            _emit("mission_verification", {
                "verified": bool(v_res.verified), "score": v_res.score,
                "structural": v_res.structural_score, "behavioral": v_res.behavioral_score,
                "critic_score": critic_res.get("overall_score"),
                "errors": v_res.errors[:5],
            })

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
                _emit("mission_finished", {
                    "state": report.state, "progress_pct": 100,
                    "confidence_score": report.confidence_score,
                    "artifacts": report.artifacts[:10],
                })
                return report

            # D. DIAGNOSE & REPAIR LOOP
            if budget.repairs_used < budget.max_repairs and not budget.exhausted(PHASE_REPAIR):
                budget.repairs_used += 1
                _spend(PHASE_REPAIR, 1)
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
                _emit("mission_repair", {"round": budget.repairs_used,
                                          "hints": [str(h)[:120] for h in repair_hints[:6]]})
                self._save_mission(report)
                continue
            else:
                break

        # ---- structured failure (never a silent downgrade to chat) ----------
        report.state = MissionState.FAILED.value
        report.finished_at = datetime.now().isoformat()
        # Never report 100% for a failed mission: DAG stages may all have run,
        # but the mission only "completes" when verification passes.
        report.progress_pct = min(95, self._compute_progress(dag))
        if budget.steps_left() <= 0:
            reason, stage = ("budget_exhausted", PHASE_EXECUTION)
        else:
            reason, stage = ("repairs_exhausted", PHASE_REPAIR)
        can_extend = (
            budget.extensions_used < budget.max_extensions
            or budget.emergency_extensions < budget.max_emergency_extensions
        )
        report.recoverable = bool(can_extend)
        report.error = report.error or {
            "type": reason,
            "stage": stage,
            "message": (
                f"Mission did not reach verifiable completion after "
                f"{budget.consumed_steps} step(s) and {budget.repairs_used} repair "
                f"attempt(s) ({reason})."
            ),
            "recoverable": can_extend,
        }
        report.final_proof = (
            f"MISSION FAILED ({reason}): {report.error['message']} "
            f"Mission {report.mission_id} can be extended and resumed."
        )
        self._save_mission(report)
        _emit("mission_finished", {"state": report.state,
                                   "progress_pct": report.progress_pct,
                                   "failure": report.failure_summary()})
        return report

    # -- resume semantics ---------------------------------------------------
    # TERMINAL  : completed, cancelled  → never resumed
    # RESUMABLE : pending/planning/executing/... and blocked → plain resume
    # RECOVERABLE: failed → only with restart_failed=True (a crash-looping
    #              mission must not be auto-resumed by a scheduler or by an
    #              eager retry path; the restart is explicit and auditable).
    def resume_mission(
        self,
        mission_id: str,
        *,
        agent: Any = None,
        on_event: Optional[Callable[..., None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        steer_source: Optional[Callable[[], list[str]]] = None,
        restart_failed: bool = False,
        extra_steps: Optional[int] = None,
    ) -> MissionReport:
        """Continue a mission that is blocked, interrupted or (explicitly) failed.

        ``restart_failed=True`` is the recovery path for a ``failed`` mission:
        it clears the recorded error, resets failed/skipped DAG nodes, grants a
        fresh step allowance and re-enters the lifecycle. Raises ``ValueError``
        when the mission is terminal (completed/cancelled) or when a failed
        mission is resumed without the explicit flag — silently returning the
        old report is what made resumability look broken.
        """
        report = self.get_mission(mission_id)
        if not report:
            raise ValueError(f"Mission {mission_id} not found")

        if report.state == MissionState.COMPLETED.value:
            raise ValueError(
                f"Mission {mission_id} is already completed; nothing to resume"
            )
        if report.state == MissionState.CANCELLED.value:
            raise ValueError(
                f"Mission {mission_id} was cancelled; start a new mission instead"
            )
        if report.state == MissionState.FAILED.value:
            if not restart_failed:
                raise ValueError(
                    f"Mission {mission_id} is FAILED (terminal). Restart it explicitly "
                    f"with resume_mission('{mission_id}', restart_failed=True) — "
                    f"{report.failure_summary().get('reason')}"
                )
            if not report.recoverable:
                raise ValueError(
                    f"Mission {mission_id} is FAILED and marked unrecoverable "
                    f"({(report.error or {}).get('message')})"
                )
            report.restarts_used += 1
            report.error = None
            report.recoverable = True
            report.finished_at = None
            # a restart needs room to redo the work it is restarting
            if extra_steps is None:
                extra_steps = 8
        elif extra_steps is None and report.budget.steps_left() <= 0:
            # resuming a mission that already burned its budget would loop
            # zero times and immediately fail again
            extra_steps = 8

        if extra_steps:
            if report.budget.extensions_used < report.budget.max_extensions:
                report.budget.grant_extension(int(extra_steps))
            else:
                report.budget.grant_emergency_extension(int(extra_steps))

        dag = AgentDAG.from_dict(report.dag_state) if report.dag_state else self._build_mission_dag(report.goal, report.domain)

        for node in dag.nodes.values():
            if node.status in (DAGNodeStatus.BLOCKED.value, DAGNodeStatus.FAILED.value, DAGNodeStatus.SKIPPED.value):
                node.status = DAGNodeStatus.READY.value
                node.retries = 0
                try:
                    node.error = None
                except Exception:
                    pass

        report.state = MissionState.CONTINUING.value
        report.blocker_reason = None
        report.blocker_instructions = None
        report.approval_request = None
        scope = self._mission_file_scope(report.mission_id)
        if self._injected_executor is not None and agent is None:
            self._tls.executor = self._bind_control_hooks(
                self._injected_executor, should_cancel=should_cancel
            )
        elif agent is not None or on_event or should_cancel or steer_source:
            self._tls.executor = make_agent_backed_executor(
                agent=agent, on_event=on_event,
                should_cancel=should_cancel, steer_source=steer_source,
                scope=scope,
                workspace_dir=str(self._mission_workspace(report.mission_id)),
            )
        else:
            self._tls.executor = None
        self._save_mission(report)
        try:
            return self._run_autonomous_loop(
                report, dag, on_event=on_event, should_cancel=should_cancel
            )
        finally:
            self._tls.executor = None
            self._tls.scope = None

    def extend_budget(self, mission_id: str, steps: int = 10, *,
                      emergency: bool = False) -> MissionReport:
        """Grant ``steps`` extra steps to a running/blocked/failed mission.

        The step allowance is tracked in ``bonus_steps`` (the loop bound is
        ``initial_steps + bonus_steps``); ~60% of each extension goes to the
        execution phase and the rest to the emergency reserve, so an extension
        reaches the phase that actually ran dry.

        * normal extensions consume one of ``max_extensions`` slots;
        * ``emergency=True`` uses the separate ``max_emergency_extensions``
          reserve (last resort for a mission that is still making progress);
        * ``completed`` missions are returned untouched, everything else —
          including ``failed`` — can be extended so it becomes resumable again
          (``extend_budget`` then ``resume_mission(..., restart_failed=True)``).

        Raises ``ValueError`` when no slot is left.
        """
        report = self.get_mission(mission_id)
        if not report:
            raise ValueError(f"Mission {mission_id} not found")

        if report.state == MissionState.COMPLETED.value:
            return report

        if emergency:
            if not report.budget.grant_emergency_extension(steps):
                raise ValueError(
                    f"Mission {mission_id} already used its "
                    f"{report.budget.max_emergency_extensions} emergency extensions"
                )
        else:
            if report.budget.extensions_used >= report.budget.max_extensions:
                raise ValueError(
                    f"Mission {mission_id} already used its "
                    f"{report.budget.max_extensions} budget extensions"
                )
            report.budget.grant_extension(steps)

        # A mission that ran out of budget is recoverable again once it has room.
        if report.state == MissionState.FAILED.value:
            report.recoverable = True
            if isinstance(report.error, dict):
                report.error["recoverable"] = True
                report.error["extended"] = True
        self._save_mission(report)
        return report


mission_engine = MissionEngine()
