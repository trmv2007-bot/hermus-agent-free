"""Android control API (§16–19).

These routes are a thin projection over the canonical
:class:`~core.android.tool.AndroidTool`; they carry no business logic and never
bypass it. Every op is still consent-gated, allowlisted and audited inside the
tool. Responses are honest: a missing device/permission yields
``android_control_unavailable`` with the reason, never a fabricated success.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/android", tags=["android"])


class _OpRequest(BaseModel):
    args: dict[str, Any] = {}


class _PermissionRequest(BaseModel):
    op_class: str


class _OpsRequest(BaseModel):
    ops: list[str]


@router.get("/capability")
async def capability() -> dict[str, Any]:
    from core.android.tool import get_android_tool
    return get_android_tool().capability()


@router.get("/permissions")
async def permissions() -> dict[str, Any]:
    from core.android.permissions import get_permission_manager
    pm = get_permission_manager()
    from core.android.permissions import OP_CLASSES
    return {"ok": True, "consent": {c: pm.is_consented(c) for c in OP_CLASSES},
            "allowed_ops": pm.allowed_ops()}


@router.post("/permissions/grant")
async def grant(req: _PermissionRequest) -> dict[str, Any]:
    from core.android.permissions import get_permission_manager
    try:
        get_permission_manager().grant(req.op_class)
        return {"ok": True, "granted": req.op_class}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/permissions/revoke")
async def revoke(req: _PermissionRequest) -> dict[str, Any]:
    from core.android.permissions import get_permission_manager
    get_permission_manager().revoke(req.op_class)
    return {"ok": True, "revoked": req.op_class}


@router.post("/permissions/ops")
async def set_ops(req: _OpsRequest) -> dict[str, Any]:
    from core.android.permissions import get_permission_manager
    try:
        get_permission_manager().set_allowed_ops(req.ops)
        return {"ok": True, "allowed_ops": get_permission_manager().allowed_ops()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{op}")
async def exec_op(op: str, req: _OpRequest) -> dict[str, Any]:
    from core.android.tool import get_android_tool
    result = get_android_tool().run(op, req.args or {})
    # Honest HTTP mapping: unavailable ops are still a client-confusable state the
    # caller can distinguish by the structured error; we keep it 200 with the
    # typed result rather than masking it as a 5xx.
    return result
