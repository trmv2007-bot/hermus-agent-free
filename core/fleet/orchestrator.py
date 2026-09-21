"""Fleet Orchestrator - roadmap step 4."""

from __future__ import annotations
import asyncio
import time
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable, List, Dict

from core.fleet.registry import FleetRegistry, LiveAgent
from core.fleet.bus import FleetBus, FleetEvent
from core.fleet.missions import (
    MissionManager, Mission, Subtask, MissionError,
    PROPOSED, CLAIMING, WORKING, REVIEWING, SYNTHESIZING, DONE, FAILED, SUSPENDED,
    LEASE_SECONDS, CLAIM_TIMEOUT_S, STALL_LIMIT, MAX_REPLANS, MAX_REVIEW_RETRIES,
    PER_AGENT_BUDGET_SHARE, BUDGET_WARN_RATIO,
    GATE_AFTER_DECOMPOSE, GATE_AFTER_REVIEW, GATE_ON_CONFLICT,
    GATE_ON_BUDGET_WARNING, GATE_ON_STALL_REPLAN,
    POLICY_FIRST_RESULT, POLICY_HIGHEST_RELIABILITY,
    POLICY_JUDGE_MODEL, POLICY_ASK_USER,
    MISSION_OPENED, CLAIM, SUBTASK, RESULT, REVIEW, SYNTHESIS,
    GATE_PENDING, GATE_RESOLVED, MISSION_TERMINATED,
    BUDGET_WARNING, ALERT, STATE_CHANGED,
    PROPOSE,
)
from core.log import get_logger

logger = get_logger(__name__)

Planner = Callable[[str], List[str]]
Verifier = Callable[[dict[str, Any], Any], tuple[bool, str]]
Judge = Callable[[str, List[Dict[str, Any]]], str]

@dataclass
class OrchestrationResult:
    mission_id: str
    success: bool
    synthesis: dict[str, Any] | None = None
    error: str | None = None
    partial: bool = False

class Orchestrator:

    def __init__(self, registry: FleetRegistry, bus: FleetBus, *, planner: Callable[[str], List[str]] | None = None, verifier: Callable[[dict[str, Any], Any], tuple[bool, str]] | None = None, judge: Callable[[str, List[Dict[str, Any]]], str] | None = None, lease_seconds: float = 300.0, claim_timeout_s: float = 120.0) -> None:
        self.registry = registry
        self.bus = bus
        self.planner = planner
        self.verifier = verifier
        self.judge = judge
        self.lease_seconds = lease_seconds
        self.claim_timeout_s = claim_timeout_s
        from core.fleet.missions import MissionManager
        self.missions = MissionManager(bus, lease_seconds=300.0, claim_timeout_s=120.0)
        self._active_orchestrations: dict[str, dict[str, Any]] = {}

    def _default_decompose(self, goal: str) -> List[str]:
        parts = re.split(r"\band\b|\bthen\b|;|\n", goal, flags=re.IGNORECASE)
        parts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 10]
        if len(parts) >= 2: return parts[:5]
        return [f"Research and summarize facts about: {goal}", f"List practical steps / implementation for: {goal}", f"Risks, alternatives, and recommendation for: {goal}"][:3]

    async def _claim_round(self, mission_id: str, agent_ids: list[str] | None = None) -> None:
        mission = self.missions.get(mission_id)
        available_agents = [a for a in self.registry.list() if a.state == "IDLE" and (agent_ids is None or a.agent_id in agent_ids)]
        
        # If no agents available and no specific agent_ids requested, spawn a default agent
        if not available_agents and agent_ids is None:
            try:
                default_agent = self.registry.spawn({
                    "name": "DefaultWorker",
                    "provider": "groq",
                    "persona": "You are a helpful assistant that completes subtasks efficiently.",
                })
                # Wait for agent to become IDLE - poll registry for state change
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    default_agent = self.registry.get(default_agent.agent_id)
                    if default_agent and default_agent.state == "IDLE":
                        available_agents = [default_agent]
                        break
            except Exception:
                pass
        
        if not available_agents:
            return
        
        open_subtasks = [s for s in mission.subtasks if s.status == "open"]
        if not open_subtasks:
            return
        
        # Assign each open subtask to an available agent (round-robin)
        for i, subtask in enumerate(open_subtasks):
            agent = available_agents[i % len(available_agents)]
            subtask.status = "claimed"
            subtask.claimed_by = agent.agent_id
            subtask.lease_until = time.time() + self.lease_seconds
            
            # Post CLAIM event
            self.bus.append(
                sender="orchestrator",
                kind=CLAIM,
                content={
                    "mission_id": mission_id,
                    "subtask_id": subtask.id,
                    "agent_id": agent.agent_id,
                    "lease_until": subtask.lease_until,
                },
                mission_id=mission_id,
                target=agent.agent_id,
                est_tokens=10,
            )
        
        await asyncio.sleep(0.1)
        self.missions._transition(mission, WORKING)

    async def _work_phase(self, mission_id: str, agent_ids: list[str] | None = None) -> None:
        mission = self.missions.get(mission_id)
        for subtask in mission.subtasks:
            if subtask.status == "claimed" and subtask.claimed_by:
                if subtask.lease_until and time.time() > subtask.lease_until:
                    subtask.status = "open"; subtask.claimed_by = None; continue
                task_prompt = f"Subtask: {subtask.text}\nGoal: {mission.goal}"
                result = self.registry.assign(subtask.claimed_by, task_prompt, idempotency_key=f"{mission_id}:{subtask.id}")
                if result.get("executed") and not result.get("deduplicated"):
                    content = result.get("content", ""); tokens = result.get("tokens", 0)
                    subtask.status = "done"; subtask.result = content
                    self._record_agent_tokens(mission_id, subtask.claimed_by, tokens)
                    mission.blackboard.append({"key": subtask.id, "result": content, "agent": subtask.claimed_by})
                    self.bus.append(sender=subtask.claimed_by, kind=RESULT, content={"mission_id": mission_id, "subtask_id": subtask.id, "result": content, "agent_id": subtask.claimed_by}, mission_id=mission_id, est_tokens=tokens)

    async def _verify_and_review(self, mission_id: str) -> None:
        mission = self.missions.get(mission_id)
        for subtask in mission.subtasks:
            if subtask.status == "done" and subtask.result and self.verifier:
                try: ok, note = self.verifier({"id": subtask.id, "text": subtask.text}, subtask.result)
                except Exception as exc: ok, note = False, f"verifier raised: {exc}"
                subtask.reviews.append({"reviewer": "verifier", "ok": bool(ok), "note": str(note)})
                self.bus.append(sender="verifier", kind=REVIEW, content={"mission_id": mission_id, "subtask_id": subtask.id, "ok": bool(ok), "note": str(note), "phase": "verify"}, mission_id=mission_id, est_tokens=24)
                if not ok: self.missions._stall(mission, subtask.id, f"verify failed: {note}")
        for subtask in mission.subtasks:
            if subtask.status == "done" and subtask.result:
                retries = 0
                while retries <= MAX_REVIEW_RETRIES:
                    if self.judge:
                        try:
                            judge_result = self.judge(f"Review this subtask result for correctness:\nSubtask: {subtask.text}\nResult: {subtask.result}", [{"role": "user", "content": f"Subtask: {subtask.text}\nResult: {subtask.result}"}])
                            ok = bool(judge_result.get("ok", True)); note = str(judge_result.get("note", "reviewed"))
                        except Exception as exc: ok, note = False, f"judge raised: {exc}"
                    else: ok, note = True, "auto-approved (no judge)"
                    subtask.reviews.append({"reviewer": "judge", "ok": ok, "note": note})
                    self.bus.append(sender="judge", kind=REVIEW, content={"mission_id": mission_id, "subtask_id": subtask.id, "ok": ok, "note": note, "phase": "review", "retry": retries}, mission_id=mission_id, est_tokens=48)
                    if ok: break
                    retries += 1
                    if retries <= MAX_REVIEW_RETRIES and subtask.claimed_by:
                        task_prompt = f"Subtask (retry {retries}): {subtask.text}\nPrevious result: {subtask.result}\nCritique: {note}\nGoal: {mission.goal}"
                        result = self.registry.assign(subtask.claimed_by, task_prompt, idempotency_key=f"{mission_id}:{subtask.id}:retry{retries}")
                        if result.get("executed") and not result.get("deduplicated"):
                            subtask.result = result.get("content", "")
                            self._record_agent_tokens(mission_id, subtask.claimed_by, result.get("tokens", 0))
                else: subtask.status = "failed"

    @staticmethod
    def _detect_conflicts(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        conflicts = []
        texts = [(c["subtask_id"], str(c["result"] or "")) for c in candidates]
        for i, (sid_a, text_a) in enumerate(texts):
            for sid_b, text_b in texts[i+1:]:
                marker = f"CONTRADICTS:{sid_b}" in text_a or f"CONTRADICTS:{sid_a}" in text_b
                contra = marker or ("contradicts" in text_a.lower() and sid_b in text_a) or ("contradicts" in text_b.lower() and sid_a in text_b)
                if contra:
                    agents = [c["agent"] for c in candidates if c["subtask_id"] in (sid_a, sid_b)]
                    conflicts.append({"claim": f"{sid_a} vs {sid_b}", "agents": agents, "evidence": [{sid_a: text_a[:500]}, {sid_b: text_b[:500]}]})
        return conflicts

    def _resolve(self, mission: Mission, candidates: list[dict[str, Any]], conflicts: list[dict[str, Any]], *, judge: Judge | None, reliability: dict[str, float]) -> Any:
        if not candidates: return None
        policy = mission.resolution_policy
        if policy == POLICY_HIGHEST_RELIABILITY and reliability:
            best = max(candidates, key=lambda c: float(reliability.get(c["agent"] or "", 0.0)))
            return best["result"]
        if policy == POLICY_JUDGE_MODEL:
            if judge is None: raise MissionError("judge_model policy requires a judge callable")
            return judge(mission.goal, candidates)
        merged = "\n\n".join(f"[{c['subtask_id']}] {c['result']}" for c in candidates)
        if conflicts: merged += "\n\nUnresolved conflicts: " + "; ".join(c["claim"] for c in conflicts)
        return merged

    def _synthesize(self, mission_id: str, resolution_policy: str) -> dict[str, Any]:
        mission = self.missions.get(mission_id)
        candidates, missing_subtasks = [], []
        for subtask in mission.subtasks:
            if subtask.status == "done" and subtask.result:
                candidates.append({"subtask_id": subtask.id, "text": subtask.text, "result": subtask.result, "agent": subtask.claimed_by})
            elif subtask.status != "done": missing_subtasks.append(subtask.id)
        conflicts = self._detect_conflicts(candidates)
        reliability = {}
        for sub in mission.subtasks:
            if sub.claimed_by and sub.status == "done": reliability[sub.claimed_by] = reliability.get(sub.claimed_by, 0) + 1
        resolution = self._resolve(mission, candidates, conflicts, judge=self.judge, reliability=reliability)
        synthesis = {"goal": mission.goal, "resolution_policy": resolution_policy, "subtasks_total": len(mission.subtasks), "subtasks_completed": len(candidates), "subtasks_missing": missing_subtasks, "conflicts_detected": len(conflicts), "conflicts": conflicts, "result": resolution, "blackboard": mission.blackboard, "budget_used": mission.budget_used, "budget_total": mission.budget_total, "completed_at": time.time()}
        mission.synthesis = synthesis
        self.missions._transition(mission, SYNTHESIZING)
        self.bus.append(sender="orchestrator", kind=SYNTHESIS, content={"mission_id": mission_id, "synthesis": synthesis}, mission_id=mission_id, est_tokens=100)
        return synthesis

    async def _handle_gate(self, mission_id: str, gate: str, payload: dict[str, Any] | None = None) -> bool:
        mission = self.missions.get(mission_id)
        self.bus.append(sender="orchestrator", kind=GATE_PENDING, content={"mission_id": mission_id, "gate": gate, "payload": payload or {}, "state_before": mission.state}, mission_id=mission_id, est_tokens=10)
        self.missions._transition(mission, SUSPENDED)
        mission.resume_state = mission.state; mission.state = SUSPENDED
        if hasattr(self, "_gate_handler") and self._gate_handler:
            decision = await self._gate_handler(gate, payload)
            if decision.get("action") == "reject": self.missions._transition(mission, FAILED); return False
        self.bus.append(sender="orchestrator", kind=GATE_RESOLVED, content={"mission_id": mission_id, "gate": gate, "decision": "auto_approved"}, mission_id=mission_id, est_tokens=10)
        resume_state = getattr(mission, "resume_state", CLAIMING)
        self.missions._transition(mission, resume_state)
        return True

    def _check_budget(self, mission_id: str) -> tuple[bool, str | None]:
        mission = self.missions.get(mission_id)
        if mission.budget_used >= mission.budget_total:
            return False, f"Mission budget exhausted: {mission.budget_used}/{mission.budget_total}"
        if mission.budget_total > 0:
            per_agent_cap = int(mission.budget_total * PER_AGENT_BUDGET_SHARE)
            warn_threshold = int(mission.budget_total * BUDGET_WARN_RATIO)
            for agent_id, tokens in mission.agent_spend.items():
                if per_agent_cap > 0 and tokens > per_agent_cap:
                    return False, f"Mission budget exceeded: agent {agent_id} exceeded 40% budget cap ({tokens}/{per_agent_cap})"
                if tokens > warn_threshold:
                    self.bus.append(sender="orchestrator", kind=BUDGET_WARNING, content={"mission_id": mission_id, "agent_id": agent_id, "tokens_used": tokens, "warn_threshold": warn_threshold, "cap": per_agent_cap}, mission_id=mission_id, est_tokens=10)
                    if GATE_ON_BUDGET_WARNING in mission.hitl_gates: asyncio.create_task(self._handle_gate(mission_id, GATE_ON_BUDGET_WARNING, {"agent_id": agent_id, "tokens": tokens, "cap": per_agent_cap}))
        return True, None

    def _record_agent_tokens(self, mission_id: str, agent_id: str, tokens: int) -> None:
        mission = self.missions.get(mission_id)
        mission.budget_used += tokens
        mission.agent_spend[agent_id] = mission.agent_spend.get(agent_id, 0) + tokens
        self._check_budget(mission_id)

    async def _detect_and_handle_stalls(self, mission_id: str) -> bool:
        """Detect stalls (3 no-progress cycles in WORKING/REVIEWING) and trigger replan (max 2)."""
        mission = self.missions.get(mission_id)
        
        # Only detect stalls in WORKING or REVIEWING states
        if mission.state not in (WORKING, REVIEWING):
            return True
        
        if not hasattr(mission, "_stall_count"):
            mission._stall_count = 0
        
        recent_completions = sum(1 for s in mission.subtasks if s.status == "done")
        if not hasattr(mission, "_last_completion_count"):
            mission._last_completion_count = recent_completions
            return True
        
        # Only start counting stalls after at least one subtask has completed
        if mission._last_completion_count == 0:
            return True
        
        if recent_completions > mission._last_completion_count:
            mission._stall_count = 0
            mission._last_completion_count = recent_completions
            return True
        
        mission._stall_count += 1
        
        if mission._stall_count >= STALL_LIMIT:
            if mission.replans >= MAX_REPLANS:
                self.bus.append(
                    sender="orchestrator",
                    kind=ALERT,
                    content={
                        "mission_id": mission_id,
                        "level": "critical",
                        "message": f"Mission stalled {STALL_LIMIT} times, max replans ({MAX_REPLANS}) exceeded",
                    },
                    mission_id=mission_id,
                    est_tokens=20,
                )
                self.missions._transition(mission, FAILED)
                return False
            
            mission.replans += 1
            self.bus.append(
                sender="orchestrator",
                kind=ALERT,
                content={
                    "mission_id": mission_id,
                    "level": "warning",
                    "message": f"Stall detected (count={mission._stall_count}), replanning (replan {mission.replans}/{MAX_REPLANS})",
                },
                mission_id=mission_id,
                est_tokens=20,
            )
            
            if GATE_ON_STALL_REPLAN in mission.hitl_gates:
                await self._handle_gate(mission_id, GATE_ON_STALL_REPLAN, {"replan": mission.replans})
            
            if self.planner:
                new_subtask_texts = self.planner(mission.goal)
            else:
                new_subtask_texts = self._default_decompose(mission.goal)
            
            for text in new_subtask_texts:
                mission.subtasks.append(Subtask(id=str(uuid.uuid4())[:8], text=text))
            
            mission._stall_count = 0
            mission._last_completion_count = recent_completions
            
            # Add new subtasks but DON'T change state - let the work loop continue
            # The new open subtasks will be picked up in the next work phase iteration
            return True
        
        return True

    async def orchestrate(self, goal: str, *, agent_ids: list[str] | None = None, budget_tokens: int = 10000, rounds: int | None = None, hitl_gates: list[str] | None = None, resolution_policy: str = "first_result") -> "OrchestrationResult":
        mission = self.missions.open(goal=goal, budget_tokens=budget_tokens, hitl_gates=hitl_gates, resolution_policy=resolution_policy)
        try:
            if self.planner: self.missions.decompose(mission.mission_id, self.planner)
            else: self._default_decompose(goal)
            if hitl_gates and GATE_AFTER_DECOMPOSE in hitl_gates: await self._handle_gate(mission.mission_id, GATE_AFTER_DECOMPOSE, {"subtasks": [s.to_dict() for s in mission.subtasks]})
            await self._claim_round(mission.mission_id, agent_ids)
            
            # Ensure mission is in WORKING state
            mission = self.missions.get(mission.mission_id)
            if mission.state == CLAIMING:
                self.missions._transition(mission, WORKING)
            
            for _ in range(rounds or 10):
                await self._work_phase(mission.mission_id, agent_ids)
                ok, warn = self._check_budget(mission.mission_id)
                if not ok: return OrchestrationResult(mission_id=mission.mission_id, success=False, error=warn or "Budget exceeded", partial=True)
                if not await self._detect_and_handle_stalls(mission.mission_id): return OrchestrationResult(mission_id=mission.mission_id, success=False, error="Mission failed: max replans exceeded after stalls", partial=True)
                mission = self.missions.get(mission.mission_id)
                if all(s.status in ("done", "failed") for s in mission.subtasks): break
                await asyncio.sleep(0.5)
            
            # Ensure mission is in WORKING state before verification
            mission = self.missions.get(mission.mission_id)
            if mission.state == CLAIMING:
                self.missions._transition(mission, WORKING)
            if mission.state == WORKING:
                self.missions._transition(mission, REVIEWING)
            
            await self._verify_and_review(mission.mission_id)
            if hitl_gates and GATE_AFTER_REVIEW in hitl_gates: await self._handle_gate(mission.mission_id, GATE_AFTER_REVIEW, {"reviews": [s.reviews for s in mission.subtasks]})
            
            # _synthesize will handle the transition to SYNTHESIZING
            synthesis = self._synthesize(mission.mission_id, resolution_policy)
            conflicts = self._detect_conflicts([{"subtask_id": s.id, "result": s.result, "agent": s.claimed_by} for s in mission.subtasks if s.status == "done"])
            if conflicts and GATE_ON_CONFLICT in (hitl_gates or []): await self._handle_gate(mission.mission_id, GATE_ON_CONFLICT, {"conflicts": conflicts})
            self.missions.close(mission.mission_id, reason="complete", synthesis=synthesis)
            return OrchestrationResult(mission_id=mission.mission_id, success=True, synthesis=synthesis, partial=any(s.status != "done" for s in mission.subtasks))
        except Exception as e:
            from core.fleet.missions import MissionError
            logger.error(f"Orchestration failed: {e}")
            self.missions.close(mission.mission_id, reason=f"error: {e}", synthesis={"error": str(e)})
            
            # Handle budget errors specially
            if isinstance(e, MissionError) and ("exceed" in str(e).lower() or "budget" in str(e).lower()):
                return OrchestrationResult(
                    mission_id=mission.mission_id if "mission" in locals() else "",
                    success=False,
                    error=f"Mission budget exceeded: {e}",
                    partial=True,
                )

            return OrchestrationResult(
                mission_id=mission.mission_id if "mission" in locals() else "",
                success=False,
                error=str(e),
            )

    def close(self) -> None:
        self._active_orchestrations.clear()