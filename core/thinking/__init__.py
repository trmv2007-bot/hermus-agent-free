"""Thinking layer — routing, a visible plan, and verification with a receipt.

Three concerns, three modules, one entry point per turn:

* :mod:`core.thinking.routing` — decide, per turn, between the local model and a
  free hosted tier, record why, and score the decision afterwards (over-routing
  is waste, under-routing is incompetence).
* :mod:`core.thinking.plan` — turn a multi-step request into a short list the
  person can see before anything is touched, and advance it as steps verify.
* :mod:`core.thinking.receipt` — collect evidence for what a turn changed and
  refuse to call it done without one; a failure earns exactly one repair.

:class:`core.thinking.turn.TurnMind` composes them for ``core.runtime``.
"""

from __future__ import annotations

from .plan import PlanProgress, build_turn_plan, needs_plan
from .receipt import Evidence, Receipt, build_receipt
from .routing import RouteDecision, RoutingLedger, decide_route, routing_outcome
from .turn import TurnMind

__all__ = [
    "Evidence",
    "PlanProgress",
    "Receipt",
    "RouteDecision",
    "RoutingLedger",
    "TurnMind",
    "build_receipt",
    "build_turn_plan",
    "decide_route",
    "needs_plan",
    "routing_outcome",
]
