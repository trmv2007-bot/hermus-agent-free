from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/learning", tags=["learning"])


@router.get("")
@router.get("/")
async def learning_snapshot(limit: int = 12):
    from core.learning_fabric import learning_fabric

    return learning_fabric.snapshot(limit=limit)


__all__ = ["router"]
