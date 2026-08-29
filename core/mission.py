"""Mission Engine for Hermus.

Unifies the Mission Lifecycle directly with the Agent DAG and executes the full
autonomous repair and replan loop:
Goal → Requirements → DAG Plan → Execute → Observe → Verify (Structural + Behavioral) → Critic Panel →
If verification fails: Diagnose → Repair → Re-plan → Re-execute → Repeat until success, blocked, or budget exhausted.

This module is the **universal execution core**: every autonomous surface
(``HermusAgent.autonomous``, ``/command?autonomous``, the gateway queue, the
CLI, channels, the scheduler) runs through :class:`MissionEngine` via
``core.runtime``, so behavior no longer depends on the entry point.

Node executors are **evidence-gated**: a stage whose role implies performing
work (coder/implementation/verification/…) only counts as successful when the
agent actually did something — executed tools, changed files, produced
artifacts — not when it merely described how the work would be done.
"""
from __future__ import annotations

import inspect
import json
import os
import threading
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


@dataclass
class MissionBudget:
    initial_steps: int = 25
    consumed_steps: int = 0
    max_repairs: int = 3
    repairs_used: int = 0
    max_extensions: int = 2
    extensions_used: int = 0
    # Extra steps granted by explicit/auto extensions. Previously the loop
    # bound was `initial_steps + extensions_used*10` while extend_budget() also
    # added to initial_steps, double-counting every extension.
    bonus_steps: int = 0

    def total_steps(self) -> int:
        return self.initial_steps + self.bonus_steps

    def grant_extension(self, steps: int = 10) -> None:
        """Consume one extension slot and add exactly ``steps`` budget steps."""
        self.extensions_used += 1
        self.bonus_steps += max(1, int(steps))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["total_steps"] = self.total_steps()
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
            # Canonical response field used across /command and the dashboard.
            # ``final_proof`` is retained as the human-readable mission summary.
            "response": self.final_proof,
            "budget": self.budget.to_dict(),
            "repair_history": self.repair_history,
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
            checkpoint_id=data.get("checkpoint_id"),
            started_at=data.get("started_at", datetime.now().isoformat()),
            finished_at=data.get("finished_at"),
            final_proof=data.get("final_proof", ""),
            budget=budget,
            repair_history=data.get("repair_history", []),
        )


# ============================================================================
# Evidence-gated agent-backed node executor
# ============================================================================
# The executor must distinguish "the model described the work" from "the model
# performed the work". A coder node that answers with a plan but never touches
# a tool, a file, or a command is NOT a completed stage.

#: roles whose job is to change the world (files/code/tests), not just analyze
ACTION_ROLES = {
    "coder", "developer", "implementer", "implementation", "engineer",
    "integrator", "builder", "tester", "verifier", "fixer", "operator",
    "deployer", "specialist",
}

#: goal verbs that imply performing work even for analysis-ish roles
ACTION_GOAL_VERBS = (
    "implement", "build", "write", "create", "fix", "repair", "refactor",
    "deploy", "run ", "execute", "generate", "develop", "integrate", "patch",
    "migrate", "install", "test", "apply",
)

#: tools that mutate state or execute commands (real work, not reading)
ACTION_TOOLS = {
    "file_write", "file_edit", "sandbox_run", "shell_execute", "backend_execute",
    "swe_develop", "mission_start", "mission_resume", "rollback_checkpoint",
    "rollback_restore", "slack_notify", "jira_create_issue", "linear_create_issue",
    "github_integration_pr_comment", "custom_exploit_runtime", "browser_click",
    "browser_type", "browser_navigate", "browser_close", "embeddings_add",
    "embeddings_ingest", "memory_add", "memory2_remember", "subagent_spawn",
    "delegate_tasks", "fleet_distribute_task", "skill_harvest",
}
ACTION_TOOL_PREFIXES = ("pentest_", "browser_", "screen_", "sast_", "dast_", "custom_")

#: tools that literally execute commands/tests (strongest evidence)
EXEC_TOOLS = {"sandbox_run", "shell_execute", "backend_execute"}

#: directories never counted as workspace evidence
_SCAN_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", "target", ".next", ".cache",
    "data", ".tox", "site-packages",
}
_SCAN_MAX_FILES = 6000


def _is_action_tool(name: str) -> bool:
    n = str(name or "")
    return n in ACTION_TOOLS or any(n.startswith(p) for p in ACTION_TOOL_PREFIXES)


def _node_requires_action(node: Any) -> bool:
    """Does this node's job description imply performing (not describing) work?"""
    role = str(getattr(node, "role", "") or "").lower()
    goal = str(getattr(node, "goal", "") or "").lower()
    if any(v in goal for v in ACTION_GOAL_VERBS):
        return True
    return role in ACTION_ROLES


def _scan_changed_files(since_ts: float, roots: Optional[list[Path]] = None) -> list[str]:
    """Files modified under the given roots since ``since_ts`` (bounded walk)."""
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


def build_node_prompt(node: Any, parent_ctx: Optional[dict[str, Any]] = None) -> str:
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
) -> Callable[[Any, dict[str, Any]], dict[str, Any]]:
    """Build an executor that actually runs each DAG node goal via an agent.

    * ``agent`` — reuse a live agent (session memory/model/profile preserved
      across stages). When omitted a fresh agent is created per node.
    * ``on_event`` / ``should_cancel`` / ``steer_source`` — forwarded into every
      node's ReAct loop so SSE streaming, cooperative cancellation and mid-run
      steering work inside missions exactly like they do in plain chat.

    Never fabricates success:
    * no usable model/key/Ollama backend → the node is blocked, not completed;
    * a node whose job implies performing work must show evidence (executed
      tools / changed files / produced artifacts) — a prose description alone
      fails with ``no_evidence_of_work`` so the repair loop can act.
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

            prompt = build_node_prompt(node, parent_ctx)
            res = _chat_compat(
                node_agent, prompt,
                on_event=on_event, should_cancel=should_cancel,
                steer_source=steer_source,
            )
            text = str(res.get("response") or "")
            provider = str(getattr(getattr(node_agent, "llm", None), "provider", "") or "")

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

            # ---- evidence collection: did the agent PERFORM the work? ----
            tool_calls = list(res.get("tool_calls") or [])
            action_tools = sorted({t for t in tool_calls if _is_action_tool(t)})
            exec_tools = sorted({t for t in tool_calls if t in EXEC_TOOLS})
            files_changed = _scan_changed_files(node_started)
            # evidence the agent itself reported (tool_results carry per-tool outcomes)
            tool_results = res.get("tool_results") or []
            performed_work = bool(action_tools or exec_tools or files_changed)

            requires_action = _node_requires_action(node)
            evidence = [{
                "stage": stage,
                "status": "executed" if (performed_work or not requires_action) else "needs_evidence",
                "model": provider,
                "tools_used": len(tool_results),
                "action_tools": action_tools,
                "commands_executed": exec_tools,
                "files_changed": files_changed[:20],
                "performed_work": performed_work,
            }]

            if requires_action and not performed_work:
                # The model *described* the work without *performing* it.
                _emit("node_finished", {"stage": stage, "status": "needs_evidence",
                                        "reason": "no_evidence_of_work"})
                return {
                    "success": False,
                    "output": text,
                    "error": "no_evidence_of_work",
                    "evidence": evidence,
                    "instructions": (
                        "The stage answer contained no evidence of performed work "
                        f"(no tools executed, no files changed). Re-run this stage and "
                        "actually use the tools (write files, run commands/tests) to "
                        "accomplish the goal; do not merely describe how to do it."
                    ),
                }

            if not requires_action and len(text.strip()) < 120:
                # analysis stages still need a substantive analysis product
                _emit("node_finished", {"stage": stage, "status": "needs_evidence",
                                        "reason": "empty_analysis"})
                return {
                    "success": False,
                    "output": text,
                    "error": "empty_analysis",
                    "evidence": evidence,
                    "instructions": (
                        "Analysis stages must produce a substantive result (design, "
                        "review findings, research summary). The answer was empty or trivial."
                    ),
                }

            _emit("node_finished", {
                "stage": stage, "status": "executed",
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
        p = self.storage_dir / f"{report.mission_id}.json"
        try:
            p.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        except Exception as exc:
            record_issue("mission", "save_report", exc,
                         mission_id=report.mission_id, retryable=True,
                         fallback="mission report not persisted (run continues)")

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
        max_repairs: Optional[int] = None,
        executor: Optional[Callable[[Any, dict[str, Any]], dict[str, Any]]] = None,
        agent: Any = None,
        on_event: Optional[Callable[..., None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        steer_source: Optional[Callable[[], list[str]]] = None,
    ) -> MissionReport:
        """Plan and run a mission to completion.

        ``agent`` (or a custom ``executor``) binds this run to a specific
        execution surface — e.g. the runtime passes the user's live agent so
        mission stages share its session, model and profile. ``on_event`` /
        ``should_cancel`` / ``steer_source`` stream and control the mission the
        same way a plain chat turn is streamed and controlled.
        """
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
            budget=MissionBudget(
                initial_steps=budget_steps,
                max_repairs=max_repairs if max_repairs is not None else 3,
            ),
        )
        self._save_mission(report)

        # per-run executor override (bound agent / hooks), scoped to this thread
        if executor is not None:
            self._tls.executor = executor
        elif agent is not None or on_event or should_cancel or steer_source:
            self._tls.executor = make_agent_backed_executor(
                agent=agent,
                on_event=on_event,
                should_cancel=should_cancel,
                steer_source=steer_source,
            )
        else:
            self._tls.executor = None
        try:
            return self._run_autonomous_loop(
                report, dag,
                on_event=on_event, should_cancel=should_cancel,
            )
        finally:
            self._tls.executor = None

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

        def _emit(event_type: str, data: Optional[dict[str, Any]] = None) -> None:
            if on_event is not None:
                try:
                    on_event(event_type, {"mission_id": report.mission_id, **(data or {})})
                except Exception:
                    pass

        _emit("mission_started", {"goal": report.goal[:200], "domain": report.domain,
                                  "budget_steps": budget.total_steps()})

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
                                    "steps_left": budget.total_steps() - budget.consumed_steps})
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
                budget.grant_extension(10)
            prev_completed = completed_now

            # Check for explicit external blockers
            blocked_nodes = [n for n in dag.nodes.values() if n.status == DAGNodeStatus.BLOCKED.value]
            if blocked_nodes:
                report.state = MissionState.BLOCKED.value
                report.blocker_reason = blocked_nodes[0].error or "External prerequisite or authorization required"
                report.blocker_instructions = "Please resolve the blocker and call `hermus mission resume <mission_id>`"
                self._save_mission(report)
                record_issue("mission", "node_blocked", report.blocker_reason,
                             mission_id=report.mission_id, retryable=True,
                             fallback="mission paused as BLOCKED; resume after resolving")
                _emit("mission_finished", {"state": report.state,
                                           "blocker_reason": report.blocker_reason})
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
            report.artifacts = [
                a.path for a in artifacts
                if not getattr(a, "mission_id", None) or a.mission_id == report.mission_id
            ]

            # C. VERIFY (STRUCTURAL + BEHAVIORAL + CRITIC)
            report.state = MissionState.VERIFYING.value
            _emit("mission_state", {"state": report.state})
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
                _emit("mission_repair", {"round": budget.repairs_used,
                                          "hints": [str(h)[:120] for h in repair_hints[:6]]})
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
        _emit("mission_finished", {"state": report.state,
                                   "progress_pct": report.progress_pct})
        return report

    def resume_mission(
        self,
        mission_id: str,
        *,
        agent: Any = None,
        on_event: Optional[Callable[..., None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        steer_source: Optional[Callable[[], list[str]]] = None,
    ) -> MissionReport:
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
        if agent is not None or on_event or should_cancel or steer_source:
            self._tls.executor = make_agent_backed_executor(
                agent=agent, on_event=on_event,
                should_cancel=should_cancel, steer_source=steer_source,
            )
        else:
            self._tls.executor = None
        try:
            return self._run_autonomous_loop(
                report, dag, on_event=on_event, should_cancel=should_cancel
            )
        finally:
            self._tls.executor = None

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

        report.budget.grant_extension(steps)
        self._save_mission(report)
        return report


mission_engine = MissionEngine()
