"""Shared dashboard layout API used by the browser and agent tools."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.dashboard_state import dashboard_state

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/state")
async def get_dashboard_state() -> dict:
    return {"status": "ok", "dashboard": dashboard_state.snapshot()}


@router.post("/panels")
async def add_dashboard_panel(payload: dict) -> dict:
    try:
        panel = dashboard_state.add_panel(
            title=payload.get("title", "Untitled panel"),
            content=payload.get("content", ""),
            kind=payload.get("kind", "text"),
            order=payload.get("order"),
            width=payload.get("width", 6),
            source=payload.get("source", "dashboard"),
        )
        return {"status": "ok", "panel": panel, "dashboard": dashboard_state.snapshot()}
    except (KeyError, ValueError, TypeError) as exc:
        raise _error(exc) from exc


@router.patch("/panels/{panel_id}")
async def update_dashboard_panel(panel_id: str, payload: dict) -> dict:
    try:
        changes = {key: payload[key] for key in ("title", "content", "kind", "order", "width", "visible") if key in payload}
        panel = dashboard_state.update_panel(panel_id, **changes)
        return {"status": "ok", "panel": panel, "dashboard": dashboard_state.snapshot()}
    except (KeyError, ValueError, TypeError) as exc:
        raise _error(exc) from exc


@router.post("/panels/{panel_id}/move")
async def move_dashboard_panel(panel_id: str, payload: dict) -> dict:
    try:
        panel = dashboard_state.move_panel(panel_id, order=int(payload.get("order", 0)), width=payload.get("width"))
        return {"status": "ok", "panel": panel, "dashboard": dashboard_state.snapshot()}
    except (KeyError, ValueError, TypeError) as exc:
        raise _error(exc) from exc


@router.delete("/panels/{panel_id}")
async def remove_dashboard_panel(panel_id: str) -> dict:
    try:
        return {"status": "ok", **dashboard_state.remove_panel(panel_id), "dashboard": dashboard_state.snapshot()}
    except (KeyError, ValueError, TypeError) as exc:
        raise _error(exc) from exc


@router.patch("/tabs/{tab_id}")
async def update_dashboard_tab(tab_id: str, payload: dict) -> dict:
    try:
        tab = dashboard_state.update_tab(
            tab_id,
            label=payload.get("label"),
            visible=payload.get("visible"),
            order=payload.get("order"),
        )
        return {"status": "ok", "tab": tab, "dashboard": dashboard_state.snapshot()}
    except (KeyError, ValueError, TypeError) as exc:
        raise _error(exc) from exc


@router.post("/reset")
async def reset_dashboard() -> dict:
    return {"status": "ok", "dashboard": dashboard_state.reset()}


__all__ = ["router"]
