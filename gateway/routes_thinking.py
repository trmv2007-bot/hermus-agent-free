"""Thinking-layer endpoints — what the turn decided, and how it proved it.

* ``GET  /api/v1/thinking/routing``         the ledger's totals: tiers, over- and under-routing
* ``GET  /api/v1/thinking/routing/recent``  the last decisions with their reasons
* ``POST /api/v1/thinking/route``           classify a request without running it
* ``GET  /api/v1/thinking/last``            the most recent receipt and plan

Read-only except ``/thinking/route``, which is a pure classifier: it runs no
model and touches nothing. The point of the surface is that a person can see
*why* a turn went to a model instead of taking it on faith.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/thinking", tags=["thinking"])


@router.get("/routing")
async def routing_summary(limit: int = 500):
    """Totals over recent turns: which tier, how often the decision was wrong."""
    from core.thinking.routing import RoutingLedger

    ledger = RoutingLedger(limit=limit)
    return {"success": True, "summary": ledger.summary()}


@router.get("/routing/recent")
async def routing_recent(limit: int = 20):
    """The last decisions, each with the reason recorded at the time."""
    from core.thinking.routing import routing_ledger

    rows = routing_ledger.recent(limit)
    return {"success": True, "count": len(rows), "decisions": rows}


@router.post("/route")
async def route_preview(payload: dict[str, Any] | None = None):
    """Classify a request and say which tier it would use — without running it."""
    from core.thinking.routing import configured_hosted_refs, decide_route

    data = payload or {}
    text = str(data.get("text") or "")
    from core.config import config

    local = str(data.get("local_model") or config.model or "")
    decision = decide_route(
        text,
        local_model=local,
        hosted_models=configured_hosted_refs(),
        forced_tier=str(data.get("tier") or config.route_tier or "auto"),
    )
    return {"success": True, "decision": decision.to_dict()}


@router.get("/last")
async def last_turn():
    """The newest receipt in the routing ledger, with the plan it belonged to."""
    from core.thinking.routing import routing_ledger

    rows = routing_ledger.recent(1)
    if not rows:
        return {"success": True, "turn": None}
    return {"success": True, "turn": rows[-1]}
