"""User-friendly routine control surface over the existing proactive automation owner."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/routines", tags=["routines"])


def _automation():
    from core.proactive_runtime import automation, wire_proactive_automation

    wire_proactive_automation()
    return automation


@router.get("")
@router.get("/")
async def routine_list():
    return {"routines": _automation().list_rules()}


@router.post("")
async def routine_create(payload: dict | None = None):
    payload = payload or {}
    try:
        rule = _automation().add_rule(
            name=str(payload.get("name") or "routine"),
            event_type=str(payload.get("event_type") or ""),
            action_type=str(payload.get("action_type") or "runtime.turn"),
            task=str(payload.get("task") or ""),
            enabled=bool(payload.get("enabled", False)),
            cooldown_seconds=max(0.0, float(payload.get("cooldown_seconds", 60))),
            max_fires=(int(payload["max_fires"]) if payload.get("max_fires") not in (None, "") else None),
            filters=payload.get("filters") or {},
        )
        return {"success": True, "routine": rule.as_dict()}
    except (TypeError, ValueError) as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@router.post("/{routine_id}/enable")
async def routine_enable(routine_id: str, payload: dict | None = None):
    enabled = bool((payload or {}).get("enabled", True))
    if not _automation().set_enabled(routine_id, enabled):
        return JSONResponse({"success": False, "error": "routine not found"}, status_code=404)
    return {"success": True, "routine_id": routine_id, "enabled": enabled}


@router.delete("/{routine_id}")
async def routine_delete(routine_id: str):
    if not _automation().remove_rule(routine_id):
        return JSONResponse({"success": False, "error": "routine not found"}, status_code=404)
    return {"success": True, "routine_id": routine_id, "deleted": True}


# Canonical endpoint: /routines/validate
@router.post("/validate")
async def routine_validate(payload: dict | None = None):
    """Validate a routine definition without saving or executing it."""
    payload = payload or {}
    action = str(payload.get("action_type") or "runtime.turn")
    event_type = str(payload.get("event_type") or "").strip()
    task = str(payload.get("task") or "").strip()
    errors = []
    allowed = {"runtime.turn", "agent.autonomous", "mission.start"}
    if action not in allowed:
        errors.append(f"unsupported action_type: {action}")
    if not event_type:
        errors.append("event_type is required")
    if not task:
        errors.append("task is required")
    try:
        cooldown = float(payload.get("cooldown_seconds", 60))
        if cooldown < 0:
            errors.append("cooldown_seconds must be >= 0")
    except (TypeError, ValueError):
        errors.append("cooldown_seconds must be numeric")
    return {
        "valid": not errors,
        "errors": errors,
        "action_type": action,
        "safety": "existing automation + mission policy remains authoritative",
    }


__all__ = ["router"]
