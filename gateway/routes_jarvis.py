"""Read-only control-plane data and honest Navigator operations for JARVIS.

This router deliberately aggregates existing runtime registries rather than
inventing dashboard state.  No secret values are returned.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import threading
import time
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()
_STARTED = time.monotonic()

#: The aggregate samples the resource monitor and capability gate, which can
#: take seconds (nvidia-smi). Reusing one real sample for a few seconds keeps
#: the 5s UI poll cheap without inventing values.
STATUS_TTL_SECONDS = 30.0
_STATUS_LOCK = threading.Lock()
_STATUS_CACHE: dict[str, object] = {"ts": 0.0, "data": None}


def _active_count(rows: list[dict]) -> int:
    return sum(1 for row in rows if row.get("status") in {"queued", "running"})


@router.get("/api/jarvis/status")
def jarvis_status():
    """One factual snapshot used by the JARVIS status/telemetry panels.

    Declared ``def`` (not ``async def``) on purpose: the body reads the queue,
    the run bus, the agent/tool/artifact registries and the resource monitor,
    all synchronously. FastAPI runs a sync handler in its threadpool, so this
    can no longer stall the event loop while the Control Room polls it.
    """
    from core.agent_manager import agent_manager
    from core.artifact_manager import artifact_manager
    from core.computer.resources import get_resource_monitor
    from core.config import config
    from core.model_capabilities import mission_capability_gate
    from core.providers import list_providers
    from core.run_events import run_bus
    from core.tool_registry import tool_registry
    from gateway.channels import get_channel_status, get_discord_token, get_telegram_token
    from gateway.queue import job_queue

    now = time.monotonic()
    with _STATUS_LOCK:
        cached = _STATUS_CACHE.get("data")
        if isinstance(cached, dict) and now - float(_STATUS_CACHE.get("ts") or 0.0) < STATUS_TTL_SECONDS:
            return cached

    queue = job_queue.status()
    jobs = job_queue.list_jobs(limit=100)
    runs = run_bus.runs()[-100:]
    agents = agent_manager.list()
    tools = tool_registry.list_tools()
    channels = get_channel_status()
    artifacts = artifact_manager.list_artifacts()
    model_ref = str(getattr(config, "model", "") or "")
    capability = mission_capability_gate(model_ref)
    telemetry = get_resource_monitor().sample()
    providers = list_providers()

    snapshot = {
        "gateway": {"reachable": True, "version": "2.2-free-architecture", "uptime_seconds": int(time.monotonic() - _STARTED)},
        "queue": queue,
        "counts": {
            "active_jobs": _active_count(jobs),
            "active_runs": _active_count(runs),
            "tools": int(tools.get("count", len(tools.get("tools", [])))),
            "agents": len(agents),
            "artifacts": len(artifacts),
        },
        "runs": runs,
        "jobs": jobs,
        "channels": {
            "runtime": channels,
            "telegram_configured": bool(get_telegram_token()),
            "discord_configured": bool(get_discord_token()),
        },
        "model": capability.to_dict() if hasattr(capability, "to_dict") else capability,
        "providers": providers,
        "telemetry": telemetry,
    }
    with _STATUS_LOCK:
        _STATUS_CACHE.update(ts=time.monotonic(), data=snapshot)
    return snapshot


def _validate_public_url(value: str) -> tuple[str | None, str | None]:
    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None, "Only absolute http:// or https:// URLs are supported"
        # Browser retrieval is server-side. Refuse loopback/private/link-local
        # targets so this UI cannot be used as an SSRF primitive.
        for info in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return None, "Private, loopback, link-local and reserved addresses are not allowed"
        return value, None
    except Exception as exc:
        return None, f"URL validation failed: {exc}"


@router.post("/navigator/fetch")
async def navigator_fetch(payload: dict | None = None):
    """Perform a real Playwright navigation and extract visible body text."""
    payload = payload or {}
    url, error = await asyncio.to_thread(_validate_public_url, str(payload.get("url") or "").strip())
    if error:
        return JSONResponse({"success": False, "error": error}, status_code=400)

    from tools.browser import browser_extract, browser_navigate

    result = await asyncio.to_thread(browser_navigate, url)
    if not result.get("success"):
        return JSONResponse(result, status_code=503)
    extracted = await asyncio.to_thread(browser_extract, "body")
    if not extracted.get("success"):
        return JSONResponse(
            {**result, "success": False, "error": extracted.get("error", "Page loaded but extraction failed")}, status_code=502
        )
    return {
        "success": True,
        "url": result.get("url", url),
        "title": result.get("title") or "",
        "content_length": result.get("content_length"),
        "text": str(extracted.get("text") or "")[:20000],
        "retrieval": "playwright",
    }
