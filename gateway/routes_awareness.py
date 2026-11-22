"""Awareness API: what Hermus can see of this PC, and when it may speak.

Everything here is read-only or a settings write. The snapshot route is the same
object the chat turn renders into a prompt, so a person can see exactly what the
model was given — including the fields that are unavailable and why. The
suggestion routes are the one place a proactive item can be answered, and
accepting one hands a normal ``runtime.turn`` to the queue rather than acting
directly, so approval and red-line policy still apply.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/awareness", tags=["awareness"])


@router.get("/snapshot")
def awareness_snapshot(fresh: bool = False):
    from core.awareness import get_awareness

    try:
        return get_awareness().public_snapshot(fresh=bool(fresh))
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "awareness_unavailable", "message": str(exc)[:300], "paused": True},
        )


@router.get("/settings")
def awareness_settings():
    from core.awareness import get_awareness

    return {"settings": get_awareness().settings().to_dict()}


@router.put("/settings")
def awareness_update_settings(patch: dict[str, Any]):
    from core.awareness import get_awareness

    try:
        settings = get_awareness().update_settings(patch or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"settings": settings}


@router.get("/suggestions")
def awareness_suggestions():
    from core.attention import attention_gate

    return attention_gate().suggestions()


@router.post("/suggestions/{suggestion_id}")
def awareness_respond(suggestion_id: str, payload: dict[str, Any] | None = None):
    from core.attention import attention_gate

    action = str((payload or {}).get("action") or "accept")
    result = attention_gate().respond(suggestion_id, action)
    if not result.get("ok"):
        status = 404 if "unknown" in str(result.get("error", "")) else 400
        return JSONResponse(status_code=status, content={"ok": False, **result})
    return result


@router.get("/journal")
def awareness_journal():
    from core.awareness import get_awareness

    return get_awareness().journal.summary()


@router.delete("/journal")
def awareness_journal_clear():
    from core.awareness import get_awareness

    return get_awareness().journal.clear()


__all__ = ["router"]
