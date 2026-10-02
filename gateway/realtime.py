"""Realtime gateway surface: async job intake, SSE streaming, WS duplex control.

Mounted by ``gateway.gateway`` via :func:`install`, which keeps the 2.4k-line
monolith out of this concern:

* ``POST /jobs`` … — enqueue work and get an id back immediately (no request is
  held open while a tool loop runs).
* ``GET /jobs/{id}/events`` and ``GET /stream/run/{run_id}`` — **SSE**: token
  deltas + step-by-step tool feedback, replayable via ``Last-Event-ID``.
* ``POST /stream/command`` — submit + stream in one call (dashboard/TUI use).
* ``WS /ws/agent`` — **bi-directional**: chat/cancel/subscribe frames in, run
  events out, so a client can interrupt a long run mid-flight.
* ``/memory/*``, ``/skills/forge/*``, ``/sandbox/*``, ``/delegate*`` — the new
  subsystems exposed over HTTP the same way the rest of the gateway is.

Auth follows the existing gateway convention (``HERMUS_GATEWAY_TOKEN``); SSE/WS
accept it as ``?token=`` too, since browsers cannot set headers on EventSource.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse

from core.config import config
from core.log import get_logger
from core.run_events import RunBus, run_bus, sse_format

logger = get_logger(__name__)

router = APIRouter()

# The bidirectional WebSocket is mounted on its own router so the control-plane
# HTTP/SSE router above can be gated by the optional gateway token WITHOUT the
# dependency running ahead of the WS handler's own 1008-close auth (a failing
# router dependency on a WebSocket closes it with the wrong code).
ws_router = APIRouter()

_agent_getter: Callable[..., Any] | None = None


def _auth_ok(token: str | None, header_token: str | None) -> bool:
    import hmac

    expected = config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN")
    if not expected:
        return True
    return hmac.compare_digest(str(token or header_token or ""), str(expected))


# ------------------------------------------------------------------ SSE helpers
async def _stream_run(
    bus: RunBus,
    run_id: str,
    *,
    after: int = 0,
    keepalive: float = 15.0,
    max_seconds: float = 1800.0,
    request: Request | None = None,
    stop_on: str = "run_finished",
) -> Any:
    """Generator yielding SSE frames for a run, live until it finishes (or the
    client disconnects). Late subscribers replay what they missed."""
    loop = asyncio.get_running_loop()
    aq, unsubscribe = bus.subscribe(run_id, loop=loop, after=after)
    started = loop.time()
    try:
        yield "retry: 1500\n\n"
        while True:
            if request is not None and await request.is_disconnected():
                break
            if loop.time() - started > max_seconds:
                yield sse_format(
                    {"id": 0, "run_id": run_id, "type": "stream_timeout", "ts": _ts(), "data": {"max_seconds": max_seconds}}
                )
                break
            try:
                event = await asyncio.wait_for(aq.get(), timeout=keepalive)
            except asyncio.TimeoutError:
                yield f": ping {_ts()}\n\n"
                continue
            etype = event.get("type")
            if etype == "__closed__":
                yield sse_format(
                    {
                        "id": int(event.get("id") or 0),
                        "run_id": run_id,
                        "type": "stream_end",
                        "ts": _ts(),
                        "data": {"status": event.get("status")},
                    }
                )
                break
            yield sse_format(event)
            if stop_on and etype == stop_on:
                # one trailing beat so a client that reconnects sees the end marker
                await asyncio.sleep(0.05)
                break
    finally:
        unsubscribe()


def _ts() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ------------------------------------------------------------------ job intake
@router.post("/jobs")
async def submit_job(payload: dict[str, Any] = None):
    """Enqueue any registered job kind and return immediately with a job id."""
    payload = payload or {}
    from gateway.queue import job_queue

    kind = str(payload.get("kind") or "agent.chat")
    body = dict(payload.get("payload") or {})
    if not body.get("text") and payload.get("text"):
        body["text"] = payload["text"]
    session_key = str(payload.get("session_key") or f"{body.get('platform', 'api')}:{body.get('user_id', 'anonymous')}")
    try:
        job = job_queue.submit(
            kind,
            body,
            session_key=session_key,
            priority=int(payload.get("priority", 0)),
            timeout=payload.get("timeout"),
            max_attempts=payload.get("max_attempts"),
            dedupe_key=str(payload.get("dedupe_key") or ""),
        )
    except KeyError as e:
        return JSONResponse({"error": str(e), "kinds": sorted(job_queue.handlers)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    session_id = str(body.get("session_id") or payload.get("session_id") or "")
    if session_id:
        from core.conversation import conversation_manager
        conversation_manager.get_or_create(
            session_id,
            user_id=str(body.get("user_id") or "anonymous"),
            platform=str(body.get("platform") or "api"),
        )
        conversation_manager.attach_run(session_id, job.run_id)
        if body.get("text"):
            conversation_manager.add_turn(session_id, "user", str(body["text"]), run_id=job.run_id)
    return {
        "job_id": job.id,
        "run_id": job.run_id,
        "status": job.status,
        "kind": job.kind,
        "session_id": session_id or None,
        "status_url": f"/jobs/{job.id}",
        "events_url": f"/jobs/{job.id}/events",
        "stream_url": f"/stream/run/{job.run_id}",
    }


@router.get("/jobs")
async def list_jobs(limit: int = 50, status: str = None, session_key: str = None):
    from gateway.queue import job_queue

    return {
        "queue": job_queue.status(),
        "jobs": job_queue.list_jobs(limit=limit, status=status, session_key=session_key),
    }


@router.get("/jobs/{job_id}")
async def job_status(job_id: str):
    """Status of one job.

    ``found`` (not the mere presence of an ``error`` key) decides 404: every
    job carries an ``error`` field that is simply empty on success, so the old
    ``"error" in st`` test answered 404 for jobs that had already succeeded and
    broke the dashboard's job-poll fallback.
    """
    from gateway.queue import job_queue

    st = job_queue.status(job_id)
    if st.get("found") is False:
        return JSONResponse(st, status_code=404)
    return st


@router.get("/jobs/{job_id}/result")
async def job_result(job_id: str):
    from gateway.queue import job_queue

    res = job_queue.result(job_id)
    if res is None:
        st = job_queue.status(job_id)
        if st.get("found") is False:
            return JSONResponse({"error": st.get("error") or "unknown job", "status": st.get("status")}, status_code=404)
        return JSONResponse({"error": "result not ready", "status": st.get("status")}, status_code=409)
    return {"job_id": job_id, "result": res}


@router.post("/jobs/{job_id}/cancel")
async def job_cancel(job_id: str):
    from gateway.queue import job_queue

    return job_queue.cancel(job_id)


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, follow: bool = True, after: int = 0):
    """SSE feed of one job's run (steps, tool calls, tokens). ``follow=false`` = snapshot."""
    from gateway.queue import job_queue

    st = job_queue.status(job_id)
    if st.get("found") is False:
        return JSONResponse(st, status_code=404)
    run_id = st.get("run_id") or job_id
    if not follow:
        return {"job_id": job_id, "run_id": run_id, "events": run_bus.history(run_id, after=after)}
    return StreamingResponse(
        _stream_run(run_bus, run_id, after=after, request=request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no", "X-Hermus-Run": str(run_id)},
    )


@router.get("/stream/run/{run_id}")
async def stream_run(run_id: str, request: Request, after: int = 0):
    """SSE for any run id (queue jobs, direct /command runs, delegations)."""
    return StreamingResponse(
        _stream_run(run_bus, run_id, after=after, request=request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no", "X-Hermus-Run": run_id},
    )


@router.post("/stream/command")
async def stream_command(payload: dict[str, Any] = None, request: Request = None):
    """Run a turn and stream it as SSE (single call). Non-streaming clients keep /command.

    Submits the canonical ``runtime.turn`` job (auto-classified chat vs mission
    by the universal runtime) — the dashboard's queue-first path uses exactly
    the same kind.
    """
    from gateway.queue import job_queue

    payload = payload or {}
    text = str(payload.get("text") or "")
    if not text.strip():
        return JSONResponse({"error": "text required"}, status_code=400)
    body = dict(payload)
    body["stream"] = bool(payload.get("stream", True))
    prefer = str(payload.get("prefer") or ("mission" if payload.get("autonomous") else "auto")).lower()
    body["prefer"] = prefer
    job = job_queue.submit(
        "runtime.turn",
        body,
        session_key=f"{payload.get('platform', 'api')}:{payload.get('user_id', 'anonymous')}",
        timeout=payload.get("timeout"),
    )
    session_id = str(payload.get("session_id") or "")
    if session_id:
        from core.conversation import conversation_manager
        conversation_manager.get_or_create(
            session_id,
            user_id=str(payload.get("user_id") or "anonymous"),
            platform=str(payload.get("platform") or "api"),
        )
        conversation_manager.attach_run(session_id, job.run_id)
        conversation_manager.add_turn(session_id, "user", text, run_id=job.run_id)
    return StreamingResponse(
        _stream_run(run_bus, job.run_id, request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Hermus-Job": job.id,
            "X-Hermus-Run": job.run_id,
        },
    )


@router.post("/conversation/session")
async def conversation_session(payload: dict[str, Any] = None):
    payload = payload or {}
    from core.conversation import conversation_manager
    session = conversation_manager.get_or_create(
        payload.get("session_id"),
        user_id=str(payload.get("user_id") or "anonymous"),
        platform=str(payload.get("platform") or "api"),
    )
    return session.snapshot()


@router.get("/conversation/{session_id}")
async def conversation_get(session_id: str, limit: int = 12):
    from core.conversation import conversation_manager
    snap = conversation_manager.snapshot(session_id)
    snap["context"] = conversation_manager.context(session_id, limit=limit)
    return snap


@router.post("/conversation/{session_id}/steer")
async def conversation_steer(session_id: str, payload: dict[str, Any] = None):
    payload = payload or {}
    instruction = str(payload.get("instruction") or payload.get("text") or "").strip()
    if not instruction:
        return JSONResponse({"error": "instruction is required"}, status_code=400)
    from core.conversation import conversation_manager
    return conversation_manager.steer(session_id, instruction)


@router.post("/conversation/{session_id}/interrupt")
async def conversation_interrupt(session_id: str, payload: dict[str, Any] = None):
    payload = payload or {}
    from core.conversation import conversation_manager
    return conversation_manager.interrupt(session_id, reason=str(payload.get("reason") or "user_interrupt"))


@router.get("/conversation/{session_id}/notifications")
async def conversation_notifications(session_id: str, consume: bool = False):
    from core.conversation import conversation_manager
    return {
        "session_id": session_id,
        "notifications": conversation_manager.notifications(session_id, consume=consume),
    }


@router.post("/runs/{run_id}/steer")
async def run_steer(run_id: str, payload: dict[str, Any] = None):
    payload = payload or {}
    instruction = str(payload.get("instruction") or payload.get("text") or "").strip()
    if not instruction:
        return JSONResponse({"error": "instruction is required"}, status_code=400)
    from core.run_events import run_bus
    if not run_bus.steer(run_id, instruction):
        return JSONResponse({"error": "run not found or instruction rejected", "run_id": run_id}, status_code=404)
    return {"ok": True, "run_id": run_id, "action": "steer"}


@router.post("/runs/{run_id}/interrupt")
async def run_interrupt(run_id: str):
    from core.run_events import run_bus
    if not run_bus.cancel(run_id):
        return JSONResponse({"error": "run not found", "run_id": run_id}, status_code=404)
    return {"ok": True, "run_id": run_id, "action": "interrupt"}


@router.get("/queue/status")
async def queue_status():
    from gateway.queue import job_queue

    return {"queue": job_queue.status(), "runs": run_bus.runs()[-20:]}


# ---------------------------------------------------------------- bi-directional WS
@ws_router.websocket("/ws/agent")
async def ws_agent(websocket: WebSocket):
    """Bidirectional agent channel.

    client → server frames:
        {"action":"chat","text":"…","model":"…","platform":"ws","user_id":"…","stream":true}
        {"action":"autonomous","text":"…"}
        {"action":"cancel","job_id":"…"}          # cooperative, stops at next step
        {"action":"subscribe","run_id":"…"}       # attach to a run already going
        {"action":"tool","name":"…","args":{}}   # direct tool call (still permission-gated)
        {"action":"ping"}
    server → client: every run event (token deltas, tool calls/results, steps)
    plus {"type":"ack"|"result"|"error"|"pong"|"hello"}.
    """
    token = websocket.query_params.get("token") or websocket.headers.get("X-Hermus-Token")
    if not _auth_ok(token, None):
        await websocket.close(code=1008, reason="Unauthorized")
        return
    await websocket.accept()
    from gateway.queue import job_queue

    send_lock = asyncio.Lock()

    async def send(obj: dict[str, Any]) -> None:
        async with send_lock:
            try:
                await websocket.send_json(obj)
            except Exception:
                pass

    await send(
        {
            "type": "hello",
            "protocol": "hermus.agent.v1",
            "actions": ["chat", "autonomous", "cancel", "steer", "redirect", "subscribe", "tool", "ping"],
            "kinds": sorted(job_queue.handlers),
            "queue": {"workers": job_queue.workers, "enabled": job_queue.enabled},
            "sandbox": _safe(lambda: __import__("core.sandbox", fromlist=["sandbox"]).sandbox.status()["backend"]),
            "memory_index": _safe(lambda: _memory_index_stats()),
            "ts": _ts(),
        }
    )

    streams: dict[str, asyncio.Task] = {}

    async def pump_events(run_id: str, source: str = "") -> None:
        try:
            async for frame in _stream_run(run_bus, run_id, keepalive=20.0, max_seconds=1800.0):
                # SSE frames → JSON messages for the socket
                payload_line = None
                for line in frame.splitlines():
                    if line.startswith("data: "):
                        payload_line = line[6:]
                if payload_line:
                    try:
                        event = json.loads(payload_line)
                    except Exception:
                        continue
                    if source:
                        event["source"] = source
                    await send(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await send({"type": "stream_error", "run_id": run_id, "error": str(e)[:200]})

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            try:
                msg = json.loads(raw) if raw.strip() else {}
            except Exception:
                await send({"type": "error", "error": "invalid JSON frame"})
                continue
            action = str(msg.get("action") or msg.get("type") or "").lower()

            if action in ("chat", "autonomous", "delegate"):
                kind = {"chat": "agent.chat", "autonomous": "agent.autonomous", "delegate": "subagent.delegate"}[action]
                body = {k: v for k, v in msg.items() if k not in ("action", "type")}
                if action == "delegate" and not body.get("goal") and body.get("text"):
                    body["goal"] = body["text"]
                try:
                    job = job_queue.submit(
                        kind,
                        body,
                        session_key=f"ws:{body.get('user_id') or msg.get('user_id') or 'guest'}",
                        timeout=body.get("timeout"),
                    )
                except Exception as e:
                    await send({"type": "error", "error": str(e)[:300], "action": action})
                    continue
                session_id = str(body.get("session_id") or msg.get("session_id") or "")
                if session_id:
                    from core.conversation import conversation_manager
                    conversation_manager.get_or_create(
                        session_id,
                        user_id=str(body.get("user_id") or msg.get("user_id") or "guest"),
                        platform=str(body.get("platform") or "ws"),
                    )
                    conversation_manager.attach_run(session_id, job.run_id)
                    if body.get("text"):
                        conversation_manager.add_turn(session_id, "user", str(body["text"]), run_id=job.run_id)
                await send({"type": "ack", "job_id": job.id, "run_id": job.run_id, "kind": kind, "session_id": session_id or None})
                streams[job.run_id] = asyncio.create_task(pump_events(job.run_id, source=job.id))
                continue

            if action == "subscribe":
                run_id = str(msg.get("run_id") or "")
                if not run_id:
                    await send({"type": "error", "error": "run_id required"})
                    continue
                await send(
                    {
                        "type": "subscribe_ok",
                        "run_id": run_id,
                        "replayed": len(run_bus.history(run_id, after=int(msg.get("after") or 0))),
                    }
                )
                streams[run_id] = asyncio.create_task(pump_events(run_id, source="subscribe"))
                continue

            if action == "cancel":
                from gateway.queue import job_queue as jq

                job_id = str(msg.get("job_id") or "")
                res = jq.cancel(job_id) if job_id else jq.cancel(str(msg.get("run_id") or ""))
                await send({"type": "cancel_result", "result": res})
                continue

            if action in ("steer", "redirect"):
                run_id = str(msg.get("run_id") or "")
                instruction = str(msg.get("instruction") or msg.get("text") or "").strip()
                if not run_id or not instruction:
                    await send({"type": "error", "error": "run_id and instruction required", "action": action})
                    continue
                ok = run_bus.steer(run_id, instruction)
                await send({"type": "steer_result", "ok": ok, "run_id": run_id, "instruction": instruction[:500]})
                continue

            if action == "tool":
                name = str(msg.get("name") or msg.get("tool") or "")
                args = msg.get("args") or msg.get("arguments") or {}
                try:
                    # §5 canonical path: gateway tool calls go through ToolGateway.
                    from core.tools import gateway_result_dict, get_tool_gateway

                    def _run(name=name, args=args):
                        r = get_tool_gateway().execute(name, args if isinstance(args, dict) else {}, actor="realtime")
                        return gateway_result_dict(r)

                    result = await asyncio.to_thread(_run)
                    await send({"type": "tool_result", "tool": name, "result": result})
                except Exception as e:
                    await send({"type": "error", "error": str(e)[:300], "tool": name})
                continue

            if action == "memory":
                try:
                    from core.memory import memory

                    out = await asyncio.to_thread(
                        memory.hybrid_recall,
                        str(msg.get("query") or ""),
                        limit=int(msg.get("limit") or 6),
                    )
                    await send(
                        {
                            "type": "memory_result",
                            "query": msg.get("query"),
                            "hits": [
                                {
                                    "id": h.get("id"),
                                    "kind": h.get("kind"),
                                    "score": h.get("score"),
                                    "rrf": h.get("rrf_score"),
                                    "text": (h.get("content") or "")[:300],
                                }
                                for h in out
                            ],
                        }
                    )
                except Exception as e:
                    await send({"type": "error", "error": str(e)[:300]})
                continue

            if action in ("ping", ""):
                await send({"type": "pong", "ts": _ts(), "queue": _safe(lambda: dict(job_queue.status()["by_status"]))})
                continue

            await send(
                {
                    "type": "error",
                    "error": f"unknown action '{action}'",
                    "actions": ["chat", "autonomous", "delegate", "subscribe", "cancel", "tool", "memory", "ping"],
                }
            )
    except Exception:
        pass
    finally:
        for task in streams.values():
            task.cancel()


def _hello(job_queue) -> dict[str, Any]:
    """Capabilities frame sent right after a WS handshake."""
    from core.memory import memory
    from core.sandbox import sandbox as jail

    return {
        "type": "hello",
        "protocol": "hermus.agent.v1",
        "actions": ["chat", "autonomous", "delegate", "subscribe", "cancel", "tool", "memory", "ping"],
        "kinds": sorted(job_queue.handlers),
        "queue": {"workers": job_queue.workers, "enabled": job_queue.enabled, "backend": job_queue.backend},
        "sandbox": _safe(lambda: jail.status()["backend"]),
        "memory_index": _safe(lambda: memory.index_stats()),
        "ts": _ts(),
    }


def _memory_index_stats() -> dict[str, Any]:
    try:
        from core.memory import memory

        return memory.index_stats()
    except Exception:
        return {}


def _safe(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception as e:
        return {"error": str(e)[:120]}


# ------------------------------------------------------------- subsystem routes
@router.post("/memory/hybrid")
async def memory_hybrid(payload: dict[str, Any] = None):
    """Hybrid recall over typed memory (BM25 + vectors + RRF + decay)."""
    payload = payload or {}
    from core.memory import memory

    query = str(payload.get("query") or "")
    if not query.strip():
        return JSONResponse({"error": "query required"}, status_code=400)
    limit = int(payload.get("limit") or 8)
    project = payload.get("project") or None
    kinds = payload.get("kinds") or None
    if payload.get("explain"):
        return await asyncio.to_thread(memory.explain, query, limit, project=project, kinds=kinds)
    hits = await asyncio.to_thread(memory.hybrid_recall, query, project=project, kinds=kinds, limit=limit)
    return {
        "query": query,
        "mode": "hybrid",
        "count": len(hits),
        "index": memory.index_stats(),
        "results": [
            {
                "id": h.get("id"),
                "kind": h.get("kind"),
                "score": h.get("score"),
                "rrf_score": h.get("rrf_score"),
                "decay": h.get("decay"),
                "retrieval": h.get("retrieval"),
                "signals": h.get("signals"),
                "content": (h.get("content") or "")[:700],
            }
            for h in hits
        ],
    }


@router.post("/memory/remember")
async def memory_remember(payload: dict[str, Any] = None):
    from core.memory import memory

    payload = payload or {}
    res = await asyncio.to_thread(
        memory.remember,
        str(payload.get("kind") or "semantic"),
        str(payload.get("content") or ""),
        project=payload.get("project"),
        importance=float(payload.get("importance", 5.0)),
        success=payload.get("success"),
        ttl_hours=payload.get("ttl_hours"),
        pinned=bool(payload.get("pinned")),
        metadata=payload.get("metadata") or None,
    )
    return res


@router.post("/memory/sweep")
async def memory_sweep(payload: dict[str, Any] = None):
    payload = payload or {}
    if not payload.get("confirm"):
        return JSONResponse({"error": "sweep archives/purges memory; send confirm=true"}, status_code=400)
    from core.memory import memory

    return await asyncio.to_thread(
        memory.sweep,
        project=payload.get("project") or None,
        dry_run=bool(payload.get("dry_run", False)),
    )


@router.get("/memory/stats")
async def memory_stats():
    from core.memory import memory

    return await asyncio.to_thread(memory.stats)


@router.post("/memory/reindex")
async def memory_reindex(payload: dict[str, Any] = None):
    """Rebuild the FTS + vector indexes (after switching embedding model)."""
    from core.memory import memory

    return await asyncio.to_thread(memory.reindex)


@router.get("/memory/access-log")
async def memory_access_log(memory_id: int, limit: int = 20):
    from core.memory import memory

    return {"memory_id": memory_id, "access": memory.access_log(memory_id, limit=limit)}


# ---- skill forge ---------------------------------------------------------------
@router.post("/skills/forge/harvest")
async def skill_forge_harvest(payload: dict[str, Any] = None):
    """Harvest a trajectory into a validated skill. Provide goal + trajectory, or a session id."""
    payload = payload or {}
    from core.skill_forge import skill_forge

    traj = payload.get("trajectory")
    if not traj:
        return JSONResponse({"error": "trajectory required (list of turns with tool_calls)"}, status_code=400)
    return await asyncio.to_thread(
        skill_forge.harvest,
        str(payload.get("goal") or payload.get("text") or ""),
        traj,
        verification=payload.get("verification"),
        tool_results=payload.get("tool_results"),
        session_id=str(payload.get("session_id") or "api"),
        dry_run=bool(payload.get("dry_run")),
    )


@router.get("/skills/forge/stats")
async def skill_forge_stats():
    from core.skill_forge import skill_forge

    reg = skill_forge.index()
    return {"stats": skill_forge.stats(), "skills": {k: v for k, v in list(reg["skills"].items())[-40:]}}


@router.post("/skills/forge/run")
async def skill_forge_run(payload: dict[str, Any] = None):
    payload = payload or {}
    name = str(payload.get("name") or "")
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    from core.skill_forge import skill_forge

    return await asyncio.to_thread(
        skill_forge.run,
        name,
        **{k: v for k, v in payload.items() if k != "name"},
    )


@router.post("/skills/forge/validate")
async def skill_forge_validate(payload: dict[str, Any] = None):
    payload = payload or {}
    from pathlib import Path

    from core.skill_forge import skill_forge

    path = str(payload.get("path") or (Path(skill_forge.skills_dir) / str(payload.get("name") or "")))
    if not Path(path).exists():
        return JSONResponse({"error": f"not found: {path}"}, status_code=404)
    return await asyncio.to_thread(skill_forge.validate, Path(path))


# ---- sandbox ---------------------------------------------------------------
@router.get("/sandbox/status")
async def sandbox_status():
    from core.sandbox import sandbox

    return await asyncio.to_thread(sandbox.status)


@router.post("/sandbox/run")
async def sandbox_run(payload: dict[str, Any] = None):
    payload = payload or {}
    command = str(payload.get("command") or "")
    if not command.strip():
        return JSONResponse({"error": "command required"}, status_code=400)
    from core.sandbox import sandbox

    res = await asyncio.to_thread(
        sandbox.run,
        command,
        timeout=int(payload.get("timeout") or 0) or None,
        cwd=payload.get("cwd"),
        network=payload.get("network"),
        policy=payload.get("policy") or None,
        allow_dangerous=bool(payload.get("allow_dangerous")),
        purpose="api:/sandbox/run",
    )
    denied = res.get("returncode") == 126 and "blocked by sandbox policy" in str(res.get("error"))
    return JSONResponse(res, status_code=403 if denied else 200)


@router.get("/sandbox/recent")
async def sandbox_recent(limit: int = 20):
    """Last sandbox executions from the audit log."""
    try:
        from core.workspace import workspace

        path = workspace.dirs["logs"] / "sandbox.jsonl"
        lines = path.read_text(errors="ignore").splitlines()[-limit:] if path.exists() else []
        return {"entries": [json.loads(x) for x in reversed(lines) if x.strip()], "path": str(path)}
    except Exception as e:
        return {"error": str(e), "entries": []}


# ---- delegation ---------------------------------------------------------------
@router.post("/delegate")
async def delegate(payload: dict[str, Any] = None):
    """Fan out work to parallel sub-agents (processes + JSON-RPC) and aggregate.

    The delegation always runs as a canonical ``subagent.delegate`` Job on the
    JobQueue (async: return the job id; sync: block for its structured result) so
    the lifecycle is owned by the queue.
    """
    payload = payload or {}
    tasks = payload.get("tasks")
    goal = str(payload.get("goal") or payload.get("text") or "")
    if payload.get("async"):
        from gateway.queue import job_queue

        job = job_queue.submit("subagent.delegate", payload, session_key=f"delegate:{goal[:40]}")
        return {"job_id": job.id, "run_id": job.run_id, "status": job.status, "events_url": f"/jobs/{job.id}/events"}
    if not (tasks or goal):
        return JSONResponse({"error": "goal or tasks required"}, status_code=400)
    # Canonical path: even the synchronous form of the /delegate endpoint runs the
    # delegation as a ``subagent.delegate`` Job on the JobQueue (lifecycle owned by
    # the queue), then blocks for its structured result — never calling the
    # delegation module directly.
    from subagents.subagent import DELEGATE_JOB, submit_and_wait

    # Run the blocking submit+wait off the event loop so a live gateway (whose
    # queue owns a running loop) can drive the job without deadlocking.
    st = await asyncio.to_thread(
        submit_and_wait,
        DELEGATE_JOB,
        payload,
        session_key=f"delegate:{str(goal)[:40]}",
        timeout=float(payload.get("timeout") or 300.0),
    )
    if st.get("status") == "failed":
        return {"ok": False, "error": st.get("error") or "delegate job failed", "job_id": st.get("job_id")}
    res = st.get("result") or {"ok": False, "error": "no result"}
    res["job_id"] = st.get("job_id") or res.get("job_id")
    return res


@router.get("/delegation/status")
async def delegation_status():
    from core.delegation import delegation

    return await asyncio.to_thread(delegation.status)


@router.get("/delegation/{tree_id}")
async def delegation_tree(tree_id: str):
    from core.delegation import delegation

    out = delegation.tree(tree_id)
    if "error" in out:
        return JSONResponse(out, status_code=404)
    return out


@router.post("/delegation/{tree_id}/cancel")
async def delegation_cancel(tree_id: str):
    from core.delegation import delegation

    return delegation.cancel_tree(tree_id)


@router.get("/runs")
async def list_runs(limit: int = 30):
    return {"runs": run_bus.runs()[-limit:]}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, limit: int = 200):
    snap = run_bus.snapshot(run_id)
    snap["events"] = run_bus.history(run_id, limit=limit)
    if "exists" in snap and not snap["exists"]:
        return JSONResponse(snap, status_code=404)
    return snap


# ---- missions & DAG -----------------------------------------------------------
@router.post("/missions")
async def mission_start_api(payload: dict[str, Any] = None):
    payload = payload or {}
    goal = str(payload.get("goal") or payload.get("text") or "")
    if not goal:
        return JSONResponse({"error": "goal is required"}, status_code=400)
    from core.mission import mission_engine

    report = await asyncio.to_thread(
        mission_engine.start_mission,
        goal=goal,
        requirements=payload.get("requirements"),
        domain=payload.get("domain"),
        subgoals=payload.get("subgoals"),
        budget_steps=(int(payload["budget_steps"]) if payload.get("budget_steps") not in (None, "") else None),
        preflight=str(payload.get("preflight", True)).lower() not in {"0", "false", "no"},
        allow_preflight_planning=str(payload.get("allow_preflight_planning", False)).lower() in {"1", "true", "yes"},
    )
    return report.to_dict()


@router.get("/missions")
async def mission_list_api():
    from core.mission import mission_engine

    missions = await asyncio.to_thread(mission_engine.list_missions)
    return {"missions": [m.to_dict() for m in missions]}


@router.get("/missions/{mission_id}")
async def mission_get_api(mission_id: str):
    from core.mission import mission_engine

    report = await asyncio.to_thread(mission_engine.get_mission, mission_id)
    if not report:
        return JSONResponse({"error": f"Mission {mission_id} not found"}, status_code=404)
    return report.to_dict()


@router.post("/missions/{mission_id}/preflight/approvals")
async def mission_preflight_approvals_api(mission_id: str):
    from core.autonomy_preflight import create_preflight_approval_requests
    from core.mission import mission_engine

    report = await asyncio.to_thread(mission_engine.get_mission, mission_id)
    if not report:
        return JSONResponse({"error": f"Mission {mission_id} not found"}, status_code=404)
    result = await asyncio.to_thread(create_preflight_approval_requests, report.goal, mission_id=mission_id, bundle=True)
    result["mission_id"] = mission_id
    return result


@router.post("/missions/{mission_id}/resume")
async def mission_resume_api(
    mission_id: str,
    restart_failed: bool = False,
    extra_steps: int | None = None,
    payload: dict[str, Any] = None,
):
    """Resume a blocked/interrupted mission — or restart a failed one.

    ``failed`` is terminal by default: pass ``restart_failed=true`` (explicit
    recovery) so a crash-looping mission is never auto-resumed by accident.
    """
    payload = payload or {}
    restart = bool(restart_failed or payload.get("restart_failed"))
    steps = extra_steps if extra_steps is not None else payload.get("extra_steps")
    from core.mission import mission_engine

    try:
        report = await asyncio.to_thread(
            mission_engine.resume_mission,
            mission_id,
            restart_failed=restart,
            extra_steps=int(steps) if steps not in (None, "") else None,
        )
        return report.to_dict()
    except ValueError as e:
        # terminal / unrecoverable: say why, and how to recover
        from core.mission import mission_engine as _me

        current = await asyncio.to_thread(_me.get_mission, mission_id)
        body: dict[str, Any] = {"error": str(e), "mission_id": mission_id, "restart_failed": restart}
        if current is not None:
            body["state"] = current.state
            body["failure"] = current.failure_summary()
        return JSONResponse(body, status_code=409)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/missions/{mission_id}/extend")
async def mission_extend_api(
    mission_id: str,
    steps: int = 10,
    emergency: bool = False,
    payload: dict[str, Any] = None,
):
    """Grant extra step budget (normal slot, or the emergency reserve)."""
    payload = payload or {}
    from core.mission import mission_engine

    n = int(payload.get("steps", steps) or steps)
    try:
        report = await asyncio.to_thread(
            mission_engine.extend_budget,
            mission_id,
            n,
            emergency=bool(emergency or payload.get("emergency")),
        )
        return report.to_dict()
    except ValueError as e:
        return JSONResponse({"error": str(e), "mission_id": mission_id}, status_code=409)


@router.get("/models/capabilities")
async def models_capabilities_api(model: str | None = None, needs_vision: bool = False, needs_computer: bool = False):
    """Pre-flight capability negotiation for the current (or a given) model.

    Answers, before a run starts: tools? vision? long context? structured
    outputs? streaming? computer control? — and recommends a compatible model
    when the selected one cannot do the job.
    """
    from core.config import config
    from core.model_capabilities import mission_capability_gate

    ref = model or str(getattr(config, "model", "") or "")
    return await asyncio.to_thread(
        mission_capability_gate,
        ref,
        needs_vision=needs_vision,
        needs_computer=needs_computer,
    )


# ---- artifacts ----------------------------------------------------------------
@router.get("/artifacts")
async def artifacts_list_api(mission_id: str | None = None, artifact_type: str | None = None):
    from core.artifact_manager import artifact_manager

    arts = await asyncio.to_thread(artifact_manager.list_artifacts, mission_id=mission_id, artifact_type=artifact_type)
    return {"count": len(arts), "artifacts": [a.to_dict() for a in arts]}


@router.get("/artifacts/{artifact_id}")
async def artifact_get_api(artifact_id: str):
    from core.artifact_manager import artifact_manager

    art = await asyncio.to_thread(artifact_manager.get_artifact, artifact_id)
    if not art:
        return JSONResponse({"error": f"Artifact {artifact_id} not found"}, status_code=404)
    return art.to_dict()


@router.post("/artifacts/export")
async def artifact_export_api(payload: dict[str, Any] = None):
    payload = payload or {}
    output_path = payload.get("output_path", "artifacts_bundle.zip")
    from core.artifact_manager import artifact_manager

    try:
        p = await asyncio.to_thread(
            artifact_manager.export_bundle,
            output_zip_path=output_path,
            mission_id=payload.get("mission_id"),
            artifact_ids=payload.get("artifact_ids"),
        )
        return {"success": True, "bundle_path": p}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---- verifiers & SWE ----------------------------------------------------------
@router.get("/verifiers/domains")
async def verifiers_list_domains():
    from core.verifier_registry import verifier_registry

    return {"domains": verifier_registry.list_domains()}


@router.post("/verifiers/verify")
async def verifiers_verify_api(payload: dict[str, Any] = None):
    payload = payload or {}
    from core.verifier_registry import verifier_registry

    res = await asyncio.to_thread(
        verifier_registry.verify,
        domain_or_auto=payload.get("domain", "auto"),
        context=payload.get("context", payload),
    )
    return res.to_dict()


@router.post("/swe/run")
async def swe_run_api(payload: dict[str, Any] = None):
    """Run the SWE lifecycle with an agent-backed coder phase.

    The coder stage executes on the same agent runtime as chat/missions (real
    tools, real diffs as evidence) instead of a deterministic template.
    """
    payload = payload or {}
    task = str(payload.get("task") or payload.get("text") or "")
    if not task:
        return JSONResponse({"error": "task is required"}, status_code=400)
    from core.swe_mode import swe_mode

    agent = None
    if _agent_getter is not None and not payload.get("no_agent"):
        try:
            agent = _agent_getter(
                payload.get("platform", "api"),
                payload.get("user_id", "swe"),
                model=payload.get("model"),
                mode="agent",
                api_key=payload.get("api_key"),
                base_url=payload.get("base_url"),
            )
        except Exception:
            agent = None
    res = await asyncio.to_thread(
        swe_mode.execute,
        task=task,
        max_repairs=int(payload.get("max_repairs", 3)),
        agent=agent,
    )
    return res.to_dict()


@router.get("/automation/rules")
async def automation_rules():
    from core.proactive_runtime import automation, wire_proactive_automation
    wire_proactive_automation()
    return {"rules": automation.list_rules()}


@router.post("/automation/rules")
async def automation_rule_create(payload: dict[str, Any] = None):
    payload = payload or {}
    from core.proactive_runtime import automation, wire_proactive_automation
    wire_proactive_automation()
    try:
        rule = automation.add_rule(
            name=str(payload.get("name") or "automation"),
            event_type=str(payload.get("event_type") or ""),
            action_type=str(payload.get("action_type") or "runtime.turn"),
            task=str(payload.get("task") or ""),
            enabled=bool(payload.get("enabled", False)),
            cooldown_seconds=float(payload.get("cooldown_seconds", 60)),
            max_fires=(int(payload["max_fires"]) if payload.get("max_fires") not in (None, "") else None),
            filters=payload.get("filters") or {},
        )
        return rule.as_dict()
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/automation/rules/{rule_id}/enable")
async def automation_rule_enable(rule_id: str, payload: dict[str, Any] = None):
    from core.proactive_runtime import automation, wire_proactive_automation
    wire_proactive_automation()
    enabled = bool((payload or {}).get("enabled", True))
    if not automation.set_enabled(rule_id, enabled):
        return JSONResponse({"error": "rule not found"}, status_code=404)
    return {"rule_id": rule_id, "enabled": enabled}


@router.delete("/automation/rules/{rule_id}")
async def automation_rule_delete(rule_id: str):
    from core.proactive_runtime import automation, wire_proactive_automation
    wire_proactive_automation()
    if not automation.remove_rule(rule_id):
        return JSONResponse({"error": "rule not found"}, status_code=404)
    return {"deleted": True, "rule_id": rule_id}



@router.post("/multimodal/image")
async def multimodal_image(payload: dict[str, Any] = None):
    payload = payload or {}
    path = str(payload.get("path") or "")
    if not path:
        return JSONResponse({"error": "path is required"}, status_code=400)
    from core.multimodal import multimodal
    try:
        return multimodal.analyze_image(
            path,
            prompt=str(payload.get("prompt") or "Describe this image in detail"),
            model=str(payload.get("model") or "llava:7b"),
        )
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/multimodal/document")
async def multimodal_document(payload: dict[str, Any] = None):
    payload = payload or {}
    path = str(payload.get("path") or "")
    if not path:
        return JSONResponse({"error": "path is required"}, status_code=400)
    from core.multimodal import multimodal
    try:
        return multimodal.analyze_document(
            path,
            prompt=str(payload.get("prompt") or "Describe the visual contents and important text"),
            model=str(payload.get("model") or "llava:7b"),
        )
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/multimodal/browser")
async def multimodal_browser(payload: dict[str, Any] = None):
    payload = payload or {}
    from core.multimodal import multimodal
    try:
        return multimodal.analyze_browser(
            path=str(payload.get("path") or "data/multimodal/browser.png"),
            prompt=str(payload.get("prompt") or "Describe the current browser page, visible UI, text and important state"),
            model=str(payload.get("model") or "llava:7b"),
            full_page=bool(payload.get("full_page", False)),
        )
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/multimodal/status")
async def multimodal_status():
    from core.multimodal import multimodal
    return {
        "facts": [
            fact.to_dict()
            for fact in multimodal.world.query(subject="multimodal")
        ],
        "recent_events": [
            event.to_dict()
            for event in multimodal.world.recent_events(50)
            if event.event_type == "multimodal_observation"
        ],
    }


@router.get("/specialists")
async def specialists():
    from core.specialist_registry import specialist_registry
    return {"specialists": specialist_registry.list(), "max_active": specialist_registry.max_active}


@router.post("/plans/long-horizon")
async def long_horizon_plan(payload: dict[str, Any] = None):
    payload = payload or {}
    goal = str(payload.get("goal") or payload.get("text") or "")
    if not goal:
        return JSONResponse({"error": "goal is required"}, status_code=400)
    from core.long_horizon import long_horizon_planner
    plan = long_horizon_planner.build(
        goal,
        success_criteria=payload.get("success_criteria"),
        subgoals=payload.get("subgoals"),
    )
    return plan.to_dict()


@router.get("/distributed/status")
async def distributed_status():
    from core.distributed import distributed
    return distributed.status()


@router.get("/distributed/nodes")
async def distributed_nodes():
    from core.distributed import distributed
    return {"nodes": distributed.list_nodes()}


@router.post("/distributed/nodes")
async def distributed_register(payload: dict[str, Any] = None):
    payload = payload or {}
    from core.distributed import distributed
    return distributed.register_node(
        payload.get("name") or "HERMUS node",
        node_id=payload.get("node_id"),
        capabilities=payload.get("capabilities") or [],
        endpoint=payload.get("endpoint") or "",
        metadata=payload.get("metadata") or {},
    )


@router.post("/distributed/nodes/{node_id}/heartbeat")
async def distributed_heartbeat(node_id: str, payload: dict[str, Any] = None):
    payload = payload or {}
    from core.distributed import distributed
    return distributed.heartbeat(
        node_id,
        status=payload.get("status") or "online",
        capabilities=payload.get("capabilities"),
    )


@router.delete("/distributed/nodes/{node_id}")
async def distributed_unregister(node_id: str):
    from core.distributed import distributed
    return {"success": distributed.unregister(node_id)}


@router.post("/distributed/assign")
async def distributed_assign(payload: dict[str, Any] = None):
    payload = payload or {}
    from core.distributed import distributed
    return distributed.assign(
        payload.get("job_id") or "",
        capability=payload.get("capability") or "",
        node_id=payload.get("node_id"),
    )


@router.post("/distributed/assignments/{assignment_id}/complete")
async def distributed_complete(assignment_id: str, payload: dict[str, Any] = None):
    payload = payload or {}
    from core.distributed import distributed
    return distributed.complete_assignment(assignment_id, success=bool(payload.get("success")))


@router.get("/self-improvement/status")
async def self_improvement_status():
    from core.self_improvement import self_improvement
    from core.self_improvement_controller import self_improvement_controller
    return {
        "agent": self_improvement.get_status(),
        "governance": self_improvement_controller.status(),
    }


@router.get("/self-improvement/proposals")
async def self_improvement_proposals(limit: int = 20):
    from core.self_improvement_controller import self_improvement_controller
    return {"proposals": self_improvement_controller.history(limit)}


@router.post("/self-improvement/reflect")
async def self_improvement_reflect(payload: dict[str, Any] = None):
    payload = payload or {}
    from core.self_improvement import self_improvement
    trajectory = payload.get("trajectory")
    result = self_improvement.run_idle_reflection(
        trajectory=trajectory if isinstance(trajectory, list) else None,
        force=bool(payload.get("force", True)),
    )
    return result


@router.get("/personal-os")
async def personal_os_snapshot(query: str = "", area: str | None = None, project: str | None = None):
    from core.personal_os import personal_os
    return personal_os.snapshot(query=query, area=area, project=project)


@router.get("/personal-os/briefing")
async def personal_os_briefing(query: str = "", area: str | None = None):
    from core.personal_os import personal_os
    return personal_os.briefing(query=query, area=area)


@router.get("/personal-os/tasks")
async def personal_os_tasks(status: str | None = None, area: str | None = None, project: str | None = None, limit: int = 100):
    from core.personal_os import personal_os
    return {"tasks": personal_os.list_tasks(status=status, area=area, project=project, limit=limit)}


@router.post("/personal-os/tasks")
async def personal_os_task_create(payload: dict[str, Any] = None):
    payload = payload or {}
    from core.personal_os import personal_os
    try:
        return personal_os.add_task(
            str(payload.get("title") or payload.get("text") or ""),
            priority=str(payload.get("priority") or "normal"),
            area=str(payload.get("area") or "general"),
            project=payload.get("project"),
            due=payload.get("due"),
            notes=str(payload.get("notes") or ""),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.patch("/personal-os/tasks/{task_id}")
async def personal_os_task_update(task_id: str, payload: dict[str, Any] = None):
    payload = payload or {}
    from core.personal_os import personal_os
    try:
        return personal_os.update_task(task_id, **payload)
    except KeyError:
        return JSONResponse({"error": "task not found"}, status_code=404)


@router.post("/personal-os/tasks/{task_id}/complete")
async def personal_os_task_complete(task_id: str):
    from core.personal_os import personal_os
    try:
        return personal_os.complete_task(task_id)
    except KeyError:
        return JSONResponse({"error": "task not found"}, status_code=404)


@router.post("/personal-os/tasks/{task_id}/execute")
async def personal_os_task_execute(task_id: str):
    from core.personal_os import personal_os
    result = personal_os.execute_task(task_id)
    return JSONResponse(result, status_code=200 if result.get("success") else 400)


@router.delete("/personal-os/tasks/{task_id}")
async def personal_os_task_delete(task_id: str):
    from core.personal_os import personal_os
    if not personal_os.delete_task(task_id):
        return JSONResponse({"error": "task not found"}, status_code=404)
    return {"deleted": True, "task_id": task_id}


@router.get("/world")
async def world_state():
    from core.world_awareness import world_awareness
    return {"world": world_awareness.world.snapshot(), "awareness": world_awareness.status()}


@router.post("/world/refresh")
async def world_refresh(payload: dict[str, Any] = None):
    payload = payload or {}
    from core.world_awareness import world_awareness
    return world_awareness.refresh(
        workspace_root=payload.get("workspace_root"),
        include_processes=bool(payload.get("include_processes", True)),
    )


@router.get("/schedules")
async def schedules_list():
    from scheduler.cron import cron_manager
    return {"scheduler": cron_manager.status(), "schedules": cron_manager.list_jobs()}


@router.post("/schedules")
async def schedule_create(payload: dict[str, Any] = None):
    payload = payload or {}
    natural = str(payload.get("schedule") or payload.get("natural") or payload.get("when") or "")
    task = str(payload.get("task") or payload.get("text") or "")
    if not natural or not task:
        return JSONResponse({"error": "schedule and task are required"}, status_code=400)
    try:
        from scheduler.cron import cron_manager
        job = cron_manager.add_job(
            natural,
            task=task,
            platform=str(payload.get("platform") or "api"),
            user_id=str(payload.get("user_id") or "default"),
            timezone=payload.get("timezone"),
            enabled=bool(payload.get("enabled", True)),
            priority=str(payload.get("priority") or "normal"),
            max_runs=(int(payload["max_runs"]) if payload.get("max_runs") not in (None, "") else None),
            respect_quiet_hours=bool(payload.get("respect_quiet_hours", False)),
            quiet_hours=tuple(payload["quiet_hours"]) if payload.get("quiet_hours") else None,
        )
        return job
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/schedules/{schedule_id}/enable")
async def schedule_enable(schedule_id: str, payload: dict[str, Any] = None):
    from scheduler.cron import cron_manager
    enabled = bool((payload or {}).get("enabled", True))
    if not cron_manager.set_enabled(schedule_id, enabled):
        return JSONResponse({"error": "schedule not found"}, status_code=404)
    return {"schedule_id": schedule_id, "enabled": enabled}


@router.delete("/schedules/{schedule_id}")
async def schedule_delete(schedule_id: str):
    from scheduler.cron import cron_manager
    if not cron_manager.remove_job(schedule_id):
        return JSONResponse({"error": "schedule not found"}, status_code=404)
    return {"deleted": True, "schedule_id": schedule_id}



@router.get("/personal-context")
async def personal_context_get(query: str = "", project: str | None = None, limit: int = 5):
    from core.personal_context import personal_context
    return personal_context.snapshot(query=query, project=project, limit=max(1, min(20, int(limit)))).as_dict()


@router.post("/personal-context/preference")
async def personal_context_preference(payload: dict[str, Any] = None):
    payload = payload or {}
    key = str(payload.get("key") or "")
    if not key:
        return JSONResponse({"error": "key is required"}, status_code=400)
    from core.personal_context import personal_context
    return personal_context.remember_preference(
        key,
        payload.get("value"),
        project=payload.get("project"),
        session_id=payload.get("session_id"),
    )


@router.post("/personal-context/goal")
async def personal_context_goal(payload: dict[str, Any] = None):
    payload = payload or {}
    title = str(payload.get("title") or "")
    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)
    from core.personal_context import personal_context
    return personal_context.add_goal(
        title,
        priority=str(payload.get("priority") or "normal"),
        status=str(payload.get("status") or "active"),
        project=payload.get("project"),
        deadline=payload.get("deadline"),
    )


@router.post("/personal-context/focus")
async def personal_context_focus(payload: dict[str, Any] = None):
    payload = payload or {}
    from core.personal_context import personal_context
    return personal_context.set_focus(str(payload.get("focus") or ""), project=payload.get("project"))


@router.post("/personal-context/project")
async def personal_context_project(payload: dict[str, Any] = None):
    payload = payload or {}
    from core.personal_context import personal_context
    name = str(payload.get("name") or "")
    details = {k: v for k, v in payload.items() if k != "name"}
    return personal_context.upsert_project(name, **details)


@router.get("/runtime/issues")
async def runtime_issues(limit: int = 100):
    """Recent structured runtime issues (component/operation/error/context).

    Replaces silent ``except: pass`` blindness: every non-fatal failure in the
    agent loop, mission engine, memory, routing and telemetry lands here with
    enough context to diagnose what an autonomous run actually did.
    """
    from core.run_events import recent_issues

    issues = recent_issues(limit=limit)
    return {"count": len(issues), "issues": issues}


# ---- rollback & checkpoints ---------------------------------------------------
@router.get("/rollback/checkpoints")
async def rollback_list_api():
    from core.rollback import rollback_manager

    cps = await asyncio.to_thread(rollback_manager.list_checkpoints)
    return {"count": len(cps), "checkpoints": [c.to_dict() for c in cps]}


@router.post("/rollback/checkpoint")
async def rollback_create_api(payload: dict[str, Any] = None):
    payload = payload or {}
    label = str(payload.get("label") or "manual_checkpoint")
    from core.rollback import rollback_manager

    cp = await asyncio.to_thread(rollback_manager.checkpoint, label=label, metadata=payload.get("metadata"))
    return {"success": True, "checkpoint": cp.to_dict()}


@router.post("/rollback/restore")
async def rollback_restore_api(payload: dict[str, Any] = None):
    payload = payload or {}
    cid = str(payload.get("checkpoint_id") or "")
    if not cid:
        return JSONResponse({"error": "checkpoint_id is required"}, status_code=400)
    from core.rollback import rollback_manager

    res = await asyncio.to_thread(rollback_manager.restore, checkpoint_id=cid)
    return res


# ------------------------------------------------------------------ lifespan glue
async def startup(app=None, *, agent_getter: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Start the queue workers + register handlers + schedule maintenance."""
    global _agent_getter
    if agent_getter is not None:
        _agent_getter = agent_getter
    from gateway.queue import job_queue

    getter = _agent_getter or (lambda *a, **k: None)
    try:
        from gateway.handlers import register_handlers

        register_handlers(job_queue, getter)
    except Exception as e:
        logger.error(f"[Realtime] handler registration failed: {e}")
    info = {}
    try:
        info = await job_queue.start()
    except Exception as e:
        logger.error(f"[Realtime] queue start failed ({e}) — /command stays synchronous")
    info["kinds"] = sorted(job_queue.handlers)
    return info


async def shutdown() -> None:
    from gateway.queue import job_queue

    try:
        await job_queue.stop()
    except Exception as e:
        logger.error(f"[Realtime] queue stop failed: {e}")


def install(app, *, agent_getter: Callable[..., Any] | None = None) -> dict[str, str]:
    """Mount the realtime router on the app and wire queue ⇄ app lifecycle.

    The control-plane HTTP/SSE router is gated by the optional gateway token
    (``_check_gateway_auth``: no-op when HERMUS_GATEWAY_TOKEN is unset, else every
    job/command/delegate/memory/skill/sandbox/mission/swe/rollback endpoint
    requires it). The single WebSocket is mounted separately on ``ws_router`` so
    it keeps its own 1008-close token check rather than a router dependency that
    would close it with the wrong code.
    """
    from .context import _check_gateway_auth

    global _agent_getter
    if agent_getter is not None:
        _agent_getter = agent_getter
    app.include_router(router, dependencies=[Depends(_check_gateway_auth)])
    app.include_router(ws_router)
    return {"mounted": True}


# ---------------------------------------------------------- production layers
@router.get("/integrations")
async def integrations_status():
    from core.integrations import external_integrations
    return external_integrations.status()

@router.post("/integrations/{integration_id}/configure")
async def integration_configure(integration_id: str, payload: dict[str, Any] | None = None):
    from core.integrations import external_integrations
    payload=payload or {}
    try:
        return external_integrations.configure(integration_id, enabled=bool(payload.get("enabled", True)), metadata=dict(payload.get("metadata") or {}))
    except KeyError:
        return JSONResponse({"error":"integration_not_found"}, status_code=404)

@router.post("/integrations/{integration_id}/enable")
async def integration_enable(integration_id: str, payload: dict[str, Any] | None = None):
    from core.integrations import external_integrations
    try:
        return external_integrations.set_enabled(integration_id, bool((payload or {}).get("enabled", True)))
    except KeyError:
        return JSONResponse({"error":"integration_not_found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error":str(exc)}, status_code=409)

@router.get("/personal-profile")
async def personal_profile_get():
    from core.personal_profile import personal_profile
    return personal_profile.snapshot()

@router.patch("/personal-profile")
async def personal_profile_update(payload: dict[str, Any] | None = None):
    from core.personal_profile import personal_profile
    return personal_profile.update(**(payload or {}))

@router.post("/personal-profile/routines")
async def personal_profile_routine(payload: dict[str, Any] | None = None):
    from core.personal_profile import personal_profile
    if not isinstance(payload, dict) or not payload:
        return JSONResponse({"error":"routine payload required"}, status_code=400)
    return personal_profile.add_routine(payload)

@router.get("/voice/streams")
async def voice_streams():
    from core.voice_stream import voice_streams as manager
    return manager.snapshot()

@router.post("/voice/streams")
async def voice_stream_start(payload: dict[str, Any] | None = None):
    from core.voice_stream import voice_streams as manager
    payload=payload or {}; session_id=str(payload.get("session_id") or "")
    if not session_id: return JSONResponse({"error":"session_id required"}, status_code=400)
    metadata=dict(payload.get("metadata") or {}); return manager.start(session_id, **metadata)

@router.post("/voice/streams/{stream_id}/chunk")
async def voice_stream_chunk(stream_id: str, payload: dict[str, Any] | None = None):
    from core.voice_stream import voice_streams as manager
    try: return manager.push(stream_id, int((payload or {}).get("bytes") or 0))
    except KeyError: return JSONResponse({"error":"stream_not_found"}, status_code=404)
    except ValueError as exc: return JSONResponse({"error":str(exc)}, status_code=409)

@router.post("/voice/streams/{stream_id}/interrupt")
async def voice_stream_interrupt(stream_id: str):
    from core.voice_stream import voice_streams as manager
    try: return manager.interrupt(stream_id)
    except KeyError: return JSONResponse({"error":"stream_not_found"}, status_code=404)

@router.post("/distributed/envelope")
async def distributed_envelope(payload: dict[str, Any] | None = None):
    import os
    from core.distributed_transport import EnvelopeSigner, create_envelope
    payload=payload or {}; secret=os.getenv("HERMUS_DISTRIBUTED_SECRET", "")
    if not secret: return JSONResponse({"error":"HERMUS_DISTRIBUTED_SECRET_not_configured"}, status_code=503)
    required=("source_node","target_node","job_id","kind")
    if any(not str(payload.get(k) or "").strip() for k in required): return JSONResponse({"error":"source_node,target_node,job_id,kind required"}, status_code=400)
    env=create_envelope(str(payload["source_node"]),str(payload["target_node"]),str(payload["job_id"]),str(payload["kind"]),dict(payload.get("payload") or {}),ttl_s=float(payload.get("ttl_s",300)))
    return {"envelope":EnvelopeSigner(secret).sign(env).__dict__}

@router.post("/distributed/envelope/verify")
async def distributed_envelope_verify(payload: dict[str, Any] | None = None):
    import os
    from core.distributed_transport import EnvelopeSigner, JobEnvelope
    secret=os.getenv("HERMUS_DISTRIBUTED_SECRET", "")
    if not secret: return JSONResponse({"error":"HERMUS_DISTRIBUTED_SECRET_not_configured"}, status_code=503)
    try: env=JobEnvelope(**dict((payload or {}).get("envelope") or {})); return {"valid":EnvelopeSigner(secret).verify(env)}
    except (TypeError, KeyError) as exc: return JSONResponse({"error":str(exc)}, status_code=400)
