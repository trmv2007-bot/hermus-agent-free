"""Local engine + Hermus doctor endpoints.

Three concerns that the dashboard drives:

* ``/engine/*``   — hardware detection, the NPU/GPU routing plan, NoLlama
  install/start/stop, and model downloads (MiniCPM lives here, not in
  ``setup.sh``: the installer stays runtime-only and weights are fetched later,
  on demand).
* ``/doctor/*``   — Hermus's self-repair: collect signals, diagnose, optionally
  ask the small local model and the internet, and hand back a report.
* ``/events/recent`` — polling fallback for the overview's Live Telemetry feed
  (the WebSocket at ``/dashboard/events`` is the primary path).

Blocking work (installs, downloads, LLM triage) runs in a worker thread via
``asyncio.to_thread`` so the gateway keeps serving while a multi-GB model
lands on disk.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

router = APIRouter()


# ---------------------------------------------------------------------------
# Local engine: detection, routing, NoLlama lifecycle
# ---------------------------------------------------------------------------
@router.get("/engine/status")
async def engine_status(probe: bool = True, refresh: bool = False):
    """Hardware, routing plan, engine reachability and what is still missing."""
    try:
        from core.accelerators import state as engine_state

        return await asyncio.to_thread(engine_state, refresh=refresh, probe=probe)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc), "status": "unknown"}, status_code=500)


@router.post("/engine/refresh")
async def engine_refresh():
    """Re-run hardware detection (after plugging in hardware / installing drivers)."""
    try:
        from core.accelerators import state as engine_state

        return await asyncio.to_thread(engine_state, refresh=True, probe=True)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/engine/nollama/install")
async def engine_nollama_install():
    """Fetch the NoLlama server + build its venv. Downloads **no** model weights."""
    try:
        from core.nollama import nollama_manager

        result = await asyncio.to_thread(nollama_manager.install)
        status_code = 200 if result.get("success") else 500
        return JSONResponse(result, status_code=status_code)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/engine/nollama/start")
async def engine_nollama_start(payload: dict[str, Any] = None):
    """Start the local engine pinned to a device (defaults to NoLlama's own detection)."""
    payload = payload or {}
    try:
        from core.nollama import nollama_manager

        result = await asyncio.to_thread(
            nollama_manager.start,
            device=str(payload.get("device") or ""),
            model_dir=payload.get("model_dir"),
            gpu_model_dir=payload.get("gpu_model_dir"),
            port=payload.get("port"),
            extra_args=payload.get("extra_args"),
            idle_timeout=payload.get("idle_timeout"),
        )
        return JSONResponse(result, status_code=200 if result.get("success") else 500)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/engine/nollama/stop")
async def engine_nollama_stop():
    """Stop the local engine this gateway started."""
    try:
        from core.nollama import nollama_manager

        return await asyncio.to_thread(nollama_manager.stop)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"stopped": False, "error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Models: catalog + on-demand downloads (the MiniCPM button)
# ---------------------------------------------------------------------------
@router.get("/engine/models")
async def engine_models():
    """Catalog merged with what is on disk, plus the plan's recommendation."""
    try:
        from core.accelerators import cached_plan
        from core.nollama import nollama_manager

        plan = cached_plan()
        return {
            "catalog": nollama_manager.list_catalog(),
            "installed": nollama_manager.installed_models(),
            "downloads": nollama_manager.downloads(),
            "recommended": nollama_manager.recommended_model(plan),
            "models_dir": str(nollama_manager.models_dir),
            "engine_installed": nollama_manager.installed(),
        }
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/engine/models/download")
async def engine_model_download(payload: dict[str, Any] = None):
    """Start a model download (e.g. ``{"model": "minicpm"}``). Returns immediately."""
    payload = payload or {}
    model_id = str(payload.get("model") or payload.get("id") or "minicpm")
    try:
        from core.nollama import nollama_manager

        result = await asyncio.to_thread(
            nollama_manager.download_model, model_id, force=bool(payload.get("force"))
        )
        return JSONResponse(result, status_code=200 if result.get("success") else 400)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.get("/engine/downloads")
async def engine_downloads(job_id: Optional[str] = None):
    """Download progress. Pass ``job_id`` for one job, omit it for all."""
    try:
        from core.nollama import nollama_manager

        if job_id:
            job = nollama_manager.download_status(job_id)
            if job is None:
                return JSONResponse({"error": f"unknown job '{job_id}'"}, status_code=404)
            return job
        return {"downloads": nollama_manager.downloads()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/engine/downloads/{job_id}")
async def engine_download_detail(job_id: str):
    """One download's progress — what the dashboard's progress bar polls."""
    try:
        from core.nollama import nollama_manager

        job = nollama_manager.download_status(job_id)
        if job is None:
            return JSONResponse({"error": f"unknown job '{job_id}'"}, status_code=404)
        return job
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/engine/downloads/{job_id}/cancel")
async def engine_download_cancel(job_id: str):
    """Cancel an in-flight download (already-terminal jobs report their state)."""
    try:
        from core.nollama import nollama_manager

        return nollama_manager.cancel_download(job_id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"cancelled": False, "error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Live telemetry feed (polling fallback for the dashboard)
# ---------------------------------------------------------------------------
@router.get("/events/recent")
async def events_recent(limit: int = 30):
    """Recent dashboard events — the feed the overview's Live Telemetry shows."""
    try:
        from core.dashboard_events import dashboard_event_bus

        events = dashboard_event_bus.recent(limit=max(1, min(200, int(limit))))
        return {"count": len(events), "events": events}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"count": 0, "events": [], "error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Hermus doctor — Hermus's own physician
# ---------------------------------------------------------------------------
@router.get("/doctor/status")
async def doctor_status():
    """Cheap status: engine, findings by severity, stuck work, recent reports."""
    try:
        from core.doctor import doctor

        return await asyncio.to_thread(doctor.status)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/doctor/run")
async def doctor_run(payload: dict[str, Any] = None):
    """Run a full examination and return the report."""
    payload = payload or {}
    try:
        from core.doctor import doctor

        report = await asyncio.to_thread(
            doctor.run,
            ask_internet=payload.get("ask_internet"),
            use_llm=bool(payload.get("use_llm", True)),
            reap=bool(payload.get("reap", False)),
            model=payload.get("model"),
            stuck_minutes=payload.get("stuck_minutes"),
            auto=bool(payload.get("auto", False)),
        )
        return report
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@router.post("/doctor/reap")
async def doctor_reap(payload: dict[str, Any] = None):
    """Close out work stuck in a non-terminal state (dry_run defaults to true)."""
    payload = payload or {}
    try:
        from core.doctor import doctor

        return await asyncio.to_thread(
            doctor.reap_stuck,
            dry_run=bool(payload.get("dry_run", True)),
            stuck_minutes=payload.get("stuck_minutes"),
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/doctor/reports")
async def doctor_reports(limit: int = 10):
    """Recent doctor reports (newest first)."""
    try:
        from core.doctor import doctor

        reports = await asyncio.to_thread(doctor.recent, max(1, min(50, int(limit))))
        return {"count": len(reports), "reports": reports}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"count": 0, "reports": [], "error": str(exc)}, status_code=500)


@router.get("/doctor/reports/{report_id}")
async def doctor_report(report_id: str, fmt: str = "json"):
    """One report. ``fmt=md`` returns the markdown a human should read."""
    try:
        from core.doctor import doctor, to_markdown

        for report in await asyncio.to_thread(doctor.recent, 50):
            if report.get("id") == report_id:
                if fmt == "md":
                    return PlainTextResponse(to_markdown(report), media_type="text/markdown")
                return report
        return JSONResponse({"error": f"unknown report '{report_id}'"}, status_code=404)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)
