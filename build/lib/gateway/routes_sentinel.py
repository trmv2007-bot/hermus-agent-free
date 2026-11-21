"""Sentinel read API — feeds the Control Room's Fairy Attention / sentinel views.

Read-only: decisions, alerts, rule proposals and thresholds. The sentinel's
``sentinel.decision`` events also flow through the canonical EventBus, so the
control room's live event stream already shows them; these endpoints give the
UI a compact pollable projection.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.config import config
from core.sentinel import list_proposals, thresholds

router = APIRouter()


def _tail_jsonl(path, limit: int) -> list[dict[str, Any]]:
    p = config.resolve_path(path)
    if not p.is_file():
        return []
    rows = [r for r in p.read_text(encoding="utf-8").splitlines() if r.strip()]
    out = []
    for r in rows[-limit:]:
        try:
            out.append(json.loads(r))
        except Exception:
            out.append({"raw": r[:200]})
    return out


@router.get("/api/sentinel/status")
async def sentinel_status() -> JSONResponse:
    try:
        from core.events import get_bus

        cursor = get_bus().cursor
    except Exception:
        cursor = None
    return JSONResponse(
        {
            "thresholds": thresholds(),
            "bus_cursor": cursor,
            "proposals": len(list_proposals()),
            "with_gateway": bool(getattr(config, "sentinel_with_gateway", True)),
            "model": getattr(config, "sentinel_model", "auto"),
        }
    )


@router.get("/api/sentinel/decisions")
async def sentinel_decisions(limit: int = 50) -> JSONResponse:
    return JSONResponse({"decisions": _tail_jsonl("data/sentinel/decisions.jsonl", max(1, min(limit, 500)))})


@router.get("/api/sentinel/alerts")
async def sentinel_alerts(limit: int = 50) -> JSONResponse:
    return JSONResponse({"alerts": _tail_jsonl("data/sentinel/alerts.jsonl", max(1, min(limit, 500)))})


@router.get("/api/healthz")
async def healthz() -> JSONResponse:
    """One-glance always-on health: sentinel, bus, cron (night shift), watchdog, queue."""
    import time as _time

    out: dict[str, Any] = {"ok": True, "ts": _time.time()}
    try:
        from core.events import get_bus

        bus = get_bus()
        out["bus"] = {"cursor": bus.cursor, "subscribers": len(getattr(bus, "_subscribers", []))}
    except Exception as e:
        out["bus"] = {"error": f"{type(e).__name__}: {e}"}
        out["ok"] = False
    try:
        from core.sentinel import list_proposals, thresholds

        out["sentinel"] = {"thresholds": thresholds(), "proposals": len(list_proposals())}
    except Exception as e:
        out["sentinel"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        from scheduler.cron import cron_manager

        jobs = cron_manager.list_jobs()
        night = next((j for j in jobs if "sentinel consolidate" in str(j.get("task", ""))), None)
        out["cron"] = {
            "jobs": len(jobs),
            "night_shift": (
                {"id": night.get("id"), "schedule": night.get("natural"), "next_run": night.get("next_run")} if night else None
            ),
        }
    except Exception as e:
        out["cron"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        from core.watchdog import watchdog

        rec = watchdog.recent(1)
        out["watchdog"] = {"last": rec[0] if rec else None}
    except Exception as e:
        out["watchdog"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        from gateway.queue import job_queue

        st = job_queue.status()
        out["queue"] = {"by_status": st.get("by_status", {}), "enabled": getattr(job_queue, "enabled", False)}
    except Exception as e:
        out["queue"] = {"error": f"{type(e).__name__}: {e}"}
    return JSONResponse(out)


@router.get("/api/sentinel/proposals")
async def sentinel_proposals() -> JSONResponse:
    return JSONResponse({"proposals": list_proposals()})
