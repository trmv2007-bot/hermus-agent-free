"""Fleet control-plane routes (SPEC_PERSISTENT_FLEET §9).

Canonical router for the persistent fleet: roster introspection, spawn/dismiss,
task assignment, broadcast. Mounted at /api/fleet alongside the other gateway
routers (see gateway/gateway.py).

Honest contract:
* 400 for illegal transitions / missing confirm / bad body
* 404 for unknown agents
* 409 for name collision (reuses FleetRegistry's case-insensitive uniqueness)
* Never fabricate state — reads come from FleetRegistry, errors are envelope-wrapped
* INFO logs for spawn/dismiss/assign
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from core.fleet.orchestrator import Orchestrator
from core.fleet.registry import (
    DESTROYED,
    ERROR,
    IDLE,
    PAUSED,
    SLEEPING,
    WORKING,
    FleetRegistry,
    IllegalTransition,
    RegistryError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fleet")

#: WebSocket routes live on their own router because the gateway's HTTP auth
#: dependency cannot be applied to a router that carries WS endpoints (a failing
#: dependency closes a socket with the wrong code). They self-authenticate via
#: gateway.context.ws_token_ok instead.
ws_router = APIRouter(prefix="/api/fleet")


# ------------------------------------------------------------------
# Spawn
# ------------------------------------------------------------------


@router.post("/agents")
async def spawn_agent(body: dict[str, Any] | None = None):
    """Spawn a new persistent agent (SPEC §9).

    Body: {name, persona?, provider?, model?, key_name?, skills?}
    Returns: {success: true, agent_id, state}
    """
    body = body or {}
    reg = _get_registry()

    name = str(body.get("name") or "").strip()
    if not name:
        return JSONResponse(
            {"success": False, "error": "bad_request", "message": "name is required", "code": "bad_request"},
            status_code=400,
        )

    persona = str(body.get("persona") or "").strip()
    provider = str(body.get("provider") or "groq").strip()
    model = str(body.get("model") or "").strip()
    key_name = body.get("key_name")
    skills = [str(s).strip() for s in (body.get("skills") or []) if str(s).strip()]

    try:
        spec = {
            "name": name,
            "persona": persona,
            "provider": provider,
            "model": model,
            "key_name": key_name,
            "skills": skills,
        }
        agent = reg.spawn(spec)
    except RegistryError as exc:
        # Name collision → 409 (RegistryError carries the collision message).
        logger.warning("[routes_fleet] spawn rejected: %s", exc)
        return JSONResponse(
            {"success": False, "error": str(exc), "code": "conflict"},
            status_code=409,
        )

    logger.info("[routes_fleet] spawned agent %s (%s) provider=%s model=%s", agent.name, agent.agent_id, provider, model)
    return {"success": True, "agent_id": agent.agent_id, "state": agent.state}


# ------------------------------------------------------------------
# Task assignment
# ------------------------------------------------------------------


@router.post("/agents/{agent_id}/task")
async def assign_task(agent_id: str, body: dict[str, Any] | None = None):
    """Assign a task to an agent (SPEC §9).

    Body: {task, idempotency_key?}
    Returns: {success, agent_id, state, task_id, executed, content?, blocked?, needs_approval?}
    """
    body = body or {}
    reg = _get_registry()

    task = str(body.get("task") or "").strip()
    if not task:
        return JSONResponse(
            {"success": False, "error": "bad_request", "message": "task is required", "code": "bad_request"},
            status_code=400,
        )

    agent = reg.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent {agent_id!r}")

    idempotency_key = body.get("idempotency_key")

    try:
        result = reg.assign(agent_id, task, idempotency_key=idempotency_key)
    except IllegalTransition as exc:
        logger.warning("[routes_fleet] assign illegal transition on %s: %s", agent_id, exc)
        return JSONResponse(
            {"success": False, "error": str(exc), "code": "illegal_transition"},
            status_code=400,
        )
    except RegistryError as exc:
        logger.warning("[routes_fleet] assign rejected on %s: %s", agent_id, exc)
        return JSONResponse(
            {"success": False, "error": str(exc), "code": "bad_request"},
            status_code=400,
        )

    logger.info(
        "[routes_fleet] assigned task %s to %s (%s) executed=%s blocked=%s",
        result.get("task_id"),
        agent.name,
        agent_id,
        result.get("executed"),
        result.get("blocked"),
    )

    resp = {
        "success": True,
        "agent_id": agent_id,
        "state": agent.state,
        "task_id": result.get("task_id"),
        "executed": result.get("executed", False),
    }
    if result.get("blocked"):
        resp["blocked"] = True
        resp["needs_approval"] = result.get("needs_approval")
    if result.get("content"):
        resp["content"] = result["content"]
    if result.get("deduplicated"):
        resp["deduplicated"] = True
    return resp


@router.get("/agents")
async def list_agents():
    """Return the live agent roster (SPEC §9).

    Each entry carries the minimal card shape the dashboard needs: id, name,
    state, provider, model, key_name, skills, last_activity, created_at, stats.
    """
    reg = _get_registry()
    agents = reg.list()
    return {"success": True, "agents": [_agent_card(a) for a in agents], "count": len(agents)}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    """Return a single agent with memory summary (SPEC §9)."""
    reg = _get_registry()
    agent = reg.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent {agent_id!r}")
    card = _agent_card(agent)
    # Attach a compact memory summary for the dismiss confirm dialog.
    card["memory_summary"] = agent.summary or ""
    card["memory_count"] = len(agent.memory)
    if agent.pending_approval:
        card["pending_approval"] = {
            "task_id": agent.pending_approval.get("task_id"),
            "task": agent.pending_approval.get("task"),
            "needs_approval": agent.pending_approval.get("needs_approval"),
        }
    return {"agent": card}


@router.get("/providers")
async def list_providers(fallback: str = Query(None, description="comma-separated fallback provider ids")):
    """Return available provider ids.

    If the ``/api/fleet/providers`` endpoint is not wired yet, return a
    hardcoded fallback list mirroring ``core/providers.PROVIDER_PRESETS`` keys.
    """
    from core.providers import PROVIDER_PRESETS

    ids = sorted(PROVIDER_PRESETS.keys())
    if fallback:
        extra = [p.strip() for p in fallback.split(",") if p.strip() and p.strip() not in ids]
        ids.extend(extra)
    return {"providers": ids, "source": "presets"}


@router.get("/models")
async def list_models(provider: str = Query(..., description="provider id")):
    """Return available models for a provider.

    Real implementation would call ``GET /v1/models`` on the provider SDK.
    For now, return a sensible fallback derived from the provider preset's
    ``default_model`` plus a handful of well-known models per provider.
    """
    from core.providers import PROVIDER_PRESETS

    preset = PROVIDER_PRESETS.get(provider)
    if not preset:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider!r}")

    default = preset.get("default_model", "")
    # Build a small curated list per provider — enough for the spawn widget.
    models = [default] if default else []
    if provider == "groq":
        models = ["groq/llama-3.3-70b", "groq/llama-3.1-8b-instant", "groq/gemma-7b-it", "groq/mixtral-8x7b-32768"]
    elif provider == "openai":
        models = ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
    elif provider == "ollama":
        models = ["llama3", "llama3.1", "mistral", "gemma:7b"]
    elif provider == "nvidia":
        models = ["nvidia/llama-3.3-nemotron-super-49b-v1.5", "nvidia/llama-3.1-70b-instruct"]
    else:
        models = [default] if default else []

    return {"provider": provider, "models": models, "source": "fallback"}


@router.get("/keys")
async def list_keys():
    """Return configured key names.

    Real implementation would list ``MultiKeyManager`` keys.  For now return
    an empty list — the spawn widget degrades gracefully.
    """
    from core.multi_key import multi_key_manager

    try:
        keys = multi_key_manager.list_keys()
        names = [k.get("name") or k.get("id") for k in keys if isinstance(k, dict)]
        return {"keys": names, "source": "vault"}
    except Exception:
        logger.warning("[routes_fleet] key listing unavailable", exc_info=True)
        return {"keys": [], "source": "none"}




@router.delete("/agents/{agent_id}")
async def dismiss_agent(agent_id: str, confirm: bool = Query(False, description="must be true to confirm destruction")):
    """Dismiss (destroy) an agent. Requires confirm=true (SPEC §9)."""
    reg = _get_registry()
    agent = reg.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent {agent_id!r}")
    try:
        reg.dismiss(agent_id, confirm=confirm)
    except RegistryError as exc:
        logger.warning("[routes_fleet] dismiss rejected on %s: %s", agent_id, exc)
        return JSONResponse(
            {"success": False, "error": "bad_request", "message": str(exc), "code": "bad_request"},
            status_code=400,
        )
    except IllegalTransition as exc:
        logger.warning("[routes_fleet] dismiss illegal transition on %s: %s", agent_id, exc)
        return JSONResponse(
            {"success": False, "error": "illegal_transition", "message": str(exc), "code": "illegal_transition"},
            status_code=400,
        )
    logger.info("[routes_fleet] dismissed agent %s (%s)", agent.name, agent_id)
    return {"success": True, "agent_id": agent_id, "destroyed": True}


@router.delete("/agents/{agent_id}/task")
async def cancel_task(agent_id: str):
    """Cancel the current in-flight task on an agent (SPEC §9)."""
    reg = _get_registry()
    agent = reg.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent {agent_id!r}")
    try:
        result = reg.cancel_task(agent_id)
    except RegistryError as exc:
        logger.warning("[routes_fleet] cancel rejected on %s: %s", agent_id, exc)
        return JSONResponse(
            {"success": False, "error": "bad_request", "message": str(exc), "code": "bad_request"},
            status_code=400,
        )
    logger.info("[routes_fleet] cancelled task %s on %s (%s)", result.get("task_id"), agent.name, agent_id)
    return {"success": True, "agent_id": agent_id, "cancelled": True, "task_id": result.get("task_id")}


@router.post("/agents/{agent_id}/pause")
async def pause_agent(agent_id: str):
    """Pause an agent — freezes cursor consumption (SPEC §9)."""
    reg = _get_registry()
    agent = reg.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent {agent_id!r}")
    try:
        agent = reg.pause(agent_id)
    except IllegalTransition as exc:
        logger.warning("[routes_fleet] pause illegal transition on %s: %s", agent_id, exc)
        return JSONResponse(
            {"success": False, "error": "illegal_transition", "message": str(exc), "code": "illegal_transition"},
            status_code=400,
        )
    logger.info("[routes_fleet] paused agent %s (%s)", agent.name, agent_id)
    return {"success": True, "agent_id": agent_id, "state": agent.state}


@router.post("/agents/{agent_id}/resume")
async def resume_agent(agent_id: str):
    """Resume a paused agent — thaws cursor consumption (SPEC §9)."""
    reg = _get_registry()
    agent = reg.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent {agent_id!r}")
    try:
        agent = reg.resume(agent_id)
    except IllegalTransition as exc:
        logger.warning("[routes_fleet] resume illegal transition on %s: %s", agent_id, exc)
        return JSONResponse(
            {"success": False, "error": "illegal_transition", "message": str(exc), "code": "illegal_transition"},
            status_code=400,
        )
    logger.info("[routes_fleet] resumed agent %s (%s)", agent.name, agent_id)
    return {"success": True, "agent_id": agent_id, "state": agent.state}


@router.post("/broadcast")
async def broadcast_message(body: dict[str, Any] | None = None):
    """Broadcast a message to all agents (SPEC §9).

    Body: {content}
    Returns: {success, event_id, seq}
    """
    body = body or {}
    content = body.get("content")
    if content is None:
        return JSONResponse(
            {"success": False, "error": "bad_request", "message": "content is required", "code": "bad_request"},
            status_code=400,
        )
    reg = _get_registry()
    event = reg.broadcast(content)
    return {"success": True, "event_id": event.id, "seq": event.seq}



@router.post("/orchestrate")
async def orchestrate_mission(body: dict[str, Any] | None = None):
    """Open and run a multi-agent mission via the Orchestrator (SPEC §7/§9).

    Body: {goal, agents?: list[str]|"all", budget?: int, rounds?: int, hitl_gates?: list[str], resolution_policy?: str}
    Returns: {success, mission_id, state, synthesis?, error?, partial?}
    """
    body = body or {}
    goal = str(body.get("goal") or "").strip()
    if not goal:
        return JSONResponse(
            {"success": False, "error": "bad_request", "message": "goal is required", "code": "bad_request"},
            status_code=400,
        )

    agents = body.get("agents")
    if agents == "all":
        agents = None  # Use all available agents
    budget = int(body.get("budget", 0) or 10000)
    rounds = int(body.get("rounds", 0) or 0) or None
    hitl_gates = body.get("hitl_gates") or []
    resolution_policy = str(body.get("resolution_policy", "first_result"))

    # Validate hitl_gates against known gates
    from core.fleet.missions import HITL_GATES
    valid_gates = [g for g in hitl_gates if g in HITL_GATES]

    orch = _get_orchestrator()

    try:
        result = await orch.orchestrate(
            goal=goal,
            agent_ids=agents,
            budget_tokens=budget,
            rounds=rounds,
            hitl_gates=valid_gates,
            resolution_policy=resolution_policy,
        )
    except Exception as exc:
        logger.warning("[routes_fleet] orchestrate failed: %s", exc)
        return JSONResponse(
            {"success": False, "error": "internal_error", "message": str(exc), "code": "internal_error"},
            status_code=500,
        )

    return {
        "success": result.success,
        "mission_id": result.mission_id,
        # The mission's real state when the orchestrator knows it; a mission
        # parked on a HITL gate reports "suspended", not "failed".
        "state": result.state or ("done" if result.success else "failed"),
        "synthesis": result.synthesis,
        "error": result.error,
        "partial": result.partial,
    }


@router.get("/watchdog")
async def watchdog_status():
    """Honest autonomy report: what the fleet watchdog did, on its own."""
    from core.fleet.watchdog import get_watchdog

    wd = get_watchdog()
    if wd is None:
        return {"ok": False, "reason": "watchdog not installed"}
    return {"ok": True, **wd.status()}


@router.post("/watchdog/toggle")
async def watchdog_toggle(body: dict[str, Any] | None = None):
    """Enable/disable the autonomy layer (the brake still outranks it)."""
    from core.fleet.watchdog import get_watchdog

    wd = get_watchdog()
    if wd is None:
        return {"ok": False, "reason": "watchdog not installed"}
    wd.enabled = bool((body or {}).get("enabled", not wd.enabled))
    return {"ok": True, "enabled": wd.enabled}


@router.post("/keys")
async def add_key(body: dict[str, Any] | None = None):
    """Add a new API key to the vault (SPEC §9 - open mode, FULL keys visible).

    Body: {provider, name, key, base_url?, rpm?, tpm?, rpd?}
    Returns: {success, key_id, name}
    """
    body = body or {}
    provider = str(body.get("provider") or "").strip()
    name = str(body.get("name") or "").strip()
    key = str(body.get("key") or "").strip()

    if not provider:
        return JSONResponse(
            {"success": False, "error": "bad_request", "message": "provider is required", "code": "bad_request"},
            status_code=400,
        )
    if not name:
        return JSONResponse(
            {"success": False, "error": "bad_request", "message": "name is required", "code": "bad_request"},
            status_code=400,
        )
    if not key:
        return JSONResponse(
            {"success": False, "error": "bad_request", "message": "key is required", "code": "bad_request"},
            status_code=400,
        )

    from core.multi_key import multi_key_manager

    try:
        # Add the key to the vault
        key_id = multi_key_manager.add_key(
            provider=provider,
            api_key=key,
            name=name,
            base_url=body.get("base_url"),
            rpm_limit=body.get("rpm"),
            tpm_limit=body.get("tpm"),
            rpd_limit=body.get("rpd"),
        )
    except Exception as exc:
        logger.warning("[routes_fleet] add_key failed: %s", exc)
        return JSONResponse(
            {"success": False, "error": "bad_request", "message": str(exc), "code": "bad_request"},
            status_code=400,
        )

    logger.info("[routes_fleet] added key %s (%s) for provider %s", name, key_id, provider)
    return {"success": True, "key_id": key_id, "name": name}


@router.get("/screen/frame")
async def get_screen_frame():
    """Return the latest annotated screen frame (SPEC §9).

    Returns: {success, frame_base64?, timestamp?, error?}
    """
    # This is a placeholder - real implementation would integrate with screen capture
    # For now, return a stub response
    return {
        "success": True,
        "frame_base64": None,
        "timestamp": None,
        "message": "screen capture not yet wired in this build",
    }




# Event kinds the dashboard agent feed forwards to connected WS clients.
_FLEET_FEED_KINDS = frozenset({
    "state_changed", "broadcast", "result",
    "mission_opened", "mission_terminated",
    "agent.spawned", "agent.updated",
})

def _ws_auth_ok(websocket) -> bool:
    """Delegate to the single owner of the WS token policy (gateway.context)."""
    from gateway.context import ws_token_ok

    return ws_token_ok(websocket)

def _fleet_ws_snapshot(reg):
    return {"agents": [_agent_card(a) for a in reg.list()], "count": len(reg.list())}


def _fleet_event_to_client_msg(reg, ev):
    """Project a FleetBus event into the dashboard client envelope.

    Secrets and full credential material are never included — only public card
    fields (name, state) are projected.
    """
    kind = ev.kind
    content = ev.content
    target = ev.target

    if kind == "state_changed" and isinstance(content, dict):
        agent_id = content.get("agent_id") or target
        agent = reg.get(agent_id) if agent_id else None
        return {
            "type": "fleet.state_changed",
            "seq": ev.seq, "id": ev.id, "ts": ev.ts,
            "agent_id": agent_id,
            "agent_name": agent.name if agent else None,
            "old_state": content.get("old_state"),
            "new_state": content.get("new_state") or (agent.state if agent else None),
            "reason": content.get("reason"),
        }

    if kind == "broadcast":
        return {
            "type": "fleet.broadcast",
            "seq": ev.seq, "id": ev.id, "ts": ev.ts,
            "content": content, "sender": ev.sender,
        }

    if kind == "result" and isinstance(content, dict):
        return {
            "type": "fleet.result",
            "seq": ev.seq, "id": ev.id, "ts": ev.ts,
            "agent_id": target,
            "task_id": content.get("task_id"),
            "executed": content.get("executed"),
            "blocked": content.get("blocked"),
            "needs_approval": content.get("needs_approval"),
        }

    if kind in ("mission_opened", "mission_terminated") and isinstance(content, dict):
        return {
            "type": "fleet." + kind,
            "seq": ev.seq, "id": ev.id, "ts": ev.ts,
            "mission_id": content.get("mission_id"),
            "goal": content.get("goal"),
            "state": content.get("state"),
        }

    if kind in ("agent.spawned", "agent.updated") and target:
        agent = reg.get(target)
        if agent is not None:
            return {
                "type": "fleet.agent_updated",
                "seq": ev.seq, "id": ev.id, "ts": ev.ts,
                "agent_id": target,
                "agent_name": agent.name,
                "state": agent.state,
                "event_kind": kind,
            }
    return None


async def _fleet_ws_poll_and_send(websocket, reg, *, poll_interval_s=1.0):
    """Poll the FleetBus for new events and forward filtered ones to websocket.

    The FleetBus has no push/subscribe primitive, so this closes the gap by
    polling the durable log for events newer than the client's last seen seq
    and forwarding only the dashboard-relevant kinds.

    TODO(Solar-2026-02): replace polling with an asyncio.Event/notify path when
    FleetBus grows a push primitive, so a quiet fleet burns no CPU.
    """
    import asyncio

    bus = reg.bus
    last_seq = 0

    while True:
        try:
            await asyncio.sleep(poll_interval_s)
        except asyncio.CancelledError:
            break

        try:
            new_since = bus.last_seq
            if new_since <= last_seq:
                continue

            # `since` is bound at call time. Capturing `last_seq` by name let the
            # worker thread read a value the loop had already advanced, so the
            # reader could skip events it had not sent.
            new_events = await asyncio.to_thread(
                lambda since=last_seq + 1: list(bus._iter_events(since))
            )
            if not new_events:
                last_seq = new_since
                continue

            sent = 0
            for ev in new_events:
                if ev.seq > last_seq:
                    last_seq = ev.seq
                if ev.kind not in _FLEET_FEED_KINDS:
                    continue
                msg = _fleet_event_to_client_msg(reg, ev)
                if msg is None:
                    continue
                try:
                    await websocket.send_json(msg)
                    sent += 1
                except Exception:
                    return
            if sent == 0 and last_seq < new_since:
                last_seq = new_since
        except Exception as exc:
            logger.debug("[routes_fleet] fleet_ws poll error: %s", exc)
            await asyncio.sleep(poll_interval_s)
@ws_router.websocket("/ws/fleet")
async def fleet_ws(websocket: WebSocket):
    """WebSocket live stream: state changes, messages, results, approvals (SPEC §9).

    Auth: token from ?token= or X-Hermus-Token; open when no gateway token
    configured. Sends initial snapshot then polls FleetBus for deltas.
    """
    if not _ws_auth_ok(websocket):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    await websocket.send_json({"type": "hello", "protocol": "hermus.fleet.v1"})

    reg = _get_registry()
    try:
        await websocket.send_json({"type": "snapshot", "data": _fleet_ws_snapshot(reg)})

        import asyncio as _asyncio

        poll_task = _asyncio.create_task(_fleet_ws_poll_and_send(websocket, reg))
        try:
            while True:
                try:
                    await _asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                except WebSocketDisconnect:
                    break
                except _asyncio.TimeoutError:
                    continue
                except Exception:
                    break
        finally:
            poll_task.cancel()
            try:
                await poll_task
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("[routes_fleet] fleet_ws error: %s", exc)
        try:
            await websocket.close()
        except Exception:
            pass


@ws_router.websocket("/ws/fleet/screen")
async def fleet_screen_ws(websocket: WebSocket):
    """Low-FPS live screen mirror WebSocket (SPEC §9).

    Streams JPEG-compressed frames from the rolling screen recorder.
    Starts the recorder if not already running (best-effort).

    Auth: token from ?token= or X-Hermus-Token; open when no gateway token
    configured (local default).
    """
    if not _ws_auth_ok(websocket):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    await websocket.send_json({"type": "hello", "protocol": "hermus.fleet.screen.v1"})

    # Get the screen recorder
    try:
        from core.integrations import _screen_recorder
        recorder = _screen_recorder()
    except Exception as exc:
        logger.warning("[routes_fleet] failed to get screen recorder: %s", exc)
        await websocket.send_json({
            "type": "error",
            "message": f"Screen recorder unavailable: {exc}",
        })
        await websocket.close()
        return

    # Start recorder if not running
    if not recorder.running:
        try:
            start_result = recorder.start(max_seconds=0, fps=2, output_path=None)
            if not start_result.get("success"):
                logger.warning("[routes_fleet] could not start screen recorder: %s", start_result.get("error"))
                await websocket.send_json({
                    "type": "error",
                    "message": f"Could not start screen recorder: {start_result.get('error', 'unknown error')}",
                })
        except Exception as exc:
            logger.warning("[routes_fleet] exception starting screen recorder: %s", exc)

    try:
        last_seq = -1
        while True:
            # Get latest frame from recorder's rolling buffer
            latest = recorder.latest()
            if latest and latest.get("sequence", -1) != last_seq:
                last_seq = latest.get("sequence", -1)
                # Frame data is already JPEG-compressed bytes
                frame_data = latest.get("data")
                if frame_data:
                    # Send as base64 for JSON transport
                    import base64
                    b64 = base64.b64encode(frame_data).decode("ascii")
                    await websocket.send_json({
                        "type": "frame",
                        "data": b64,
                        "timestamp": latest.get("ts"),
                        "sequence": latest.get("sequence"),
                        "captured_at": latest.get("captured_at"),
                        "offset": latest.get("offset"),
                    })
            await asyncio.sleep(0.5)  # ~2 FPS
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("[routes_fleet] fleet_screen_ws error: %s", exc)
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(exc),
            })
            await websocket.close()
        except Exception:
            pass


def _get_registry() -> FleetRegistry:
    """Return the shared FleetRegistry instance used by the gateway.

    The registry is created once at gateway startup (lifespan) and stored on
    the app state so every route sees the same truth.  When called outside the
    gateway (tests), the caller is responsible for constructing + mounting a
    registry on the test app.
    """
    from gateway.gateway import app

    reg = getattr(app.state, "fleet_registry", None)
    if reg is None:
        # Fallback for tests / direct imports: construct an in-memory registry
        # with a stub chat function so the route layer is testable in isolation.
        from core.fleet.bus import FleetBus
        from core.fleet.registry import chat_via_freellm

        bus = FleetBus(base_dir=None, fsync=False)  # in-memory when base_dir is None
        reg = FleetRegistry(bus, chat_fn=chat_via_freellm)
        app.state.fleet_registry = reg
        logger.info("[routes_fleet] created in-memory FleetRegistry (test/direct mode)")
    return reg


def _get_orchestrator() -> Orchestrator:
    """Return the shared Orchestrator instance used by the gateway.

    The orchestrator is created once at gateway startup (lifespan) and stored on
    the app state. Falls back to creating one with the registry's bus for tests.
    """
    from gateway.gateway import app

    orch = getattr(app.state, "fleet_orchestrator", None)
    if orch is None:
        reg = _get_registry()
        orch = Orchestrator(reg, reg.bus)
        app.state.fleet_orchestrator = orch
        logger.info("[routes_fleet] created Orchestrator (test/direct mode)")
    return orch


def _agent_card(agent) -> dict[str, Any]:
    """Project a LiveAgent into the dashboard card shape (SPEC §9)."""
    stats = agent.stats.to_dict() if agent.stats else {}
    return {
        "id": agent.agent_id,
        "name": agent.name,
        "state": agent.state,
        "provider": agent.provider,
        "model": agent.model,
        "key_name": agent.key_name,
        "skills": agent.skills or [],
        "last_activity": agent.last_activity or agent.created_at or "",
        "created_at": agent.created_at or "",
        "stats": {
            "tasks_done": stats.get("tasks_done", 0),
            "tasks_failed": stats.get("tasks_failed", 0),
            "tokens": stats.get("tokens", 0),
        },
        "memory_summary": agent.summary or "",
        "current_task": agent.current_task,
        # Whether a usable credential was found for this worker's provider. A
        # worker can be IDLE and still be unable to do anything, and that has to
        # be visible instead of discovered by a failing task.
        "binding_status": agent.binding_status,
    }


def _state_label(state: str) -> str:
    """Human-readable state label for the dashboard pill."""
    labels = {
        IDLE: "idle",
        WORKING: "working",
        "THINKING": "thinking",
        "BLOCKED": "blocked",
        PAUSED: "paused",
        SLEEPING: "sleeping",
        ERROR: "error",
        DESTROYED: "destroyed",
        "SPAWNING": "spawning",
    }
    return labels.get(state, state.lower())
