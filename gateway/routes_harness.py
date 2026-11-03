"""Harness product API — suites, runs, stats, and A/B comparison for the
control room / dashboards (read-only, gateway-auth gated like the other
control routers). The pinned control-room JS is untouched; it can poll these.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from core.harness.runner import HarnessRunner
from core.harness.tasks import list_builtin_suites, suite_info

router = APIRouter()


def _runner() -> HarnessRunner:
    return HarnessRunner()


@router.get("/api/harness/suites")
async def harness_suites(stats: bool = Query(False)) -> JSONResponse:
    out: list[dict[str, Any]] = []
    for name in list_builtin_suites():
        try:
            info = suite_info(name)
        except Exception as e:
            out.append({"name": name, "error": f"{type(e).__name__}: {e}"})
            continue
        entry = {"name": name, "task_count": info.get("task_count", 0), "description": info.get("description", "")}
        if stats:
            entry["stats"] = next((s for s in _runner().suite_stats() if s["suite"] == name), None)
        out.append(entry)
    return JSONResponse({"suites": out})


@router.get("/api/harness/runs")
async def harness_runs(suite: str = Query(""), model: str = Query(""), limit: int = Query(50, ge=1, le=500)) -> JSONResponse:
    runs = _runner().list_runs(limit=100000)
    if suite:
        runs = [r for r in runs if r.get("suite") == suite]
    if model:
        runs = [r for r in runs if r.get("model") == model]
    return JSONResponse({"runs": runs[-limit:], "count": len(runs[-limit:])})


@router.get("/api/harness/latest")
async def harness_latest(suite: str = Query(...), model: str = Query("")) -> JSONResponse:
    row = _runner().latest_run(suite, model=model or None)
    if row is None:
        return JSONResponse({"error": f"no runs for suite '{suite}'"}, status_code=404)
    return JSONResponse({"latest": row})


@router.get("/api/harness/stats")
async def harness_stats() -> JSONResponse:
    return JSONResponse({"stats": _runner().suite_stats()})


@router.get("/api/harness/runs/{run_id}")
async def harness_run(run_id: str) -> JSONResponse:
    report = _runner().load_run(run_id)
    if report is None:
        return JSONResponse({"error": f"run '{run_id}' not found"}, status_code=404)
    return JSONResponse(report)


@router.get("/api/harness/compare")
async def harness_compare(a: str = Query(...), b: str = Query(...)) -> JSONResponse:
    try:
        return JSONResponse(_runner().compare(a, b))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
