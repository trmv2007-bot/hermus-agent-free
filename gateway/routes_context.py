"""Context Fabric HTTP transport."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/context", tags=["context"])


@router.get("")
@router.get("/")
async def context_snapshot(
    query: str = "",
    user_id: str = "default",
    project: str | None = None,
    memory_limit: int = 6,
):
    from core.context_fabric import context_fabric

    return context_fabric.build(
        query=str(query or ""),
        user_id=str(user_id or "default"),
        project=project,
        memory_limit=memory_limit,
    )


__all__ = ["router"]
