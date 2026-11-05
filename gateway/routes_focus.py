from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/focus", tags=["focus"])


@router.get("")
@router.get("/")
async def focus_snapshot(query: str = "", area: str | None = None):
    from core.focus_os import focus_os

    return focus_os.snapshot(query=query, area=area)


__all__ = ["router"]
