"""Canonical contract endpoints (Rebuild spec §8, §28).

These routes expose truth from the canonical system — one event bus, one tool
gateway, one model gateway, one canonical world-state — rather than fabricating
health/counts/state. Every response is derived from a real probe or the durable
event log. No endpoint returns a value it cannot prove.

Endpoints:
* ``POST /api/v1/commands``            — command accounting (click → command)
* ``GET  /api/v1/system/health``       — health probes (real)
* ``GET  /api/v1/system/capabilities`` — provider/model/tool capability state
* ``GET  /api/v1/runs/{id}``           — run detail (from event log)
* ``GET  /api/v1/runs/{id}/timeline``  — timeline of state transitions
* ``GET  /api/v1/runs/{id}/evidence``  — evidence bundle
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1", tags=["canonical"])

#: A health snapshot is a live probe reused for this long. The UI polls every
#: few seconds; sharing one probe keeps the event loop free without inventing
#: a status (the payload is always the result of a real probe).
HEALTH_TTL_SECONDS = 30.0
_HEALTH_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_CAPABILITY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class CommandRequest(BaseModel):
    """Typed backend command (Rebuild spec §8)."""

    command: str
    target: str | None = None
    args: dict[str, Any] = {}
    actor: str = "user"
    source: str = "dashboard"
    session_id: str = "default"
    idempotency_key: str | None = None


@router.get("/system/health")
async def system_health():
    """Real health probes (never fabricated).

    ``bootstrap.doctor()`` shells out to inspect the interpreter, the venv and
    every optional capability. It is a real probe, not a cheap one: running it
    inline on the event loop blocked *every* other request for ~7s, which made
    the Control Room feel dead while its own health card was loading. The probe
    now runs in a worker thread and its result is reused for a few seconds —
    still a live probe, just not a stampede of them.
    """
    now = time.monotonic()
    cached = _HEALTH_CACHE.get("data")
    if cached is not None and now - float(_HEALTH_CACHE.get("ts") or 0.0) < HEALTH_TTL_SECONDS:
        return cached

    try:
        report = await asyncio.to_thread(_probe_health)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"health probe failed: {exc}") from exc
    _HEALTH_CACHE.update(ts=time.monotonic(), data=report)
    return report


def _probe_health() -> dict[str, Any]:
    import bootstrap

    report = bootstrap.doctor()
    required_ok = all(v["present"] for k, v in report["capabilities"].items() if k.startswith("required."))
    return {
        "ok": required_ok,
        "python": report.get("python"),
        "venv": report.get("venv"),
        "capabilities": report["capabilities"],
    }


def warm_health_cache() -> None:
    """Probe once at startup so the first Control Room load is not 10s of skeletons."""
    try:
        report = _probe_health()
    except Exception:
        return
    _HEALTH_CACHE.update(ts=time.monotonic(), data=report)


@router.get("/system/capabilities")
async def system_capabilities(payload: bool = False):
    """Live provider/model/tool capability state from the canonical gateways.

    Building the provider/tool/voice/avatar inventory is synchronous work that
    touches the filesystem and (for speech/avatar) subprocess discovery. It runs
    off the event loop and is reused briefly, so opening the Control Room does
    not block every other request behind capability discovery.
    """
    key = "payload" if payload else "cached"
    now = time.monotonic()
    hit = _CAPABILITY_CACHE.get(key)
    if hit and now - float(hit[0]) < HEALTH_TTL_SECONDS:
        return hit[1]
    try:
        report = await asyncio.to_thread(_probe_capabilities, bool(payload))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"capability probe failed: {exc}") from exc
    _CAPABILITY_CACHE[key] = (time.monotonic(), report)
    return report


def _probe_capabilities(payload: bool) -> dict[str, Any]:
    from core.avatar import get_avatar_service
    from core.models import get_model_gateway
    from core.speech import speech_engine
    from core.tools import get_tool_gateway
    from tools.voice import voice_available_models

    model_gw = get_model_gateway()
    providers = model_gw.providers(probe=payload)
    tool_gw = get_tool_gateway()
    tools = tool_gw.descriptors()
    return {
        "providers": providers,
        "tools": {name: d.to_dict() for name, d in tools.items()},
        "tool_count": len(tools),
        "circuit": model_gw.health(),
        "speech": speech_engine.status(),
        "transcription": voice_available_models(),
        "avatar": get_avatar_service().status(probe=payload),
    }


@router.post("/commands")
async def command(req: CommandRequest):
    """Record a backend command and emit canonical events (click accounting)."""
    from core.contracts import Command, CommandStatus, EventType
    from core.events import get_bus

    cmd = Command(
        command=req.command,
        target=req.target,
        args=req.args,
        actor=req.actor,
        source=req.source,
        session_id=req.session_id,
        idempotency_key=req.idempotency_key,
    )
    env = cmd.to_envelope(type=EventType.COMMAND_REQUESTED.value, status=CommandStatus.PENDING.value)
    published = get_bus().publish(env)
    return {"ok": True, "event_id": published.event_id, "trace_id": published.trace_id, "command_id": published.command_id}


@router.get("/runs/{run_id}")
async def run_detail(run_id: str):
    """Reconstruct a run from the canonical event log."""
    return _run_from_log(run_id)


@router.get("/runs/{run_id}/timeline")
async def run_timeline(run_id: str):
    return {"run_id": run_id, "timeline": _run_from_log(run_id).get("events", [])}


@router.get("/runs/{run_id}/evidence")
async def run_evidence(run_id: str):
    ev_log = _run_from_log(run_id)
    return {"run_id": run_id, "evidence": ev_log.get("evidence_refs", [])}


def _run_from_log(run_id: str) -> dict[str, Any]:
    from core.events import get_bus

    bus = get_bus()
    # Replay the durable log (or buffer) filtering by run_id.
    envs = bus.replay(since_cursor=0)
    matched = [e for e in envs if e.run_id == run_id or run_id in (e.mission_id or "") or (e.trace_id == run_id)]
    if not matched:
        raise HTTPException(status_code=404, detail=f"no events for run {run_id}")
    refs = sorted({r for e in matched for r in e.evidence_refs})
    return {
        "run_id": run_id,
        "status": matched[-1].status,
        "last_stage": matched[-1].type,
        "events": [e.to_dict() for e in matched],
        "evidence_refs": refs,
        "count": len(matched),
    }
