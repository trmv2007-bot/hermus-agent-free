"""Fleet Missions — durable, event-sourced orchestration objects (SPEC §7).

A mission is the unit of multi-agent work: decompose → PROPOSE → CLAIM
(with leases) → WORK → VERIFY → REVIEW → SYNTHESIZE → DONE. Missions are
*durable objects on the bus*: ``mission_opened``, ``claim``/``subtask``,
``result``, ``gate_pending``/``gate_resolved``, ``mission_terminated`` are
bus events, so any agent can resume coordination by replaying the mission's
event prefix — a dead coordinator never orphans a mission and never disables
budgets (§7, fix for gap O-arch-15). Budget enforcement lives HERE (the Bus/
Registry layer), not in any coordinator agent.

State machine (transitions are validated; every transition also emits a
``state_changed`` bus event so invalid states are statically detectable)::

    PROPOSED → CLAIMING → WORKING ⇄ REVIEWING → SYNTHESIZING → DONE | FAILED
       any → SUSPENDED(gate) → resume at gate
       CLAIMING with no live agents → detected, escalated (replan or fail)

Deliberate scope line: this module never calls an LLM or the network. The
planner (decompose), verifier, judge-model and summarizer are injected
callables — deterministic stubs in tests, real model calls wired by the
orchestrator in roadmap step 5. That keeps missions unit-testable offline.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from core.log import get_logger

from .bus import (
    ALERT,
    BUDGET_WARNING,
    CLAIM,
    FleetBus,
    GATE_PENDING,
    GATE_RESOLVED,
    MISSION_OPENED,
    MISSION_TERMINATED,
    PROPOSE,
    RESULT,
    REVIEW,
    STATE_CHANGED,
    SUBTASK,
    SYNTHESIS,
)

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# States, gates, policies (SPEC §7)
# --------------------------------------------------------------------------- #

PROPOSED = "proposed"
CLAIMING = "claiming"
WORKING = "working"
REVIEWING = "reviewing"
SYNTHESIZING = "synthesizing"
DONE = "done"
FAILED = "failed"
SUSPENDED = "suspended"

STATES: tuple[str, ...] = (
    PROPOSED, CLAIMING, WORKING, REVIEWING, SYNTHESIZING, DONE, FAILED, SUSPENDED,
)

#: Allowed transitions; SUSPENDED may resume to the recorded ``resume_state``.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    PROPOSED: (CLAIMING, SUSPENDED, FAILED),
    CLAIMING: (WORKING, SUSPENDED, FAILED),
    WORKING: (REVIEWING, SUSPENDED, FAILED),
    REVIEWING: (WORKING, SYNTHESIZING, SUSPENDED, FAILED),
    SYNTHESIZING: (DONE, FAILED, SUSPENDED),
    SUSPENDED: (CLAIMING, WORKING, REVIEWING, SYNTHESIZING, DONE, FAILED),
    DONE: (),
    FAILED: (),
}

#: Claim lease: claimed work must heartbeat or it returns to the pool (§7 O1).
LEASE_SECONDS = 300.0
#: Claim round window: unclaimed subtasks return to the pool after this (§7 O1).
CLAIM_TIMEOUT_S = 120.0
#: Stall-driven replan (Magentic-One pattern, §7): 3 stalls → replan, ≤2 replans.
STALL_LIMIT = 3
MAX_REPLANS = 2
#: Review retries per subtask before the subtask fails honestly.
MAX_REVIEW_RETRIES = 2

#: HITL gates (LangGraph interrupt-style, §7 O7).
GATE_AFTER_DECOMPOSE = "after_decompose"
GATE_AFTER_REVIEW = "after_review"
GATE_ON_CONFLICT = "on_conflict"
GATE_ON_BUDGET_WARNING = "on_budget_warning"
GATE_ON_STALL_REPLAN = "on_stall_replan"

HITL_GATES: tuple[str, ...] = (
    GATE_AFTER_DECOMPOSE,
    GATE_AFTER_REVIEW,
    GATE_ON_CONFLICT,
    GATE_ON_BUDGET_WARNING,
    GATE_ON_STALL_REPLAN,
)

#: Conflict-resolution policies for SYNTHESIZE (§7 O5).
POLICY_FIRST_RESULT = "first_result"
POLICY_HIGHEST_RELIABILITY = "highest_reliability"
POLICY_JUDGE_MODEL = "judge_model"
POLICY_ASK_USER = "ask_user"

#: Chatter economics (§7 O6): no agent may spend more than this share.
PER_AGENT_BUDGET_SHARE = 0.40
#: Soft threshold: warn the coordinator at 70% of the mission budget.
BUDGET_WARN_RATIO = 0.70

#: Injectable planner: goal → ordered subtask texts. Real LLM planners are
#: wired by the orchestrator (step 5); tests inject deterministic stubs.
Planner = Callable[[str], list[str]]
#: Injectable programmatic verifier: (subtask, result) → (ok, note).
Verifier = Callable[[dict[str, Any], Any], tuple[bool, str]]
#: Injectable judge model for POLICY_JUDGE_MODEL: (goal, candidates) → text.
Judge = Callable[[str, list[dict[str, Any]]], str]

class MissionError(Exception):
    """Invalid transition, exhausted budget, unknown mission — always explicit."""


@dataclass
class Subtask:
    """One unit of decomposed work."""

    id: str = ""
    text: str = ""
    required_skills: list[str] = field(default_factory=list)
    status: str = "open"  # open | claimed | done | failed | returned
    claimed_by: str | None = None
    lease_until: float = 0.0
    result: Any = None
    review_retries: int = 0
    reviews: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "required_skills": list(self.required_skills),
            "status": self.status,
            "claimed_by": self.claimed_by,
            "lease_until": self.lease_until,
            "result": self.result,
            "review_retries": self.review_retries,
            "reviews": list(self.reviews),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Subtask":
        return cls(
            id=str(data.get("id") or ""),
            text=str(data.get("text") or ""),
            required_skills=list(data.get("required_skills") or []),
            status=str(data.get("status") or "open"),
            claimed_by=data.get("claimed_by"),
            lease_until=float(data.get("lease_until") or 0.0),
            result=data.get("result"),
            review_retries=int(data.get("review_retries") or 0),
            reviews=list(data.get("reviews") or []),
        )


@dataclass
class Mission:
    """Durable orchestration object. Rebuildable from its bus event prefix."""

    mission_id: str = ""
    goal: str = ""
    state: str = PROPOSED
    resume_state: str | None = None
    subtasks: list[Subtask] = field(default_factory=list)
    budget_total: int = 0
    budget_used: int = 0
    agent_spend: dict[str, int] = field(default_factory=dict)
    warned: bool = False
    hitl_gates: list[str] = field(default_factory=list)
    gates_pending: list[dict[str, Any]] = field(default_factory=list)
    stalls: int = 0
    replans: int = 0
    blackboard: list[dict[str, Any]] = field(default_factory=list)
    synthesis: dict[str, Any] | None = None
    missing: list[str] = field(default_factory=list)
    terminate_reason: str | None = None
    opened_seq: int = 0
    closed_seq: int = 0
    resolution_policy: str = POLICY_FIRST_RESULT
    claim_deadline: float = 0.0

    def ledger(self) -> dict[str, list[str]]:
        done = [s.id for s in self.subtasks if s.status == "done"]
        failed = [s.id for s in self.subtasks if s.status == "failed"]
        pending = [s.id for s in self.subtasks if s.status in ("open", "claimed", "returned")]
        stuck = [s.id for s in self.subtasks if s.status == "claimed" and s.lease_until and s.lease_until < time.time()]
        return {"done": done, "failed": failed, "pending": pending, "stuck": stuck}

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "goal": self.goal,
            "state": self.state,
            "resume_state": self.resume_state,
            "subtasks": [s.to_dict() for s in self.subtasks],
            "budget_total": self.budget_total,
            "budget_used": self.budget_used,
            "agent_spend": dict(self.agent_spend),
            "warned": self.warned,
            "hitl_gates": list(self.hitl_gates),
            "gates_pending": list(self.gates_pending),
            "stalls": self.stalls,
            "replans": self.replans,
            "blackboard": list(self.blackboard),
            "synthesis": self.synthesis,
            "missing": list(self.missing),
            "terminate_reason": self.terminate_reason,
            "opened_seq": self.opened_seq,
            "closed_seq": self.closed_seq,
            "resolution_policy": self.resolution_policy,
            "claim_deadline": self.claim_deadline,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Mission":
        mission = cls(
            mission_id=str(data.get("mission_id") or ""),
            goal=str(data.get("goal") or ""),
            state=str(data.get("state") or PROPOSED),
            resume_state=data.get("resume_state"),
            budget_total=int(data.get("budget_total") or 0),
            budget_used=int(data.get("budget_used") or 0),
            agent_spend=dict(data.get("agent_spend") or {}),
            warned=bool(data.get("warned")),
            hitl_gates=list(data.get("hitl_gates") or []),
            gates_pending=list(data.get("gates_pending") or []),
            stalls=int(data.get("stalls") or 0),
            replans=int(data.get("replans") or 0),
            blackboard=list(data.get("blackboard") or []),
            synthesis=data.get("synthesis"),
            missing=list(data.get("missing") or []),
            terminate_reason=data.get("terminate_reason"),
            opened_seq=int(data.get("opened_seq") or 0),
            closed_seq=int(data.get("closed_seq") or 0),
            resolution_policy=str(data.get("resolution_policy") or POLICY_FIRST_RESULT),
            claim_deadline=float(data.get("claim_deadline") or 0.0),
        )
        mission.subtasks = [Subtask.from_dict(s) for s in (data.get("subtasks") or [])]
        return mission


class MissionManager:
    """Owns missions; every mutation is validated + bus-emitted + budgeted.

    Parameters
    ----------
    bus: the FleetBus missions live on.
    lease_seconds / claim_timeout_s: claim mechanics (§7 O1).
    clock: injectable wall clock (tests freeze/advance time).
    """

    def __init__(
        self,
        bus: FleetBus,
        *,
        lease_seconds: float = LEASE_SECONDS,
        claim_timeout_s: float = CLAIM_TIMEOUT_S,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.bus = bus
        self.lease_seconds = max(1.0, float(lease_seconds))
        self.claim_timeout_s = max(1.0, float(claim_timeout_s))
        self._clock = clock or time.time
        self._missions: dict[str, Mission] = {}

    # ---------- lookup ----------

    def get(self, mission_id: str) -> Mission:
        try:
            return self._missions[str(mission_id)]
        except KeyError:
            raise MissionError(f"unknown mission {mission_id!r}") from None

    def list(self) -> list[Mission]:
        return list(self._missions.values())

    # ---------- internal helpers ----------

    def _transition(self, mission: Mission, target: str, *, sender: str = "mission-manager") -> None:
        allowed = TRANSITIONS.get(mission.state, ())
        if target not in allowed:
            raise MissionError(f"illegal transition {mission.state} → {target} (mission {mission.mission_id})")
        previous = mission.state
        mission.state = target
        if target == SUSPENDED:
            mission.resume_state = previous
        self.bus.append(
            sender=sender,
            kind=STATE_CHANGED,
            content={"scope": "mission", "mission_id": mission.mission_id, "from": previous, "to": target},
            mission_id=mission.mission_id,
            est_tokens=24,
        )

    def _spend(self, mission: Mission, agent: str, tokens: int) -> None:
        """Charge ``tokens`` to the mission budget; warn/stop at thresholds.

        Per-agent sub-quota (40% default) is enforced on CLAIM/WORK charges so
        one chatty agent cannot eat the mission. The 100% hard stop terminates
        with a partial synthesis — it never blocks forever.
        """
        tokens = max(0, int(tokens))
        if tokens == 0 or mission.budget_total <= 0:
            return
        if mission.state in (DONE, FAILED):
            return
        spend = mission.agent_spend.get(agent, 0) + tokens
        cap = int(mission.budget_total * PER_AGENT_BUDGET_SHARE)
        if spend > cap:
            raise MissionError(f"agent {agent} would exceed its {cap}-token share of mission {mission.mission_id}")
        mission.agent_spend[agent] = spend
        mission.budget_used += tokens
        ratio = mission.budget_used / mission.budget_total
        if ratio >= 1.0:
            mission.missing = [s.id for s in mission.subtasks if s.status != "done"]
            mission.synthesis = self._partial_synthesis(mission)
            self._terminate_locked(mission, reason="budget")
        elif ratio >= BUDGET_WARN_RATIO and not mission.warned:
            mission.warned = True
            self.bus.append(
                sender="mission-manager",
                kind=BUDGET_WARNING,
                content={"mission_id": mission.mission_id, "used": mission.budget_used, "total": mission.budget_total},
                mission_id=mission.mission_id,
                est_tokens=24,
            )
            if GATE_ON_BUDGET_WARNING in mission.hitl_gates:
                self.suspend(mission.mission_id, GATE_ON_BUDGET_WARNING)

    def _partial_synthesis(self, mission: Mission) -> dict[str, Any]:
        done = {s.id: s.result for s in mission.subtasks if s.status == "done" and s.result is not None}
        return {
            "answer": None,
            "partial": True,
            "results": done,
            "missing": [s.id for s in mission.subtasks if s.status != "done"],
            "reason": mission.terminate_reason,
        }

    def _terminate_locked(self, mission: Mission, *, reason: str) -> dict[str, Any]:
        mission.terminate_reason = reason
        if mission.synthesis is None:
            mission.synthesis = self._partial_synthesis(mission)
        event = self.bus.append(
            sender="mission-manager",
            kind=MISSION_TERMINATED,
            content={"mission_id": mission.mission_id, "reason": reason, "synthesis": mission.synthesis},
            mission_id=mission.mission_id,
            est_tokens=32,
        )
        mission.closed_seq = event.seq
        if mission.state != SUSPENDED:
            self._transition(mission, FAILED if reason not in ("complete",) else DONE)
        else:
            mission.state = FAILED if reason != "complete" else DONE
        return mission.synthesis

    def _gate_pending(self, mission: Mission, gate: str, payload: dict[str, Any]) -> None:
        if gate not in mission.hitl_gates:
            return
        mission.gates_pending.append({"gate": gate, "payload": payload, "at": self._clock()})
        self.bus.append(
            sender="mission-manager",
            kind=GATE_PENDING,
            content={"mission_id": mission.mission_id, "gate": gate, "payload": payload},
            mission_id=mission.mission_id,
            est_tokens=24,
        )
        self._transition(mission, SUSPENDED)


    # ---------- lifecycle ----------

    def open(
        self,
        goal: str,
        *,
        budget_tokens: int = 0,
        hitl_gates: list[str] | None = None,
        resolution_policy: str = POLICY_FIRST_RESULT,
        sender: str = "user",
    ) -> Mission:
        """Open a mission (PROPOSED + ``mission_opened`` bus event)."""
        if not str(goal or "").strip():
            raise MissionError("goal must be a non-empty string")
        gates = [g for g in (hitl_gates or []) if g in HITL_GATES]
        if GATE_ON_BUDGET_WARNING not in gates:
            gates.append(GATE_ON_BUDGET_WARNING)  # §7: default ON
        if resolution_policy == POLICY_ASK_USER and GATE_ON_CONFLICT not in gates:
            gates.append(GATE_ON_CONFLICT)  # ask_user IS the on_conflict gate
        if resolution_policy not in (POLICY_FIRST_RESULT, POLICY_HIGHEST_RELIABILITY, POLICY_JUDGE_MODEL, POLICY_ASK_USER):
            raise MissionError(f"unknown resolution policy {resolution_policy!r}")
        mission = Mission(
            mission_id=f"m_{uuid.uuid4().hex[:12]}",
            goal=str(goal).strip(),
            budget_total=max(0, int(budget_tokens)),
            hitl_gates=gates,
            resolution_policy=resolution_policy,
        )
        event = self.bus.append(
            sender=sender,
            kind=MISSION_OPENED,
            content={"mission_id": mission.mission_id, "goal": mission.goal, "budget_total": mission.budget_total},
            mission_id=mission.mission_id,
            est_tokens=32,
        )
        mission.opened_seq = event.seq
        self._missions[mission.mission_id] = mission
        return mission

    def decompose(self, mission_id: str, planner: Planner, *, est_tokens: int = 200) -> list[Subtask]:
        """Run the planner, publish subtasks, move PROPOSED → CLAIMING (PROPOSE)."""
        mission = self.get(mission_id)
        if mission.state != PROPOSED:
            raise MissionError(f"decompose requires PROPOSED, mission is {mission.state}")
        texts = planner(mission.goal) or []
        mission.subtasks = [
            Subtask(id=f"s{i + 1}", text=str(t).strip(), status="open") for i, t in enumerate(texts) if str(t).strip()
        ]
        if not mission.subtasks:
            raise MissionError("planner returned no usable subtasks")
        self._spend(mission, "planner", est_tokens)
        self.bus.append(
            sender="mission-manager",
            kind=PROPOSE,
            content={"mission_id": mission.mission_id, "subtasks": [s.to_dict() for s in mission.subtasks]},
            mission_id=mission.mission_id,
            est_tokens=est_tokens,
        )
        mission.claim_deadline = self._clock() + self.claim_timeout_s
        claimed_round = list(mission.subtasks)
        self._transition(mission, CLAIMING)
        self._gate_pending(mission, GATE_AFTER_DECOMPOSE, {"subtasks": [s.text for s in mission.subtasks]})
        return claimed_round

    # ---------- claims (single-writer, first by seq, leased) ----------

    def _find_subtask(self, mission: Mission, subtask_id: str) -> Subtask:
        for sub in mission.subtasks:
            if sub.id == subtask_id:
                return sub
        raise MissionError(f"unknown subtask {subtask_id!r} in mission {mission.mission_id}")

    def _bus_claim_winner(self, mission: Mission, subtask_id: str) -> str | None:
        """Current durable holder of ``subtask_id`` per the log, or ``None``.

        Replays the subtask's event history in seq order: CLAIM sets the
        holder, ``lease_expired`` clears it (the work is back in the pool —
        anyone may claim), ``result`` finishes it. This is what makes
        re-claim after lease expiry legal while a live claim by another
        agent is not.
        """
        history = self._claim_history(mission.mission_id, subtask_id)
        return history[0][0] if history else None

    def _claim_history(self, mission_id: str, subtask_id: str) -> list[tuple[str, int]]:
        """All (agent, seq) CLAIMs for a subtask, in log order (oldest first)."""
        history: list[tuple[str, int]] = []
        holder: str | None = None
        for event in self.bus.tail():
            content = event.content if isinstance(event.content, dict) else {}
            sid = content.get("subtask_id")
            if event.mission_id != mission_id or sid != subtask_id:
                continue
            if event.kind == CLAIM:
                holder = str(content.get("agent_id") or event.sender)
                history.append((holder, event.seq))
            elif event.kind == SUBTASK and content.get("event") == "lease_expired":
                holder = None
                history.clear()
            elif event.kind == RESULT:
                holder = None
        return history if holder is not None else []

    def claim(self, mission_id: str, agent_id: str, subtask_id: str, *, est_tokens: int = 24) -> Subtask:
        """Claim a subtask: timed round, leased work, first-by-seq wins (§7 O1)."""
        mission = self.get(mission_id)
        if mission.state != CLAIMING:
            raise MissionError(f"claim requires CLAIMING, mission is {mission.state}")
        if mission.claim_deadline and self._clock() > mission.claim_deadline:
            raise MissionError(f"claim round for mission {mission_id} has timed out")
        sub = self._find_subtask(mission, subtask_id)
        if sub.status == "claimed":
            raise MissionError(f"subtask {subtask_id} already claimed by {sub.claimed_by}")
        if sub.status != "open":
            raise MissionError(f"subtask {subtask_id} is {sub.status}, not claimable")
        winner = self._bus_claim_winner(mission, subtask_id)
        if winner is not None and winner != agent_id:
            raise MissionError(f"subtask {subtask_id} already claimed on the bus by {winner} (first by seq wins)")
        self._spend(mission, agent_id, est_tokens)
        now = self._clock()
        sub.status = "claimed"
        sub.claimed_by = agent_id
        sub.lease_until = now + self.lease_seconds
        self.bus.append(
            sender=agent_id,
            kind=CLAIM,
            content={"mission_id": mission.mission_id, "subtask_id": subtask_id, "agent_id": agent_id, "lease_until": sub.lease_until},
            mission_id=mission.mission_id,
            est_tokens=est_tokens,
        )
        return sub

    def heartbeat(self, mission_id: str, agent_id: str) -> float:
        """Renew the worker's lease; returns the new ``lease_until``."""
        mission = self.get(mission_id)
        renewed = 0.0
        for sub in mission.subtasks:
            if sub.status == "claimed" and sub.claimed_by == agent_id:
                sub.lease_until = self._clock() + self.lease_seconds
                renewed = sub.lease_until
        if not renewed:
            raise MissionError(f"agent {agent_id} holds no live claim in mission {mission_id}")
        return renewed

    def expire_leases(self, mission_id: str) -> list[str]:
        """Return expired-lease subtasks to the pool (bus SUBTASK event each)."""
        mission = self.get(mission_id)
        now = self._clock()
        returned: list[str] = []
        for sub in mission.subtasks:
            if sub.status == "claimed" and sub.lease_until and sub.lease_until < now:
                holder = sub.claimed_by
                sub.status = "open"
                sub.claimed_by = None
                sub.lease_until = 0.0
                returned.append(sub.id)
                self.bus.append(
                    sender="mission-manager",
                    kind=SUBTASK,
                    content={"mission_id": mission.mission_id, "subtask_id": sub.id, "event": "lease_expired", "previous_holder": holder},
                    mission_id=mission.mission_id,
                    est_tokens=16,
                )
        return returned

    def claim_timeout_sweep(self, mission_id: str) -> list[str]:
        """Close the claim round → CLAIMING → WORKING; returns still-open ids."""
        mission = self.get(mission_id)
        if mission.state != CLAIMING:
            raise MissionError(f"claim sweep requires CLAIMING, mission is {mission.state}")
        self.expire_leases(mission_id)
        self._transition(mission, WORKING)
        return [s.id for s in mission.subtasks if s.status == "open"]


    # ---------- work / verify / review ----------

    def submit_result(
        self, mission_id: str, agent_id: str, subtask_id: str, result: Any, *, est_tokens: int = 200
    ) -> Subtask:
        """Worker posts a result to the blackboard; mission → REVIEWING."""
        mission = self.get(mission_id)
        if mission.state not in (WORKING, REVIEWING):
            raise MissionError(f"submit_result requires WORKING/REVIEWING, mission is {mission.state}")
        sub = self._find_subtask(mission, subtask_id)
        if sub.status != "claimed" or sub.claimed_by != agent_id:
            raise MissionError(f"subtask {subtask_id} is not claimed by {agent_id}")
        self._spend(mission, agent_id, est_tokens)
        sub.result = result
        sub.status = "done"
        sub.lease_until = 0.0
        mission.blackboard.append(
            {"key": subtask_id, "result": result, "agent": agent_id, "at": self._clock()}
        )
        self.bus.append(
            sender=agent_id,
            kind=RESULT,
            content={"mission_id": mission.mission_id, "subtask_id": subtask_id, "agent_id": agent_id, "result": result},
            mission_id=mission.mission_id,
            est_tokens=est_tokens,
        )
        if mission.state == WORKING:
            self._transition(mission, REVIEWING)
        return sub

    def verify(
        self, mission_id: str, subtask_id: str, verifier: Verifier, *, reviewer: str = "mission-manager"
    ) -> tuple[bool, str]:
        """Programmatic check BEFORE any LLM review (§7 O4); failure = a stall."""
        mission = self.get(mission_id)
        sub = self._find_subtask(mission, subtask_id)
        try:
            ok, note = verifier({"id": sub.id, "text": sub.text}, sub.result)
        except Exception as exc:
            ok, note = False, f"verifier raised: {exc}"
        sub.reviews.append({"reviewer": reviewer, "kind": "verify", "ok": bool(ok), "note": str(note)})
        self.bus.append(
            sender=reviewer,
            kind=REVIEW,
            content={"mission_id": mission.mission_id, "subtask_id": subtask_id, "ok": bool(ok), "note": str(note), "phase": "verify"},
            mission_id=mission.mission_id,
            est_tokens=24,
        )
        if not ok:
            self._stall(mission, subtask_id, f"verify failed: {note}")
        return bool(ok), str(note)

    def record_review(
        self, mission_id: str, subtask_id: str, *, ok: bool, critique: str = "", reviewer: str = "reviewer"
    ) -> Subtask:
        """Record an LLM review decision (bounded retries, §7); fail → rework."""
        mission = self.get(mission_id)
        sub = self._find_subtask(mission, subtask_id)
        sub.reviews.append({"reviewer": reviewer, "kind": "review", "ok": bool(ok), "note": str(critique)})
        self.bus.append(
            sender=reviewer,
            kind=REVIEW,
            content={"mission_id": mission.mission_id, "subtask_id": subtask_id, "ok": bool(ok), "critique": str(critique)},
            mission_id=mission.mission_id,
            est_tokens=48,
        )
        if ok:
            return sub
        sub.review_retries += 1
        if sub.review_retries > MAX_REVIEW_RETRIES:
            sub.status = "failed"
            self._stall(mission, subtask_id, f"review retries exhausted: {critique}")
        else:
            sub.status = "claimed"  # back to the worker with critique attached
            if mission.state == REVIEWING:
                self._transition(mission, WORKING)
        return sub


    # ---------- stall / replan ----------

    def _stall(self, mission: Mission, subtask_id: str, note: str) -> None:
        mission.stalls += 1
        logger.warning("[Mission %s] stall %s on %s: %s", mission.mission_id, mission.stalls, subtask_id, note)
        self.bus.append(
            sender="mission-manager",
            kind=ALERT,
            content={"mission_id": mission.mission_id, "alert": "stall", "stalls": mission.stalls, "subtask_id": subtask_id, "note": note},
            mission_id=mission.mission_id,
            est_tokens=24,
        )
        if GATE_ON_STALL_REPLAN in mission.hitl_gates:
            self._gate_pending(mission, GATE_ON_STALL_REPLAN, {"stalls": mission.stalls, "subtask_id": subtask_id})

    def replan(self, mission_id: str, planner: Planner, *, est_tokens: int = 200) -> list[Subtask]:
        """Stall-driven replan: re-decompose unfinished work (≤2, else fail)."""
        mission = self.get(mission_id)
        if mission.state not in (WORKING, REVIEWING, SUSPENDED):
            raise MissionError(f"replan requires WORKING/REVIEWING, mission is {mission.state}")
        if GATE_ON_STALL_REPLAN in mission.hitl_gates and not any(
            g["gate"] == GATE_ON_STALL_REPLAN for g in mission.gates_pending
        ):
            raise MissionError("replan is gated on on_stall_replan: resolve the gate first")
        if mission.replans >= MAX_REPLANS:
            self._terminate_locked(mission, reason="stalls_exhausted")
            raise MissionError(f"mission {mission_id} failed honestly: replan budget exhausted")
        remaining = "; ".join(f"{s.id}: {s.text}" for s in mission.subtasks if s.status != "done")
        texts = planner(mission.goal + "\n\nUnfinished: " + remaining) or []
        mission.replans += 1
        mission.stalls = 0
        base = len(mission.subtasks)
        fresh = [Subtask(id=f"s{base + i + 1}", text=str(t).strip(), status="open") for i, t in enumerate(texts) if str(t).strip()]
        if not fresh:
            raise MissionError("replan planner returned no usable subtasks")
        mission.subtasks.extend(fresh)
        self._spend(mission, "planner", est_tokens)
        self.bus.append(
            sender="mission-manager",
            kind=PROPOSE,
            content={"mission_id": mission.mission_id, "event": "replan", "replan": mission.replans, "subtasks": [s.to_dict() for s in fresh]},
            mission_id=mission.mission_id,
            est_tokens=est_tokens,
        )
        if mission.state == SUSPENDED:
            mission.state = WORKING
        elif mission.state == REVIEWING:
            self._transition(mission, WORKING)
        mission.claim_deadline = self._clock() + self.claim_timeout_s
        return fresh


    # ---------- synthesize (conflict contract) ----------

    def synthesize(
        self,
        mission_id: str,
        *,
        judge: Judge | None = None,
        reliability: dict[str, float] | None = None,
        est_tokens: int = 200,
    ) -> dict[str, Any]:
        """Merge results into one deliverable — surfacing conflicts (§7 O5)."""
        mission = self.get(mission_id)
        allowed_from = (REVIEWING, WORKING, SUSPENDED)
        if mission.state not in allowed_from:
            raise MissionError(f"synthesize requires REVIEWING/WORKING, mission is {mission.state}")
        candidates = [
            {"subtask_id": s.id, "text": s.text, "result": s.result, "agent": s.claimed_by}
            for s in mission.subtasks
            if s.status == "done"
        ]
        self._spend(mission, "synthesizer", est_tokens)
        conflicts = self._detect_conflicts(candidates)
        if conflicts and mission.resolution_policy == POLICY_ASK_USER:
            self._gate_pending(mission, GATE_ON_CONFLICT, {"conflicts": conflicts})
            raise MissionError(f"mission {mission_id} suspended on on_conflict gate ({len(conflicts)} conflict(s))")
        answer = self._resolve(mission, candidates, conflicts, judge=judge, reliability=reliability or {})
        mission.synthesis = {
            "answer": answer,
            "results": {c["subtask_id"]: c["result"] for c in candidates},
            "conflicts": conflicts,
            "policy": mission.resolution_policy,
            "partial": False,
            "missing": [s.id for s in mission.subtasks if s.status != "done"],
        }
        event = self.bus.append(
            sender="mission-manager",
            kind=SYNTHESIS,
            content={"mission_id": mission.mission_id, "synthesis": mission.synthesis},
            mission_id=mission.mission_id,
            est_tokens=est_tokens,
        )
        if GATE_AFTER_REVIEW in mission.hitl_gates:
            self._gate_pending(mission, GATE_AFTER_REVIEW, {"synthesis": mission.synthesis})
            raise MissionError(f"mission {mission_id} suspended on after_review gate")
        self._transition(mission, SYNTHESIZING)
        self._terminate_locked(mission, reason="complete")
        mission.synthesis["seq"] = event.seq
        return mission.synthesis

    @staticmethod
    def _detect_conflicts(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Heuristic contradiction scan: explicit CONTRADICTS markers between results."""
        conflicts: list[dict[str, Any]] = []
        texts = [(c["subtask_id"], str(c["result"] or "")) for c in candidates]
        for i, (sid_a, text_a) in enumerate(texts):
            for sid_b, text_b in texts[i + 1:]:
                marker = f"CONTRADICTS:{sid_b}" in text_a or f"CONTRADICTS:{sid_a}" in text_b
                contra = marker or ("contradicts" in text_a.lower() and sid_b in text_a) or ("contradicts" in text_b.lower() and sid_a in text_b)
                if contra:
                    agents = [c["agent"] for c in candidates if c["subtask_id"] in (sid_a, sid_b)]
                    conflicts.append(
                        {
                            "claim": f"{sid_a} vs {sid_b}",
                            "agents": agents,
                            "evidence": [{sid_a: text_a[:500]}, {sid_b: text_b[:500]}],
                        }
                    )
        return conflicts

    def _resolve(
        self,
        mission: Mission,
        candidates: list[dict[str, Any]],
        conflicts: list[dict[str, Any]],
        *,
        judge: Judge | None,
        reliability: dict[str, float],
    ) -> Any:
        if not candidates:
            return None
        policy = mission.resolution_policy
        if policy == POLICY_HIGHEST_RELIABILITY and reliability:
            best = max(candidates, key=lambda c: float(reliability.get(c["agent"] or "", 0.0)))
            return best["result"]
        if policy == POLICY_JUDGE_MODEL:
            if judge is None:
                raise MissionError("judge_model policy requires a judge callable")
            return judge(mission.goal, candidates)
        merged = "\n\n".join(f"[{c['subtask_id']}] {c['result']}" for c in candidates)
        if conflicts:
            merged += "\n\nUnresolved conflicts: " + "; ".join(c["claim"] for c in conflicts)
        return merged


    # ---------- HITL gates ----------

    def suspend(self, mission_id: str, gate: str, payload: dict[str, Any] | None = None) -> None:
        """Suspend the mission on a gate (interrupt-style, resume is replay)."""
        mission = self.get(mission_id)
        if gate not in HITL_GATES:
            raise MissionError(f"unknown gate {gate!r}")
        if mission.state in (DONE, FAILED):
            raise MissionError(f"cannot suspend a {mission.state} mission")
        if mission.state != SUSPENDED:
            self._gate_pending(mission, gate, payload or {})

    def resolve_gate(self, mission_id: str, gate: str, *, action: str, edits: dict[str, Any] | None = None) -> Mission:
        """Resolve a pending gate: approve | edit (apply edits) | reject (fail)."""
        mission = self.get(mission_id)
        if action not in ("approve", "edit", "reject"):
            raise MissionError(f"gate action must be approve|edit|reject, got {action!r}")
        pending = [g for g in mission.gates_pending if g["gate"] == gate]
        if not pending:
            raise MissionError(f"no pending {gate} gate on mission {mission_id}")
        self.bus.append(
            sender="user",
            kind=GATE_RESOLVED,
            content={"mission_id": mission.mission_id, "gate": gate, "action": action, "edits": edits or {}},
            mission_id=mission.mission_id,
            est_tokens=24,
        )
        mission.gates_pending = [g for g in mission.gates_pending if g["gate"] != gate]
        if action == "reject":
            self._terminate_locked(mission, reason=f"gate_rejected:{gate}")
            return mission
        if action == "edit" and edits and isinstance(edits.get("subtasks"), list):
            base = len(mission.subtasks)
            for i, text in enumerate(edits["subtasks"]):
                mission.subtasks.append(Subtask(id=f"s{base + i + 1}", text=str(text), status="open"))
            mission.claim_deadline = self._clock() + self.claim_timeout_s
        resume = mission.resume_state or CLAIMING
        if mission.state == SUSPENDED:
            previous = mission.state
            mission.state = resume
            self.bus.append(
                sender="mission-manager",
                kind=STATE_CHANGED,
                content={"scope": "mission", "mission_id": mission.mission_id, "from": previous, "to": resume},
                mission_id=mission.mission_id,
                est_tokens=16,
            )
        return mission

    # ---------- terminate ----------

    def terminate(self, mission_id: str, *, reason: str = "operator") -> dict[str, Any]:
        """Operator stop: always delivers results + missing manifest (never hangs)."""
        mission = self.get(mission_id)
        if mission.state in (DONE, FAILED):
            return mission.synthesis or self._partial_synthesis(mission)
        mission.missing = [s.id for s in mission.subtasks if s.status != "done"]
        mission.synthesis = self._partial_synthesis(mission)
        return self._terminate_locked(mission, reason=reason)


    # ---------- persistence: snapshot payload + replay ----------

    def snapshot_state(self) -> dict[str, Any]:
        """Pluggable payload for the bus snapshot: all missions, as dicts."""
        return {"missions": {mid: m.to_dict() for mid, m in self._missions.items()}}

    def restore_state(self, payload: dict[str, Any]) -> int:
        """Load missions from a snapshot payload; returns count restored."""
        missions = (payload or {}).get("missions") or {}
        count = 0
        for mid, data in missions.items():
            if isinstance(data, dict):
                self._missions[str(mid)] = Mission.from_dict(data)
                count += 1
        return count

    def rebuild_from_bus(self, bus: FleetBus | None = None) -> int:
        """Rebuild missions by replaying the mission event prefix (§7).

        Snapshot is the fast path; this is the truth path — any agent can
        resume coordination from the log alone after a coordinator dies.
        """
        source = bus or self.bus
        rebuilt: dict[str, Mission] = {}
        for event in source.tail():
            content = event.content if isinstance(event.content, dict) else {}
            mid = event.mission_id or content.get("mission_id")
            if not mid:
                continue
            mid = str(mid)
            mission = rebuilt.get(mid)
            if event.kind == MISSION_OPENED:
                mission = Mission(
                    mission_id=mid,
                    goal=str(content.get("goal") or ""),
                    budget_total=int(content.get("budget_total") or 0),
                )
                mission.opened_seq = event.seq
                rebuilt[mid] = mission
            elif mission is None:
                continue
            elif event.kind == PROPOSE:
                if content.get("event") == "replan":
                    for s in content.get("subtasks") or []:
                        if isinstance(s, dict):
                            mission.subtasks.append(Subtask.from_dict(s))
                    mission.replans = max(mission.replans, int(content.get("replan") or mission.replans))
                else:
                    subs = content.get("subtasks") or []
                    mission.subtasks = [
                        Subtask.from_dict(s) if isinstance(s, dict) else Subtask(id=str(s)) for s in subs
                    ]
                    if mission.state == PROPOSED:
                        mission.state = CLAIMING
            elif event.kind == CLAIM:
                sid = content.get("subtask_id")
                for sub in mission.subtasks:
                    if sub.id == sid and sub.status == "open":
                        # A CLAIM already on the log for the same subtask by a
                        # different agent wins by seq — this manager defers.
                        prior = self._claim_history(mission.mission_id, sid)
                        if any(agent != (sub.claimed_by or "") for agent, _ in prior):
                            continue
                        sub.status = "claimed"
                        sub.claimed_by = str(content.get("agent_id") or event.sender)
                        sub.lease_until = float(content.get("lease_until") or 0.0)
                        break
            elif event.kind == SUBTASK and content.get("event") == "lease_expired":
                for sub in mission.subtasks:
                    if sub.id == content.get("subtask_id") and sub.status == "claimed":
                        sub.status = "open"
                        sub.claimed_by = None
            elif event.kind == RESULT:
                for sub in mission.subtasks:
                    if sub.id == content.get("subtask_id"):
                        sub.status = "done"
                        sub.result = content.get("result")
                        mission.blackboard.append(
                            {"key": sub.id, "result": sub.result, "agent": str(content.get("agent_id") or event.sender)}
                        )
                        break
                if mission.state in (CLAIMING, WORKING):
                    mission.state = REVIEWING
            elif event.kind == REVIEW:
                for sub in mission.subtasks:
                    if sub.id == content.get("subtask_id"):
                        sub.reviews.append({"ok": content.get("ok"), "note": content.get("note") or content.get("critique")})
                        break
            elif event.kind == SYNTHESIS:
                mission.synthesis = content.get("synthesis")
                mission.state = SYNTHESIZING
            elif event.kind == MISSION_TERMINATED:
                mission.synthesis = content.get("synthesis")
                mission.terminate_reason = content.get("reason")
                mission.closed_seq = event.seq
                mission.state = DONE if mission.terminate_reason == "complete" else FAILED
            elif event.kind == STATE_CHANGED and content.get("scope") == "mission":
                target = str(content.get("to") or "")
                if target in (DONE, FAILED, SUSPENDED, WORKING, REVIEWING, CLAIMING, SYNTHESIZING):
                    mission.state = target
        self._missions.update(rebuilt)
        return len(rebuilt)

        return list(mission.subtasks)
