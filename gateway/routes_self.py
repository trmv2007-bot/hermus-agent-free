"""The self surface: what Hermus says is broken, and what it wants to add.

* ``GET /api/v1/self/attention``   the bounded self-diagnosis the Systems row reads

Read-only. The items come from :func:`core.self_model.diagnose`, which reuses the
doctor's own findings and the capability ledger, so this route adds no second
opinion about the machine — it only publishes one that already exists.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/self", tags=["self"])


@router.get("/attention")
def attention(refresh: bool = False):
    """At most three broken-now and three worth-adding items, each with evidence."""
    from core.self_model import diagnose

    diagnosis = diagnose(force=bool(refresh))
    return {"success": True, **diagnosis["view"], "diagnosis": {"broken": diagnosis["broken"], "worth": diagnosis["worth"]}}
