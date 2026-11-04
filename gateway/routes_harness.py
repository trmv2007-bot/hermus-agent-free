"""Read-only harness API.

The turn harness owns session records, swarm mail and file-shift notices. It
had no HTTP surface at all, so the Control Room could only show a session list
through the console manifest. These endpoints are projections of
:mod:`core.harness.stats` — they read real files and real events, and every
route is read-only (no mutation endpoint exists here on purpose: the canonical
command path owns actions).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from core.harness import stats

router = APIRouter(prefix="/api/v1/harness", tags=["harness"])


@router.get("/overview")
async def harness_overview() -> dict[str, Any]:
    """Harness counts, pending notices, newest observed turn and the live budget."""
    return {"ok": True, **stats.overview()}


@router.get("/sessions")
async def harness_sessions(limit: int = 50) -> dict[str, Any]:
    return {"ok": True, **stats.sessions_view(limit)}


@router.get("/sessions/{session_id}")
async def harness_session(session_id: str) -> dict[str, Any]:
    view = stats.session_view(session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="unknown harness session")
    return {"ok": True, **view}


@router.get("/events")
async def harness_events(limit: int = 50) -> dict[str, Any]:
    return {"ok": True, **stats.events_view(limit)}
