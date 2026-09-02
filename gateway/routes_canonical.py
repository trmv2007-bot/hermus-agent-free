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

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1", tags=["canonical"])


class CommandRequest(BaseModel):
    """Typed backend command (Rebuild spec §8)."""
    command: str
    target: Optional[str] = None
    args: dict[str, Any] = {}
    actor: str = "user"
    source: str = "dashboard"
    session_id: str = "default"
    idempotency_key: Optional[str] = None


@router.get("/system/health")
async def system_health():
    """Real health probes (never fabricated)."""
    import bootstrap
    try:
        report = bootstrap.doctor()
        required_ok = all(v["present"] for k, v in report["capabilities"].items()
                          if k.startswith("required."))
        return {"ok": required_ok, "python": report.get("python"),
                "venv": report.get("venv"), "capabilities": report["capabilities"]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"health probe failed: {exc}") from exc


@router.get("/system/capabilities")
async def system_capabilities(payload: bool = False):
    """Live provider/model/tool capability state from the canonical gateways."""
    from core.avatar import get_avatar_service
    from core.models import get_model_gateway
    from core.speech import speech_engine
    from core.tools import get_tool_gateway
    from tools.voice import voice_available_models
    try:
        model_gw = get_model_gateway()
        providers = model_gw.providers(probe=bool(payload))
        tool_gw = get_tool_gateway()
        tools = tool_gw.descriptors()
        return {
            "providers": providers,
            "tools": {name: d.to_dict() for name, d in tools.items()},
            "tool_count": len(tools),
            "circuit": model_gw.health(),
            "speech": speech_engine.status(),
            "transcription": voice_available_models(),
            "avatar": get_avatar_service().status(probe=bool(payload)),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"capability probe failed: {exc}") from exc


@router.post("/commands")
async def command(req: CommandRequest):
    """Record a backend command and emit canonical events (click accounting)."""
    from core.contracts import Command, EventEnvelope, EventType, CommandStatus
    from core.events import get_bus
    cmd = Command(command=req.command, target=req.target, args=req.args,
                  actor=req.actor, source=req.source, session_id=req.session_id,
                  idempotency_key=req.idempotency_key)
    env = cmd.to_envelope(type=EventType.COMMAND_REQUESTED.value,
                          status=CommandStatus.PENDING.value)
    published = get_bus().publish(env)
    return {"ok": True, "event_id": published.event_id,
            "trace_id": published.trace_id, "command_id": published.command_id}


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
