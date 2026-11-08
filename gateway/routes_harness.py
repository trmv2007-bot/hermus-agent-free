"""Read-only harness API.

The turn harness owns session records, swarm mail and file-shift notices. It
had no HTTP surface at all, so the Control Room could only show a session list
through the console manifest. These endpoints are projections of
:mod:`core.harness.stats` — they read real files and real events, and every
route is read-only (no mutation endpoint exists here on purpose: the canonical
command path owns actions).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from core.harness import stats

router = APIRouter(prefix="/api/v1/harness", tags=["harness"])


@router.get("/overview")
async def harness_overview() -> dict[str, Any]:
    """Harness counts, pending notices, newest observed turn and the live budget."""
    return {"ok": True, **stats.overview()}


@router.get("/sessions")
async def harness_sessions(limit: int = 50) -> dict[str, Any]:
    return {"ok": True, **stats.sessions_view(limit)}


@router.get("/sessions/{session_id}")
async def harness_session(session_id: str) -> dict[str, Any]:
    view = stats.session_view(session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="unknown harness session")
    return {"ok": True, **view}


@router.get("/events")
async def harness_events(limit: int = 50) -> dict[str, Any]:
    return {"ok": True, **stats.events_view(limit)}


# ---------------------------------------------------------------------------
# Eval-harness API (/api/harness/*)
#
# Projections of HarnessRunner + the suite registry: list suites, list runs,
# latest run, run detail, per-suite stats and A/B compare. Read-only on
# purpose — evaluation runs are launched from the CLI (``hermus harness run``).
# ---------------------------------------------------------------------------

from core.harness.runner import HarnessRunner  # noqa: E402
from core.harness.tasks import list_builtin_suites, suite_info  # noqa: E402

eval_router = APIRouter(prefix="/api/harness", tags=["harness-eval"])


def _runner() -> HarnessRunner:
    """Default runner over the configured runs dir (tests monkeypatch this)."""
    return HarnessRunner()


@eval_router.get("/suites")
async def eval_suites(stats_flag: bool = False) -> dict[str, Any]:
    """Suite catalog with per-task summaries; ``?stats=true`` adds run rollups."""
    out: list[dict[str, Any]] = []
    for name in list_builtin_suites():
        try:
            info = suite_info(name)
        except Exception as exc:
            out.append({"name": name, "error": str(exc)})
            continue
        if stats_flag:
            try:
                rollup = next((s for s in _runner().suite_stats() if s.get("suite") == name), None)
                if rollup:
                    info["stats"] = rollup
            except Exception:
                pass
        out.append(info)
    return {"ok": True, "suites": out}


@eval_router.get("/runs")
async def eval_runs(suite: str | None = None, limit: int = 20) -> dict[str, Any]:
    runs = _runner().list_runs(limit=limit)
    if suite:
        runs = [r for r in runs if r.get("suite") == suite]
    return {"ok": True, "count": len(runs), "runs": runs}


@eval_router.get("/latest")
async def eval_latest(suite: str) -> dict[str, Any]:
    latest = _runner().latest_run(suite)
    if latest is None:
        raise HTTPException(status_code=404, detail=f"no runs recorded for suite '{suite}'")
    return {"ok": True, "latest": latest}


@eval_router.get("/runs/{run_id}")
async def eval_run_detail(run_id: str) -> dict[str, Any]:
    report = _runner().load_run(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {"ok": True, **report}


@eval_router.get("/stats")
async def eval_stats() -> dict[str, Any]:
    return {"ok": True, "stats": _runner().suite_stats()}


@eval_router.get("/compare")
async def eval_compare(a: str, b: str) -> dict[str, Any]:
    try:
        comp = _runner().compare(a, b)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, **comp}
