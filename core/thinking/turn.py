"""One turn's thinking, composed: route it, show the plan, verify the result.

``core.runtime`` owns the turn path, so this is where the three pieces meet:

* before the first model call, :meth:`TurnMind.prepare` records the routing
  decision (and swaps the model when it says so) and, for work with more than
  one action, emits the plan that will be followed;
* during the turn, :meth:`TurnMind.wrap` watches tool results and advances the
  plan, so its steps verify as they happen rather than being retold afterwards;
* after the turn, :meth:`TurnMind.finish` builds the receipt, records the
  routing outcome, and decides whether the single repair attempt is owed.

The object is deliberately small and synchronous. A fake agent is enough to
drive it, which is how the logic is tested without a model.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .plan import PlanProgress, build_turn_plan, needs_plan
from .receipt import Receipt, build_receipt, repair_instruction
from .routing import (
    TIER_FREE_HOSTED,
    TIER_LOCAL,
    TIER_UNAVAILABLE,
    RouteDecision,
    answering_hosted_ref,
    configured_hosted_refs,
    decide_route,
    routing_ledger,
    routing_outcome,
    tier_of,
)


class TurnMind:
    """Routing, plan and receipt for a single chat turn."""

    def __init__(
        self,
        agent: Any,
        text: str,
        *,
        mode: str = "agent",
        session_id: str = "",
        base_dir: Path | None = None,
        emit: Callable[[str, dict], None] | None = None,
        enabled: bool = True,
        with_plan: bool = True,
    ):
        self.agent = agent
        self.text = str(text or "")
        self.mode = str(mode or "agent")
        self.session_id = str(session_id or "")
        self.base_dir = Path(base_dir) if base_dir is not None else Path.cwd()
        self._emit = emit or (lambda _t, _d: None)
        self.enabled = bool(enabled)
        self.with_plan = bool(with_plan)

        self.decision: RouteDecision | None = None
        self.plan_progress: PlanProgress | None = None
        self.receipt: Receipt | None = None
        self.outcome: dict[str, Any] | None = None
        self.attempt = 0

    # ------------------------------------------------------------- prepare

    def prepare(self) -> None:
        """Plan first (visible), then route. Both are zero-token and deterministic."""
        if not self.enabled:
            return
        if self.with_plan:
            self._prepare_plan()
        self._prepare_route()

    def _prepare_plan(self) -> None:
        try:
            if not needs_plan(self.text, mode=self.mode):
                return
            from ..reasoning.governor import governor

            difficulty = governor.classify_difficulty(self.text)
            if getattr(self.agent, "plan_override", None) is not None:
                plan = self.agent.plan_override
            else:
                plan = build_turn_plan(self.text, session_id=self.session_id, difficulty=difficulty)
                self.agent.plan_override = plan
            self.plan_progress = PlanProgress(plan)
            self._emit(
                "plan_ready",
                {"goal": plan.goal, "steps": self.plan_progress.render(), "difficulty": difficulty},
            )
        except Exception:
            # A plan is a courtesy; never let it block the turn.
            self.plan_progress = None

    def _prepare_route(self) -> None:
        try:
            local_model = self._current_model()
            hosted = configured_hosted_refs()
            forced = None
            try:
                from ..config import config

                forced = str(getattr(config, "route_tier", "auto") or "auto")
                enabled = bool(getattr(config, "thinking_routing_enabled", True))
            except Exception:
                enabled = True
            if not enabled:
                return
            self.decision = decide_route(
                self.text,
                local_model=local_model,
                hosted_models=hosted,
                forced_tier=forced,
            )
            if self.decision.tier == TIER_FREE_HOSTED:
                self._confirm_hosted_model()
            # The decision is the turn's, not a suggestion: tell the agent's own
            # router to stand down so two selectors cannot fight over the model.
            try:
                self.agent._thinking_route = self.decision.to_dict()
            except Exception:
                pass
            if self.decision.tier == TIER_FREE_HOSTED and self.decision.model != local_model:
                self._swap_model(self.decision.model)
            self._emit("routing_decision", self.decision.to_dict())
        except Exception:
            self.decision = None

    def _confirm_hosted_model(self) -> None:
        """Point the escalated turn at a model this key answers, or stay home.

        Routing's decision names the catalog's ranking of the free tier. That
        ranking is not a measurement: a free plan can rate-limit the top of it,
        and a turn sent there comes back with the provider's error as its answer.
        So the model is confirmed against the availability probe first; when
        nothing on the tier answers, the turn stays on the local model and the
        reason says why rather than spending a request to fail.
        """
        if self.decision is None:
            return
        if self.decision.model == self._current_model():
            # Already on the model the turn will call: asking the provider whether
            # it answers would spend the very request the turn is about to make.
            return
        chosen = answering_hosted_ref(self.decision.model)
        if chosen == self.decision.model:
            return
        if chosen:
            self._emit("routing_repick", {"from": self.decision.model, "to": chosen})
            self.decision.reason += f"; {self.decision.model} did not answer for this key, so {chosen} does"
            self.decision.model = chosen
            return
        unmeasured, self.decision.model = self.decision.model, ""
        self.decision.hosted_models = []
        if tier_of(self.decision.local_model) == TIER_LOCAL:
            self.decision.tier = TIER_LOCAL
            self.decision.model = self.decision.local_model
            self.decision.reason += "; no model on the free tier answers this key, so the turn stays local"
        else:
            self.decision.tier = TIER_UNAVAILABLE
            self.decision.reason += "; no model on the free tier answers this key and no local model is bound"
        self._emit(
            "routing_repick",
            {"from": unmeasured, "to": self.decision.model, "reason": self.decision.reason},
        )

    def _current_model(self) -> str:
        try:
            provider = str(getattr(getattr(self.agent, "llm", None), "provider", "") or "")
            name = str(getattr(self.agent, "model_name", "") or "")
            if "/" in name:
                return name
            return f"{provider}/{name}" if provider and name else name
        except Exception:
            return ""

    def _swap_model(self, ref: str) -> bool:
        try:
            from ..models import get_model_gateway

            self.agent.llm = get_model_gateway().llm(model=ref)
            self.agent.model_name = ref
            return True
        except Exception as exc:
            self._emit("routing_failed", {"model": ref, "error": f"{type(exc).__name__}: {exc}"})
            return False

    # ------------------------------------------------------------ observe

    def wrap(self, on_event: Callable[[str, dict], None] | None) -> Callable[[str, dict], None] | None:
        """Intercept the agent's events to keep the plan current as steps verify."""
        if not self.enabled or self.plan_progress is None:
            return on_event

        def observer(event_type: str, data: dict | None = None) -> None:
            data = data or {}
            if on_event is not None:
                on_event(event_type, data)
            if event_type != "tool_result":
                return
            ok = not data.get("error")
            try:
                step_event = self.plan_progress.observe(
                    str(data.get("tool") or ""),
                    ok,
                    str(data.get("preview") or "")[:120] if ok else str(data.get("preview") or "")[:120],
                )
            except Exception:
                step_event = None
            if step_event:
                self._emit("plan_step", step_event)

        return observer

    # ------------------------------------------------------------- finish

    def finish(self, result: dict[str, Any], *, attempt: int = 0) -> Receipt:
        """Build the receipt, record the outcome, and say whether a repair is owed."""
        self.attempt = int(attempt)
        tool_results = list(result.get("tool_results") or [])
        answer = str(result.get("response") or "")
        try:
            self.receipt = build_receipt(
                self.text,
                tool_results,
                answer,
                base_dir=self.base_dir,
                attempt=attempt,
                expects_change=self._wants_change(),
            )
        except Exception as exc:
            self.receipt = Receipt(
                verified=False,
                verdict="verifier_error",
                claim="the receipt could not be built",
                problems=[f"{type(exc).__name__}: {exc}"],
            )

        if attempt > 0 and self.receipt.verdict in ("not_applicable", "refused"):
            # A repair was owed and the retry still changed nothing. The answer
            # may be honest ("I cannot"), but the request is unmet — say so.
            self.receipt.verified = False
            self.receipt.verdict = "unresolved"
            self.receipt.problems = self.receipt.problems or ["after the repair attempt, nothing had changed"]

        steps = int(result.get("steps") or 0)
        failed_calls = sum(
            1 for tr in tool_results if isinstance(tr, dict) and isinstance(tr.get("result"), dict) and "error" in tr["result"]
        )
        if self.decision is not None:
            self.outcome = routing_outcome(
                self.decision,
                steps=steps,
                failed_calls=failed_calls,
                verified=self.receipt.verified,
            )
            try:
                routing_ledger.record(
                    self.decision,
                    self.outcome,
                    session_id=self.session_id or None,
                    mode=self.mode,
                    receipt=self._receipt_row(),
                )
            except Exception:
                pass
            self._emit("routing_outcome", self.outcome)

        result["receipt"] = self.receipt.to_dict()
        if self.decision is not None:
            routed = self.decision.to_dict()
            routed["outcome"] = self.outcome
            result["routing"] = routed
        if self.plan_progress is not None:
            rendered = self.plan_progress.settle()
            result["plan"] = {"goal": self.plan_progress.plan.goal, "steps": rendered}
            self._emit("plan_finished", {"steps": rendered})
        self._emit("receipt", self.receipt.to_dict())
        return self.receipt

    def _wants_change(self) -> bool:
        """Did the request ask for a change? That is what makes an empty turn unmet."""
        try:
            from ..runtime import INTENT_ACTION, detect_intent

            return detect_intent(self.text) == INTENT_ACTION
        except Exception:
            return needs_plan(self.text, mode=self.mode)

    def _receipt_row(self) -> dict[str, Any]:
        """The receipt as it goes to the ledger: bounded, and enough to audit."""
        if self.receipt is None:
            return {}
        return {
            "verified": self.receipt.verified,
            "verdict": self.receipt.verdict,
            "claim": self.receipt.claim,
            "problems": list(self.receipt.problems)[:5],
            "evidence": [e.detail for e in self.receipt.evidence][:5],
            "repaired": self.attempt > 0,
        }

    # ------------------------------------------------------------- repair

    def repair_prompt(self) -> str:
        receipt = self.receipt or build_receipt(self.text, [], "")
        return repair_instruction(receipt, self.text)

    def should_repair(self) -> bool:
        return bool(self.receipt and self.receipt.repair_required)

    def escalate_for_repair(self) -> bool:
        """Under-routed local turns retry on the free tier, not on the same model."""
        if self.decision is None or self.receipt is None:
            return False
        if self.decision.tier != TIER_LOCAL or not (self.outcome or {}).get("under_routed"):
            return False
        hosted = configured_hosted_refs()
        if not hosted:
            return False
        # The repair is only worth a request on a model the key actually answers.
        target = answering_hosted_ref(hosted[0]) or ""
        if not target:
            self._emit("routing_escalation_skipped", {"reason": "no model on the free tier answers this key"})
            return False
        if self._swap_model(target):
            self._emit(
                "routing_escalated",
                {"from": self.decision.model, "to": target, "reason": self.outcome.get("note")},
            )
            return True
        return False

    def unverified_line(self) -> str:
        """The one line appended when a turn cannot prove itself."""
        if self.receipt is None:
            return ""
        if self.receipt.verified:
            return ""
        problems = "; ".join(self.receipt.problems) or self.receipt.claim
        return f"\n\nNot verified: {problems}"


def evidence_line(receipt: Receipt) -> str:
    """A one-line citation for a verified turn: what was checked, and where."""
    if not receipt.verified or not receipt.evidence:
        return ""
    first = receipt.evidence[0]
    return f"\n\nVerified: {first.detail}"
