"""Unified interaction API for the HERMUS Nexus client.

These routes intentionally adapt existing queue/run/event subsystems instead of
creating parallel execution paths. Legacy endpoints remain available.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.orchestrator import orchestrator
from core.run_events import run_bus

router = APIRouter(prefix="/api/nexus", tags=["nexus"])


@router.get("/state")
async def nexus_state(user_id: str = "default"):
    return orchestrator.state(user_id=user_id)


@router.post("/command")
async def nexus_command(payload: dict[str, Any] | None = None):
    payload = payload or {}
    result = orchestrator.submit(
        str(payload.get("text") or ""),
        user_id=str(payload.get("user_id") or "default"),
        session_id=str(payload.get("session_id") or "") or None,
        mode=str(payload.get("mode") or "chat"),
        prefer=str(payload.get("prefer") or "") or None,
        priority=int(payload.get("priority") or 0),
    )
    body = result.to_dict()
    return JSONResponse(body, status_code=202 if result.accepted else 400)


@router.post("/runs/{run_id}/cancel")
async def nexus_cancel(run_id: str):
    ok = orchestrator.cancel(run_id)
    return JSONResponse({"ok": ok, "run_id": run_id}, status_code=200 if ok else 404)


@router.post("/runs/{run_id}/steer")
async def nexus_steer(run_id: str, payload: dict[str, Any] | None = None):
    payload = payload or {}
    text = str(payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "text is required"}, status_code=400)
    ok = orchestrator.steer(run_id, text)
    return JSONResponse({"ok": ok, "run_id": run_id}, status_code=200 if ok else 404)


@router.get("/runs/{run_id}")
async def nexus_run(run_id: str, after: int = 0):
    run = run_bus.get(run_id)
    if run is None:
        return JSONResponse({"error": "run not found", "run_id": run_id}, status_code=404)
    return {
        "run": run.to_dict(),
        "events": run_bus.history(run_id, after=after),
    }
