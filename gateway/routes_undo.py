"""Undo and preview endpoints — the product's take-it-back and look-first surface.

* ``GET  /api/v1/undo``             the journal, newest first
* ``POST /api/v1/undo/{id}``        put one action back, verifying nothing moved since
* ``POST /api/v1/undo/last``        the same, for the most recent undoable action
* ``POST /api/v1/preview``          describe what an action would do, and mint its one-keystroke token
* ``POST /api/v1/preview/{token}``  act on what was shown, through the journaled tool path

Undo is deliberately not gated by the write policy: refusing to hand a person
back their own prior bytes would be the bigger failure. The *act* behind a
preview token runs through ``tool_registry.execute``, which keeps the permission
gate, the preview gate and the undo journal in the same path as every other tool.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1", tags=["undo"])


@router.get("/undo")
async def undo_list(limit: int = 20):
    """Journaled actions, newest first."""
    from core.undo import get_undo_journal

    journal = get_undo_journal()
    entries = journal.list(limit)
    return {
        "success": True,
        "count": len(entries),
        "entries": entries,
        "undoable": sum(1 for entry in entries if entry["reversible"] and entry["state"] == "applied"),
    }


@router.post("/undo/last")
async def undo_last(payload: dict | None = None):
    """Undo the most recent undoable action."""
    from core.undo import get_undo_journal

    force = bool((payload or {}).get("force"))
    result = get_undo_journal().undo_last(force=force)
    return result


@router.post("/undo/{entry_id}")
async def undo_one(entry_id: str, payload: dict | None = None):
    """Undo one journaled action by id."""
    from core.undo import get_undo_journal

    force = bool((payload or {}).get("force"))
    result = get_undo_journal().undo(entry_id, force=force)
    if not result.get("success") and "no undo entry" in str(result.get("error", "")):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/preview")
async def preview_action(payload: dict):
    """Describe an action and issue the token that lets it act."""
    from core.dry_run import preview

    tool = str(payload.get("tool") or "").strip()
    if not tool:
        raise HTTPException(status_code=400, detail="tool is required")
    args = payload.get("args") or {}
    if not isinstance(args, dict):
        raise HTTPException(status_code=400, detail="args must be an object")
    result = preview(tool, args)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/preview/{token}")
async def confirm_action(token: str):
    """Act on a previewed action — the one keystroke, through the journaled path."""
    from core.dry_run import confirm

    result = confirm(token)
    if not result.get("ok") and str(result.get("error", "")).startswith("unknown or expired"):
        raise HTTPException(status_code=409, detail=result["error"])
    return result
