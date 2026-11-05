"""Unified device fabric HTTP projection."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("")
@router.get("/")
async def device_snapshot():
    from core.device_fabric import device_fabric

    return device_fabric.snapshot()


__all__ = ["router"]
