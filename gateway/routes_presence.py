"""Presence and continuity API for the Hermus control room.

These routes expose Hermus' stable identity, current operational state, ongoing
user-approved goals and local continuity moments. They do not create a second
execution path: an explicit check-in is submitted as the normal ``runtime.turn``
queue job and remains subject to the same model, permission and cancellation
rules as every other turn.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/presence", tags=["presence"])


@router.get("")
@router.get("/")
async def presence_snapshot(user_id: str | None = None):
    from core.presence import get_presence

    return get_presence().snapshot(user_id=user_id)


@router.get("/status")
async def presence_status(user_id: str | None = None):
    from core.presence import get_presence

    return get_presence().snapshot(user_id=user_id)


@router.get("/identity")
async def presence_identity():
    from core.presence import get_presence

    return {"identity": get_presence().identity()}


@router.put("/identity")
@router.post("/identity")
async def presence_identity_update(payload: dict[str, Any] | None = None):
    from core.presence import get_presence

    payload = payload or {}
    values = payload.get("values")
    if isinstance(values, str):
        values = [item.strip() for item in values.split(",") if item.strip()]
    if values is not None and not isinstance(values, list):
        return JSONResponse({"success": False, "error": "values must be a list or comma-separated string"}, status_code=400)
    identity = get_presence().update_identity(
        name=payload.get("name"),
        role=payload.get("role"),
        tone=payload.get("tone"),
        values=values,
        greeting=payload.get("greeting"),
    )
    return {"success": True, "identity": identity}


@router.get("/goals")
async def presence_goals(status: str | None = None, user_id: str | None = None):
    from core.presence import get_presence

    return {"goals": get_presence().list_goals(status=status, user_id=user_id)}


@router.post("/goals")
async def presence_goal_add(payload: dict[str, Any] | None = None):
    from core.presence import get_presence

    payload = payload or {}
    result = get_presence().add_goal(
        str(payload.get("title") or payload.get("goal") or ""),
        priority=payload.get("priority", 3),
        due_at=payload.get("due_at"),
        source=str(payload.get("source") or "user"),
        notes=str(payload.get("notes") or ""),
        user_id=str(payload.get("user_id") or "default"),
    )
    if not result.get("success"):
        return JSONResponse(result, status_code=400)
    return result


@router.post("/goals/{goal_id}/complete")
async def presence_goal_complete(goal_id: str, payload: dict[str, Any] | None = None):
    from core.presence import get_presence

    result = get_presence().complete_goal(goal_id, note=str((payload or {}).get("note") or ""))
    if not result.get("success"):
        return JSONResponse(result, status_code=404)
    return result


@router.post("/goals/{goal_id}/touch")
async def presence_goal_touch(goal_id: str):
    from core.presence import get_presence

    result = get_presence().touch_goal(goal_id)
    if not result.get("success"):
        return JSONResponse(result, status_code=404)
    return result


@router.post("/heartbeat")
async def presence_heartbeat():
    from core.presence import get_presence

    return get_presence().heartbeat(force_event=True)


@router.post("/check-in")
async def presence_check_in(payload: dict[str, Any] | None = None):
    """Queue a safe, read-only conversational check-in for one ongoing goal.

    The endpoint is explicit rather than automatic. It asks Hermus for a status
    update and tells it to ask before taking action; it never grants permissions
    and never starts a mission directly.
    """
    from core.presence import get_presence
    from gateway.queue import job_queue

    payload = payload or {}
    manager = get_presence()
    goal_id = str(payload.get("goal_id") or "").strip()
    user_id = str(payload.get("user_id") or "default")
    goals = manager.list_goals(status="active", user_id=user_id)
    selected = next((g for g in goals if g.get("id") == goal_id), None) if goal_id else None
    if selected is None:
        due = manager.check_ins_due(user_id=user_id)
        if due:
            selected = next((g for g in goals if g.get("id") == due[0].get("id")), None)
    if selected is None and not str(payload.get("text") or "").strip():
        return {"queued": False, "reason": "no active goal is due for a check-in", "check_ins_due": manager.check_ins_due()}

    title = str((selected or {}).get("title") or "your ongoing work")
    text = str(payload.get("text") or (
        f"Check in on the ongoing goal: {title}. Give me a concise status update based on "
        "what you actually know. Do not take external or destructive action; ask me first "
        "if anything needs approval."
    ))
    if not getattr(job_queue, "enabled", False) or not getattr(job_queue, "_started", False):
        return JSONResponse({
            "queued": False,
            "error": "job queue is not running; start the gateway queue before requesting a check-in",
        }, status_code=503)

    platform = str(payload.get("platform") or "dashboard")
    run_id = str(payload.get("run_id") or f"run_presence_{uuid.uuid4().hex[:8]}")
    job = job_queue.submit(
        "runtime.turn",
        {
            "text": text,
            "platform": platform,
            "user_id": user_id,
            "model": payload.get("model"),
            # Chat mode has no system tools; read_only also strips custom APIs
            # for this one cached-agent turn, even when wording is custom.
            "mode": "chat",
            "prefer": "chat",
            "read_only": True,
            "stream": bool(payload.get("stream", True)),
        },
        session_key=f"{platform}:{user_id}",
        timeout=payload.get("timeout"),
        run_id=run_id,
    )
    if selected:
        manager.mark_checkin(str(selected.get("id")))
        manager.record_moment(
            "checkin_requested",
            f"Requested a status check on {title}",
            run_id=job.run_id,
            metadata={"goal_id": selected.get("id")},
            emit=False,
        )
    return {
        "queued": True,
        "job_id": job.id,
        "run_id": job.run_id,
        "goal": selected,
        "status_url": f"/jobs/{job.id}",
        "events_url": f"/jobs/{job.id}/events",
        "result_url": f"/jobs/{job.id}/result",
    }


__all__ = ["router"]
