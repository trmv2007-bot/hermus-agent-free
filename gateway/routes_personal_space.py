"""HTTP surface for HERMUS Personal Space."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/personal-space", tags=["personal-space"])


def _space():
    from core.personal_space import personal_space

    return personal_space


@router.get("")
@router.get("/")
async def personal_space_snapshot(limit: int = 8):
    return _space().snapshot(limit=limit)


@router.post("/run")
async def personal_space_run():
    """Run one bounded read-only curiosity pass now."""
    result = _space().tick(force=True)
    status = str(result.get("status") or "")
    if status == "queue_error":
        return JSONResponse(result, status_code=503)
    return result


@router.post("/proposals/{proposal_id}/approve")
async def personal_space_approve(proposal_id: str):
    result = _space().approve(proposal_id)
    if not result.get("success"):
        code = 404 if result.get("error") == "proposal_not_found" else 409
        return JSONResponse(result, status_code=code)
    return result


@router.post("/proposals/{proposal_id}/dismiss")
async def personal_space_dismiss(proposal_id: str):
    result = _space().dismiss(proposal_id)
    if not result.get("success"):
        code = 404 if result.get("error") == "proposal_not_found" else 409
        return JSONResponse(result, status_code=code)
    return result


__all__ = ["router"]
