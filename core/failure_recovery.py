"""Failure diagnosis: why a mission did not work, and what is worth doing about it.

The engine's repair loop used to have one response to every failure — reset the
whole DAG and run it again — and one bound on it, a repair count. That conflates
"a key is rate limited" (wait or switch key, keep every completed node) with
"the plan was wrong" (replan) and "the file the worker claimed does not exist"
(re-run only what produced it), and it can spend the whole budget repeating an
action that already failed once for the same reason.

Classification is structured-code first. Free-text matching exists, but it is
labelled as such so a reader knows how confident the diagnosis is.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from .contracts import FailureClass


class RecoveryAction(str, Enum):
    RETRY_NODE = "retry_node"  # same node, same model — worth it only once
    ALTERNATE_KEY = "alternate_key"  # quota/auth on this credential, not this task
    ALTERNATE_MODEL = "alternate_model"  # model refused / cannot do the job
    TOOL_REPAIR = "tool_repair"  # tool args or availability, worker is fine
    PLAN_REPAIR = "plan_repair"  # re-run the nodes that produced the bad outcome
    REPLAN = "replan"  # the decomposition itself is wrong
    RESUME_CHECKPOINT = "resume_checkpoint"  # state is fine, the process was not
    ESCALATE_HITL = "escalate_hitl"  # needs a human decision, not another attempt
    ABORT = "abort"  # bounded: stop spending the budget


#: How many times one action may be tried per mission before it is spent. A
#: rate limit that never clears should not be retried until the repair count runs
#: out — the loop should end when the *strategy* is exhausted, not the counter.
ACTION_LIMITS: dict[RecoveryAction, int] = {
    RecoveryAction.RETRY_NODE: 1,
    RecoveryAction.ALTERNATE_KEY: 2,
    RecoveryAction.ALTERNATE_MODEL: 2,
    RecoveryAction.TOOL_REPAIR: 2,
    RecoveryAction.PLAN_REPAIR: 2,
    RecoveryAction.REPLAN: 1,
    RecoveryAction.RESUME_CHECKPOINT: 1,
    RecoveryAction.ESCALATE_HITL: 1,
    RecoveryAction.ABORT: 1,
}

#: Where each failure class is worth aiming first. Ordered: the engine takes the
#: highest-ranked action still under its limit.
PLAYBOOK: dict[str, list[RecoveryAction]] = {
    FailureClass.RATE_LIMIT.value: [RecoveryAction.ALTERNATE_KEY, RecoveryAction.RETRY_NODE, RecoveryAction.ESCALATE_HITL],
    FailureClass.AUTH.value: [RecoveryAction.ALTERNATE_KEY, RecoveryAction.ESCALATE_HITL],
    FailureClass.NETWORK.value: [RecoveryAction.RETRY_NODE, RecoveryAction.ALTERNATE_MODEL, RecoveryAction.ESCALATE_HITL],
    FailureClass.PROVIDER_UNAVAILABLE.value: [RecoveryAction.ALTERNATE_MODEL, RecoveryAction.ALTERNATE_KEY, RecoveryAction.ESCALATE_HITL],
    FailureClass.MODEL_UNAVAILABLE.value: [RecoveryAction.ALTERNATE_MODEL, RecoveryAction.ESCALATE_HITL],
    FailureClass.INVALID_MODEL.value: [RecoveryAction.ALTERNATE_MODEL, RecoveryAction.REPLAN],
    FailureClass.CAPABILITY_MISMATCH.value: [RecoveryAction.ALTERNATE_MODEL, RecoveryAction.REPLAN],
    FailureClass.CONTEXT_OVERFLOW.value: [RecoveryAction.PLAN_REPAIR, RecoveryAction.REPLAN],
    FailureClass.TIMEOUT.value: [RecoveryAction.RETRY_NODE, RecoveryAction.PLAN_REPAIR, RecoveryAction.ALTERNATE_MODEL],
    FailureClass.TOOL_UNSUPPORTED.value: [RecoveryAction.TOOL_REPAIR, RecoveryAction.ALTERNATE_MODEL],
    FailureClass.POLICY_DENIED.value: [RecoveryAction.ESCALATE_HITL],
    # A deliverable that is not on disk is the worker's claim failing, not a
    # transport problem: re-run the work that should have produced it.
    "missing_artifact": [RecoveryAction.PLAN_REPAIR, RecoveryAction.RETRY_NODE, RecoveryAction.REPLAN],
    # A requirement whose own check failed: fix the work that answers it, not the
    # whole plan, unless that has already been tried.
    "requirement_breach": [RecoveryAction.PLAN_REPAIR, RecoveryAction.REPLAN, RecoveryAction.ESCALATE_HITL],
    "not_verified": [RecoveryAction.PLAN_REPAIR, RecoveryAction.REPLAN, RecoveryAction.ESCALATE_HITL],
    FailureClass.UNKNOWN.value: [RecoveryAction.PLAN_REPAIR, RecoveryAction.RETRY_NODE, RecoveryAction.ESCALATE_HITL],
}

_CODE_MARKERS = {
    FailureClass.RATE_LIMIT.value: ("429", "rate limit", "ratelimit", "quota", "too many requests"),
    FailureClass.AUTH.value: ("401", "403", "unauthorized", "invalid api key", "authentication", "no healthy keys"),
    FailureClass.TIMEOUT.value: ("timeout", "timed out", "deadline exceeded"),
    FailureClass.CONTEXT_OVERFLOW.value: ("context length", "maximum context", "too many tokens", "context_overflow"),
    FailureClass.NETWORK.value: ("getaddrinfo", "connection refused", "network is unreachable", "name resolution"),
    FailureClass.TOOL_UNSUPPORTED.value: ("unknown tool", "not supported", "unsupported tool"),
    FailureClass.POLICY_DENIED.value: ("permission denied", "policy denied", "blocked by policy", "red line"),
}

#: Structured codes the runtime already emits, mapped to a failure class. These
#: beat any text match because they are set by the component that knows.
_STRUCTURAL_CODES = {
    "approval_required": FailureClass.POLICY_DENIED.value,
    "no_model_backend": FailureClass.PROVIDER_UNAVAILABLE.value,
    "rate_limited": FailureClass.RATE_LIMIT.value,
    "authentication_failed": FailureClass.AUTH.value,
    "provider_unavailable": FailureClass.PROVIDER_UNAVAILABLE.value,
    "model_unavailable": FailureClass.MODEL_UNAVAILABLE.value,
    "capability_mismatch": FailureClass.CAPABILITY_MISMATCH.value,
    "context_overflow": FailureClass.CONTEXT_OVERFLOW.value,
    "timeout": FailureClass.TIMEOUT.value,
    "tool_unsupported": FailureClass.TOOL_UNSUPPORTED.value,
}


class Diagnosis(dict):
    """A dict with named access, so the repair history stays JSON-durable."""

    @property
    def failure_class(self) -> str:
        return str(self.get("failure_class") or FailureClass.UNKNOWN.value)

    @property
    def actions(self) -> list[RecoveryAction]:
        """Typed actions; the stored dict keeps plain strings so it is JSON-durable.

        The dict key is the same name the repair history uses, so
        :func:`count_attempts` actually sees the rounds it is meant to bound.
        """
        return [RecoveryAction(a) for a in (self.get("recovery_actions") or [])]


def classify(
    *,
    error_code: str | None = None,
    errors: list[str] | None = None,
    missing_artifacts: list[str] | None = None,
    breached_requirements: list[str] | None = None,
    verified: bool = False,
    cancelled: bool = False,
) -> str:
    """One failure class for a mission round, structured signals first."""
    if cancelled:
        return "cancelled"
    code = str(error_code or "").strip().lower()
    if code in _STRUCTURAL_CODES:
        return _STRUCTURAL_CODES[code]
    if breached_requirements:
        return "requirement_breach"
    if missing_artifacts:
        return "missing_artifact"
    haystack = " ".join(str(e).lower() for e in (errors or []))
    for candidate, markers in _CODE_MARKERS.items():
        if any(marker in haystack for marker in markers):
            return candidate
    if not verified:
        return "not_verified"
    return FailureClass.UNKNOWN.value


def plan(
    failure_class: str,
    *,
    attempts: dict[str, int] | None = None,
    max_actions: int = 3,
) -> list[RecoveryAction]:
    """Ranked recovery actions still under their own attempt limits.

    An exhausted strategy is dropped rather than repeated, so a mission runs out
    of distinct approaches before it runs out of repair budget — and a caller can
    see that it ran out, instead of watching the same retry loop.
    """
    used = attempts or {}
    out: list[RecoveryAction] = []
    for action in PLAYBOOK.get(str(failure_class), PLAYBOOK[FailureClass.UNKNOWN.value]):
        if int(used.get(str(action.value), 0)) >= ACTION_LIMITS.get(action, 1):
            continue
        out.append(action)
        if len(out) >= max_actions:
            break
    return out or [RecoveryAction.ABORT]


def diagnose(
    *,
    error_code: str | None = None,
    errors: list[str] | None = None,
    missing_artifacts: list[str] | None = None,
    breached_requirements: list[str] | None = None,
    verified: bool = False,
    cancelled: bool = False,
    attempts: dict[str, int] | None = None,
    evidence_refs: list[str] | None = None,
) -> Diagnosis:
    """Classify, choose actions, and say how sure the classifier is."""
    failure_class = classify(
        error_code=error_code,
        errors=errors,
        missing_artifacts=missing_artifacts,
        breached_requirements=breached_requirements,
        verified=verified,
        cancelled=cancelled,
    )
    structured = bool(error_code and str(error_code).strip().lower() in _STRUCTURAL_CODES)
    return Diagnosis(
        {
            "failure_class": failure_class,
            "recovery_actions": [action.value for action in plan(failure_class, attempts=attempts)],
            "basis": "structured_error_code" if structured else ("text_match" if errors else "outcome"),
            "errors": [str(e)[:200] for e in (errors or [])][:6],
            "missing_artifacts": [str(a) for a in (missing_artifacts or [])][:6],
            "breached_requirements": [str(r) for r in (breached_requirements or [])][:10],
            "evidence_refs": list(evidence_refs or [])[-6:],
        }
    )


def count_attempts(history: list[dict[str, Any]] | None) -> dict[str, int]:
    """Attempts already spent per action, read back out of the repair history."""
    counts: dict[str, int] = {}
    for round_entry in history or []:
        for action in (round_entry or {}).get("recovery_actions") or []:
            key = str(action)
            counts[key] = counts.get(key, 0) + 1
    return counts


def repeat_of_last(history: list[dict[str, Any]] | None, diagnosis: Diagnosis) -> bool:
    """True when this round would repeat the last one against the same errors.

    Identical class, identical actions and nothing new the verifier complained
    about means another pass is spending budget to learn nothing.
    """
    if not history:
        return False
    last = history[-1] or {}
    same = str(last.get("failure_class") or "") == diagnosis.failure_class and list(last.get("recovery_actions") or []) == [
        a.value for a in diagnosis.actions
    ]
    if not same:
        return False
    current = {re.sub(r"\s+", " ", str(e)).strip().lower()[:200] for e in (diagnosis.get("errors") or [])}
    previous = {re.sub(r"\s+", " ", str(e)).strip().lower()[:200] for e in (last.get("errors") or [])}
    return current == previous


def nodes_to_reset(dag: Any, diagnosis: Diagnosis) -> list[str] | None:
    """Which nodes a repair round should rewind, or None for the whole DAG.

    Transport failures (a key, a model, a network) say nothing about the plan, so
    completed work survives them. Only a plan-shaped failure justifies throwing
    the finished nodes away and redoing everything.
    """
    if diagnosis.failure_class in ("cancelled",):
        return []
    actions = {action.value for action in diagnosis.actions}
    if RecoveryAction.REPLAN.value in actions:
        return None
    if diagnosis.failure_class in (
        FailureClass.RATE_LIMIT.value,
        FailureClass.AUTH.value,
        FailureClass.NETWORK.value,
        FailureClass.PROVIDER_UNAVAILABLE.value,
        FailureClass.MODEL_UNAVAILABLE.value,
    ):
        return [
            node_id
            for node_id, node in getattr(dag, "nodes", {}).items()
            if str(getattr(node, "status", "")) in ("failed", "skipped", "blocked", "running")
        ]
    return [
        node_id
        for node_id, node in getattr(dag, "nodes", {}).items()
        if str(getattr(node, "status", "")) in ("failed", "skipped", "blocked", "running")
    ]
