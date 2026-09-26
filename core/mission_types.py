"""Mission domain types, phases and evidence vocabulary.

Extracted from :mod:`core.mission` so the data model can be imported without
pulling in the execution engine, the workspace, the critic panel or the
verifier registry. Everything here is pure: enums, dataclasses and constants,
with no I/O and no engine dependency.

``core.mission`` re-exports every name in this module, so existing
``from core.mission import MissionReport`` call sites are unaffected.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

# SubGoal.status defaults to a DAG node status; the value lives in agent_dag, and
# that module is itself dependency-free, so importing it here keeps the domain
# layer self-contained.
from .agent_dag import DAGNodeStatus

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
    verifier_domain: str | None = None
    # An oracle is a deterministic check this requirement can be held to; without
    # one, a satisfied flag is only the mission's own word about it.
    oracle: str = ""
    target: str = ""
    deadline_s: float | None = None
    #: satisfied | breached | claimed | unobserved — how ``satisfied`` was reached.
    status: str = "unobserved"
    verified_by: list[str] = field(default_factory=list)
    check_detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SubGoal:
    id: str
    goal: str
    role: str = "specialist"
    status: str = DAGNodeStatus.PENDING.value
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------ budgets
#: mission lifecycle phases that draw on the budget
PHASE_PLANNING = "planning"
PHASE_EXECUTION = "execution"
PHASE_VERIFICATION = "verification"
PHASE_REPAIR = "repair"
PHASE_EMERGENCY = "emergency"
MISSION_PHASES = (PHASE_PLANNING, PHASE_EXECUTION, PHASE_VERIFICATION, PHASE_REPAIR, PHASE_EMERGENCY)

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
    def allocate(self, total: int | None = None) -> None:
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
    blocker_reason: str | None = None
    blocker_instructions: str | None = None
    approval_request: dict[str, Any] | None = None
    preflight: dict[str, Any] | None = None
    create_prompts_action: dict[str, Any] | None = None
    checkpoint_id: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    finished_at: str | None = None
    final_proof: str = ""
    budget: MissionBudget = field(default_factory=MissionBudget)
    repair_history: list[dict[str, Any]] = field(default_factory=list)
    # ---- failure / recovery -------------------------------------------------
    # A crashed mission is recorded, not swallowed: ``error`` carries the
    # exception, the lifecycle stage it happened in and whether a restart is
    # likely to help. See MissionEngine.start_mission / resume_mission.
    error: dict[str, Any] | None = None
    recoverable: bool = True
    restarts_used: int = 0
    # ---- claim vs verified ----------------------------------------------------
    # What the workers asserted and what the system independently checked are two
    # different facts, and collapsing them into one boolean throws away the only
    # evidence that a self-report was wrong. Both are kept; where they disagree,
    # the disagreement is recorded rather than swallowed.
    agent_claim: dict[str, Any] = field(default_factory=dict)
    verified_result: dict[str, Any] = field(default_factory=dict)
    disagreements: list[dict[str, Any]] = field(default_factory=list)
    #: One of core.contracts.OutcomeState — how much of the claim the system
    #: confirmed for itself. Read this, not ``final_proof``, to know whether the
    #: requested thing actually happened.
    outcome_state: str = "unknown"
    #: References into the mission's evidence log (core.evidence). The payload
    #: stays there — the report carries pointers, and a reader opens a reference
    #: to see what was actually observed.
    evidence_refs: list[str] = field(default_factory=list)

    # -- state helpers ------------------------------------------------------
    TERMINAL_STATES = (MissionState.COMPLETED.value, MissionState.CANCELLED.value)
    #: states a plain ``resume_mission()`` will pick up again
    RESUMABLE_STATES = (
        MissionState.PENDING.value,
        MissionState.REQUIREMENTS.value,
        MissionState.PLANNING.value,
        MissionState.EXECUTING.value,
        MissionState.OBSERVING.value,
        MissionState.VERIFYING.value,
        MissionState.DIAGNOSING.value,
        MissionState.REPAIRING.value,
        MissionState.CONTINUING.value,
        MissionState.BLOCKED.value,
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
            "reason": (err.get("message") or self.blocker_reason or self.final_proof or f"mission ended in state '{self.state}'"),
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
            "agent_claim": self.agent_claim,
            "verified_result": self.verified_result,
            "disagreements": self.disagreements,
            "outcome_state": self.outcome_state,
            "evidence_refs": self.evidence_refs,
            "resumable": self.is_resumable(),
            # diagnostics for every non-completed mission (stage/reason/resume)
            "failure": (self.failure_summary() if self.state != MissionState.COMPLETED.value else None),
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
            agent_claim=data.get("agent_claim") or {},
            verified_result=data.get("verified_result") or {},
            disagreements=list(data.get("disagreements") or []),
            outcome_state=str(data.get("outcome_state") or "unknown"),
            evidence_refs=list(data.get("evidence_refs") or []),
        )


# ============================================================================
# Evidence-gated agent-backed node executor
# ============================================================================
# The executor must distinguish "the model described the work" from "the model
# performed the work". A coder node that answers with a plan but never touches
# a tool, a file, or a command is NOT a completed stage.

#: the three kinds of evidence a stage can produce
EVIDENCE_CHANGE = "change"  # files/code created or modified
EVIDENCE_EXECUTION = "execution"  # commands/tests actually run
EVIDENCE_ANALYSIS = "analysis"  # substantive written finding

#: minimum characters for a written finding to count as analysis evidence
MIN_ANALYSIS_CHARS = 120
#: shorter, but still concrete — used for test verdicts / observations
MIN_FINDING_CHARS = 40

#: roles whose job is to change the world (files/code/tests), not just analyze
CHANGE_ROLES = {
    "coder",
    "developer",
    "implementer",
    "implementation",
    "engineer",
    "integrator",
    "builder",
    "fixer",
    "deployer",
    "operator",
    "patcher",
    "migrator",
    "installer",
}

#: roles whose job is to observe, judge or analyse — a written finding IS the
#: deliverable, and demanding a file change from them (the old rule) punished
#: a verifier for correctly reporting "tests failed because X".
OBSERVATION_ROLES = {
    "verifier",
    "tester",
    "reviewer",
    "auditor",
    "inspector",
    "analyst",
    "researcher",
    "architect",
    "spec",
    "specifier",
    "planner",
    "critic",
    "observer",
    "qa",
    "validator",
    "monitor",
}

#: kept for backwards compatibility with older callers/injected executors
ACTION_ROLES = CHANGE_ROLES | OBSERVATION_ROLES | {"specialist"}

#: goal verbs that mean "produce/change an artifact"
CHANGE_GOAL_VERBS = (
    "implement",
    "build",
    "write",
    "create",
    "fix",
    "repair",
    "refactor",
    "deploy",
    "generate",
    "develop",
    "integrate",
    "patch",
    "migrate",
    "install",
    "scaffold",
    "add a",
    "modify",
    "update the",
    "apply",
)

#: goal verbs that make a stage an *analysis* stage (a report is the product)
ANALYSIS_GOAL_VERBS = (
    "analyz",
    "analyse",
    "review",
    "research",
    "investigate",
    "design",
    "summarize",
    "summarise",
    "compare",
    "audit",
    "inspect",
    "evaluate",
    "assess",
    "document",
    "explain",
    "plan",
    "draft",
    "report",
    "describe",
    "diagnose",
    "check whether",
    "verify",
    "validate",
    "critique",
)

#: goal verbs that ask for something to be *executed* (tests, builds, commands)
EXEC_GOAL_VERBS = (
    "run ",
    "run the",
    "execute",
    "pytest",
    "run tests",
    "test the",
    "benchmark",
    "compile",
    "build the",
    "start the",
    "launch",
    "smoke test",
)

#: legacy alias: any verb that implies performing (not describing) work
ACTION_GOAL_VERBS = CHANGE_GOAL_VERBS + EXEC_GOAL_VERBS + ("test",)

# ---- tool taxonomy ------------------------------------------------------------
# The old gate counted *any* "action tool" as proof of work, so an agent could
# satisfy a coding stage by writing a memory entry or posting a Slack message.
# Goal-completion tools and supporting tools are now separated.

#: tools that produce or mutate the deliverable (goal-completion evidence)
GOAL_EVIDENCE_TOOLS = {
    "file_write",
    "file_edit",
    "file_delete",
    "file_move",
    "file_copy",
    "swe_develop",
    "git_apply_patch",
    "git_commit",
    "patch_apply",
    "sandbox_run",
    "shell_execute",
    "backend_execute",
    "mission_start",
    "mission_resume",
    "rollback_checkpoint",
    "rollback_restore",
}
#: tools that literally execute commands/tests (strongest evidence)
EXEC_TOOLS = {"sandbox_run", "shell_execute", "backend_execute"}
#: tool families that perform real (domain-specific) actions
GOAL_TOOL_PREFIXES = ("pentest_", "browser_", "screen_", "sast_", "dast_", "custom_")
#: auxiliary actions: useful, but they never prove the goal was accomplished
SUPPORTING_TOOLS = {
    "memory_add",
    "memory2_remember",
    "memory_search",
    "embeddings_add",
    "embeddings_ingest",
    "embeddings_search",
    "slack_notify",
    "jira_create_issue",
    "linear_create_issue",
    "github_integration_pr_comment",
    "github_integration_pr_create",
    "skill_harvest",
    "skill_use",
    "subagent_spawn",
    "delegate_tasks",
    "fleet_distribute_task",
    "notion_create_page",
    "email_send_draft",
}
#: legacy alias (kept so third-party executors importing it keep working)
ACTION_TOOLS = GOAL_EVIDENCE_TOOLS | SUPPORTING_TOOLS
ACTION_TOOL_PREFIXES = GOAL_TOOL_PREFIXES

#: directories never counted as workspace evidence
_SCAN_SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
    ".next",
    ".cache",
    "data",
    ".tox",
    "site-packages",
}
_SCAN_MAX_FILES = 6000
