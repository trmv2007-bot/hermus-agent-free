"""Teach Mode HTTP transport."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/teach", tags=["teach"])


@router.get("")
async def teach_list(limit: int = 30):
    from core.teach_mode import teach_mode
    return {"sessions": teach_mode.list(limit=limit)}


@router.post("/start")
async def teach_start(payload: dict | None = None):
    payload = payload or {}
    from core.teach_mode import teach_mode
    try:
        return {"session": teach_mode.start(
            str(payload.get("goal") or ""),
            run_id=str(payload.get("run_id") or "") or None,
            user_id=str(payload.get("user_id") or "default"),
            project=str(payload.get("project") or "") or None,
        )}
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@router.post("/{session_id}/attach")
async def teach_attach(session_id: str, payload: dict | None = None):
    payload = payload or {}
    from core.teach_mode import teach_mode
    try:
        return {"session": teach_mode.attach(session_id, str(payload.get("run_id") or ""))}
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@router.get("/{session_id}/preview")
async def teach_preview(session_id: str):
    from core.teach_mode import teach_mode
    try:
        return teach_mode.preview(session_id)
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=404)


@router.post("/{session_id}/harvest")
async def teach_harvest(session_id: str, payload: dict | None = None):
    payload = payload or {}
    from core.teach_mode import teach_mode
    try:
        return await __import__("asyncio").to_thread(
            teach_mode.harvest,
            session_id,
            dry_run=bool(payload.get("dry_run", False)),
        )
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=404)


__all__ = ["router"]
