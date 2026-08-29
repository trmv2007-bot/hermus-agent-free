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
from datetime import datetime
from typing import Any, Optional
from collections.abc import Callable

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse

from core.config import config
from core.run_events import RunBus, run_bus, sse_format

router = APIRouter()

_agent_getter: Optional[Callable[..., Any]] = None


def _auth_ok(token: Optional[str], header_token: Optional[str]) -> bool:
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
    request: Optional[Request] = None,
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
                yield sse_format({"id": 0, "run_id": run_id, "type": "stream_timeout",
                                  "ts": _ts(), "data": {"max_seconds": max_seconds}})
                break
            try:
                event = await asyncio.wait_for(aq.get(), timeout=keepalive)
            except asyncio.TimeoutError:
                yield f": ping {_ts()}\n\n"
                continue
            etype = event.get("type")
            if etype == "__closed__":
                yield sse_format({"id": int(event.get("id") or 0), "run_id": run_id,
                                  "type": "stream_end", "ts": _ts(),
                                  "data": {"status": event.get("status")}})
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
    session_key = str(payload.get("session_key") or
                      f"{body.get('platform', 'api')}:{body.get('user_id', 'anonymous')}")
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
    return {
        "job_id": job.id,
        "run_id": job.run_id,
        "status": job.status,
        "kind": job.kind,
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
            return JSONResponse({"error": st.get("error") or "unknown job",
                                 "status": st.get("status")}, status_code=404)
        return JSONResponse({"error": "result not ready", "status": st.get("status")},
                            status_code=409)
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
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no", "X-Hermus-Run": str(run_id)},
    )


@router.get("/stream/run/{run_id}")
async def stream_run(run_id: str, request: Request, after: int = 0):
    """SSE for any run id (queue jobs, direct /command runs, delegations)."""
    return StreamingResponse(
        _stream_run(run_bus, run_id, after=after, request=request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no", "X-Hermus-Run": run_id},
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
    return StreamingResponse(
        _stream_run(run_bus, job.run_id, request=request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no", "X-Hermus-Job": job.id, "X-Hermus-Run": job.run_id},
    )


@router.get("/queue/status")
async def queue_status():
    from gateway.queue import job_queue

    return {"queue": job_queue.status(), "runs": run_bus.runs()[-20:]}


# ---------------------------------------------------------------- bi-directional WS
@router.websocket("/ws/agent")
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

    await send({
        "type": "hello",
        "protocol": "hermus.agent.v1",
        "actions": ["chat", "autonomous", "cancel", "subscribe", "tool", "ping"],
        "kinds": sorted(job_queue.handlers),
        "queue": {"workers": job_queue.workers, "enabled": job_queue.enabled},
        "sandbox": _safe(lambda: __import__("core.sandbox", fromlist=["sandbox"]).sandbox.status()["backend"]),
        "memory_index": _safe(lambda: __import__("core.memory2", fromlist=["memory2"]).memory2.store.index_stats()),
        "ts": _ts(),
    })

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
                kind = {"chat": "agent.chat", "autonomous": "agent.autonomous",
                        "delegate": "subagent.delegate"}[action]
                body = {k: v for k, v in msg.items() if k not in ("action", "type")}
                if action == "delegate" and not body.get("goal") and body.get("text"):
                    body["goal"] = body["text"]
                try:
                    job = job_queue.submit(
                        kind, body,
                        session_key=f"ws:{body.get('user_id') or msg.get('user_id') or 'guest'}",
                        timeout=body.get("timeout"),
                    )
                except Exception as e:
                    await send({"type": "error", "error": str(e)[:300], "action": action})
                    continue
                await send({"type": "ack", "job_id": job.id, "run_id": job.run_id, "kind": kind})
                streams[job.run_id] = asyncio.create_task(pump_events(job.run_id, source=job.id))
                continue

            if action == "subscribe":
                run_id = str(msg.get("run_id") or "")
                if not run_id:
                    await send({"type": "error", "error": "run_id required"})
                    continue
                await send({"type": "subscribe_ok", "run_id": run_id,
                             "replayed": len(run_bus.history(run_id, after=int(msg.get("after") or 0)))})
                streams[run_id] = asyncio.create_task(pump_events(run_id, source="subscribe"))
                continue

            if action == "cancel":
                from gateway.queue import job_queue as jq

                job_id = str(msg.get("job_id") or "")
                res = jq.cancel(job_id) if job_id else jq.cancel(str(msg.get("run_id") or ""))
                await send({"type": "cancel_result", "result": res})
                continue

            if action == "tool":
                name = str(msg.get("name") or msg.get("tool") or "")
                args = msg.get("args") or msg.get("arguments") or {}
                try:
                    from core.tool_registry import tool_registry

                    result = await asyncio.to_thread(tool_registry.execute, name,
                                                     args if isinstance(args, dict) else {})
                    await send({"type": "tool_result", "tool": name, "result": result})
                except Exception as e:
                    await send({"type": "error", "error": str(e)[:300], "tool": name})
                continue

            if action == "memory":
                try:
                    from core.memory2 import memory2

                    out = await asyncio.to_thread(
                        memory2.hybrid_recall,
                        str(msg.get("query") or ""),
                        limit=int(msg.get("limit") or 6),
                    )
                    await send({"type": "memory_result", "query": msg.get("query"),
                                "hits": [{"id": h.get("id"), "kind": h.get("kind"), "score": h.get("score"),
                                          "rrf": h.get("rrf_score"), "text": (h.get("content") or "")[:300]}
                                         for h in out]})
                except Exception as e:
                    await send({"type": "error", "error": str(e)[:300]})
                continue

            if action in ("ping", ""):
                await send({"type": "pong", "ts": _ts(),
                            "queue": _safe(lambda: dict(job_queue.status()["by_status"]))})
                continue

            await send({"type": "error", "error": f"unknown action '{action}'",
                        "actions": ["chat", "autonomous", "delegate", "subscribe", "cancel", "tool", "memory", "ping"]})
    except Exception:
        pass
    finally:
        for task in streams.values():
            task.cancel()


def _hello(job_queue) -> dict[str, Any]:
    """Capabilities frame sent right after a WS handshake."""
    from core.sandbox import sandbox as jail
    from core.memory2 import memory2

    return {
        "type": "hello",
        "protocol": "hermus.agent.v1",
        "actions": ["chat", "autonomous", "delegate", "subscribe", "cancel", "tool", "memory", "ping"],
        "kinds": sorted(job_queue.handlers),
        "queue": {"workers": job_queue.workers, "enabled": job_queue.enabled,
                  "backend": job_queue.backend},
        "sandbox": _safe(lambda: jail.status()["backend"]),
        "memory_index": _safe(lambda: memory2.store.index_stats()),
        "ts": _ts(),
    }


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
    from core.memory2 import memory2

    query = str(payload.get("query") or "")
    if not query.strip():
        return JSONResponse({"error": "query required"}, status_code=400)
    limit = int(payload.get("limit") or 8)
    project = payload.get("project") or None
    kinds = payload.get("kinds") or None
    if payload.get("explain"):
        return await asyncio.to_thread(memory2.explain, query, limit, project=project, kinds=kinds)
    hits = await asyncio.to_thread(memory2.hybrid_recall, query, project=project, kinds=kinds, limit=limit)
    return {
        "query": query, "mode": "hybrid", "count": len(hits),
        "index": memory2.store.index_stats(),
        "results": [
            {"id": h.get("id"), "kind": h.get("kind"), "score": h.get("score"),
             "rrf_score": h.get("rrf_score"), "decay": h.get("decay"),
             "retrieval": h.get("retrieval"), "signals": h.get("signals"),
             "content": (h.get("content") or "")[:700]}
            for h in hits
        ],
    }


@router.post("/memory/remember")
async def memory_remember(payload: dict[str, Any] = None):
    from core.memory2 import memory2

    payload = payload or {}
    res = await asyncio.to_thread(
        memory2.remember, str(payload.get("kind") or "semantic"), str(payload.get("content") or ""),
        project=payload.get("project"), importance=float(payload.get("importance", 5.0)),
        success=payload.get("success"), ttl_hours=payload.get("ttl_hours"),
        pinned=bool(payload.get("pinned")), metadata=payload.get("metadata") or None,
    )
    return res


@router.post("/memory/sweep")
async def memory_sweep(payload: dict[str, Any] = None):
    payload = payload or {}
    if not payload.get("confirm"):
        return JSONResponse({"error": "sweep archives/purges memory; send confirm=true"}, status_code=400)
    from core.memory2 import memory2

    return await asyncio.to_thread(
        memory2.sweep, project=payload.get("project") or None,
        dry_run=bool(payload.get("dry_run", False)),
    )


@router.get("/memory/stats")
async def memory_stats():
    from core.memory2 import memory2

    return await asyncio.to_thread(memory2.stats)


@router.post("/memory/reindex")
async def memory_reindex(payload: dict[str, Any] = None):
    """Rebuild the FTS + vector indexes (after switching embedding model)."""
    from core.memory2 import memory2

    return await asyncio.to_thread(memory2.reindex)


@router.get("/memory/access-log")
async def memory_access_log(memory_id: int, limit: int = 20):
    from core.memory2 import memory2

    return {"memory_id": memory_id, "access": memory2.store.access_log(memory_id, limit=limit)}


# ---- skill forge ---------------------------------------------------------------
@router.post("/skills/forge/harvest")
async def skill_forge_harvest(payload: dict[str, Any] = None):
    """Harvest a trajectory into a validated skill. Provide goal + trajectory, or a session id."""
    payload = payload or {}
    from core.skill_forge import skill_forge

    traj = payload.get("trajectory")
    if not traj:
        return JSONResponse({"error": "trajectory required (list of turns with tool_calls)"},
                            status_code=400)
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
    return {"stats": skill_forge.stats(),
            "skills": {k: v for k, v in list(reg["skills"].items())[-40:]}}


@router.post("/skills/forge/run")
async def skill_forge_run(payload: dict[str, Any] = None):
    payload = payload or {}
    name = str(payload.get("name") or "")
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    from core.skill_forge import skill_forge

    return await asyncio.to_thread(
        skill_forge.run, name,
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
        sandbox.run, command,
        timeout=int(payload.get("timeout") or 0) or None,
        cwd=payload.get("cwd"), network=payload.get("network"),
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
    """Fan out work to parallel sub-agents (processes + JSON-RPC) and aggregate."""
    payload = payload or {}
    from core.delegation import delegation

    tasks = payload.get("tasks")
    goal = str(payload.get("goal") or payload.get("text") or "")
    if payload.get("async"):
        from gateway.queue import job_queue

        job = job_queue.submit("subagent.delegate", payload, session_key=f"delegate:{goal[:40]}")
        return {"job_id": job.id, "run_id": job.run_id, "status": job.status,
                "events_url": f"/jobs/{job.id}/events"}
    if tasks:
        return await asyncio.to_thread(
            delegation.fanout, [str(t) for t in tasks], goal=goal,
            aggregate=str(payload.get("aggregate") or "synthesize"),
            max_children=int(payload.get("max_children") or delegation.max_workers),
            timeout=payload.get("timeout"),
        )
    if not goal:
        return JSONResponse({"error": "goal or tasks required"}, status_code=400)
    return await asyncio.to_thread(
        delegation.decompose_and_run, goal,
        max_children=int(payload.get("max_children") or 4),
        aggregate=str(payload.get("aggregate") or "synthesize"),
        timeout=payload.get("timeout"),
    )


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
        budget_steps=(int(payload["budget_steps"]) if payload.get("budget_steps")
                      not in (None, "") else None),
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

@router.post("/missions/{mission_id}/resume")
async def mission_resume_api(
    mission_id: str,
    restart_failed: bool = False,
    extra_steps: Optional[int] = None,
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
            mission_engine.resume_mission, mission_id,
            restart_failed=restart,
            extra_steps=int(steps) if steps not in (None, "") else None,
        )
        return report.to_dict()
    except ValueError as e:
        # terminal / unrecoverable: say why, and how to recover
        from core.mission import mission_engine as _me

        current = await asyncio.to_thread(_me.get_mission, mission_id)
        body: dict[str, Any] = {"error": str(e), "mission_id": mission_id,
                                "restart_failed": restart}
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
            mission_engine.extend_budget, mission_id, n,
            emergency=bool(emergency or payload.get("emergency")),
        )
        return report.to_dict()
    except ValueError as e:
        return JSONResponse({"error": str(e), "mission_id": mission_id}, status_code=409)


@router.get("/models/capabilities")
async def models_capabilities_api(model: Optional[str] = None, needs_vision: bool = False,
                                  needs_computer: bool = False):
    """Pre-flight capability negotiation for the current (or a given) model.

    Answers, before a run starts: tools? vision? long context? structured
    outputs? streaming? computer control? — and recommends a compatible model
    when the selected one cannot do the job.
    """
    from core.config import config
    from core.model_capabilities import mission_capability_gate

    ref = model or str(getattr(config, "model", "") or "")
    return await asyncio.to_thread(
        mission_capability_gate, ref,
        needs_vision=needs_vision, needs_computer=needs_computer,
    )

# ---- artifacts ----------------------------------------------------------------
@router.get("/artifacts")
async def artifacts_list_api(mission_id: Optional[str] = None, artifact_type: Optional[str] = None):
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
                payload.get("platform", "api"), payload.get("user_id", "swe"),
                model=payload.get("model"), mode="agent",
                api_key=payload.get("api_key"), base_url=payload.get("base_url"),
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
async def startup(app=None, *, agent_getter: Optional[Callable[..., Any]] = None) -> dict[str, Any]:
    """Start the queue workers + register handlers + schedule maintenance."""
    global _agent_getter
    if agent_getter is not None:
        _agent_getter = agent_getter
    from gateway.queue import job_queue

    getter = _agent_getter or (lambda *a, **k: None)
    try:
        from gateway.handlers import register_handlers

        kinds = register_handlers(job_queue, getter)
    except Exception as e:
        print(f"[Realtime] handler registration failed: {e}")
        kinds = {}
    info = {}
    try:
        info = await job_queue.start()
    except Exception as e:
        print(f"[Realtime] queue start failed ({e}) — /command stays synchronous")
    info["kinds"] = sorted(job_queue.handlers)
    return info


async def shutdown() -> None:
    from gateway.queue import job_queue

    try:
        await job_queue.stop()
    except Exception as e:
        print(f"[Realtime] queue stop failed: {e}")


def install(app, *, agent_getter: Optional[Callable[..., Any]] = None) -> dict[str, str]:
    """Mount the realtime router on the app and wire queue ⇄ app lifecycle."""
    global _agent_getter
    if agent_getter is not None:
        _agent_getter = agent_getter
    app.include_router(router)
    return {"mounted": True}
