"""Gateway - Single process for Telegram, Discord, Slack, WhatsApp, Signal, CLI - free, cross-platform continuity - Optimized with caching, compression, rate limiting

Composition root: builds the FastAPI app, wires the lifespan (channels, job
queue, watchdog, memory sweeps) and mounts the per-concern routers:

    gateway/context.py           shared agent registry, auth, chat adapter
    gateway/routes_channels.py   Telegram webhook + channel status/start/send
    gateway/routes_registry.py   tools, public-APIs, MCP, embeddings, fleet, keys (read)
    gateway/routes_management.py keys CRUD, custom APIs, updater, plugins
    gateway/routes_subsystems.py background agents, workspace, memory2, permissions, screen
    gateway/routes_computer.py   computer-agent dashboard API + remote control
    gateway/routes_speech.py     Talking Mode (TTS/transcribe) + dashboard events WS
    gateway/realtime.py          async job queue + SSE/WebSocket streaming

Endpoint code moved verbatim; `from gateway.gateway import app|setup|start|
get_agent_for_user` and `gateway.gateway.<attr>` monkeypatching keep working
(the shared state is re-exported from gateway.context).
"""
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

# Direct execution (python gateway/gateway.py): put the repo root on sys.path
# and drop the gateway/ directory itself, which otherwise shadows the stdlib
# `queue` module (breaking logging/uvicorn/threading imports) and invites
# ambiguous top-level imports like `import channels`.
if __package__ in (None, ""):
    _repo_root = str(Path(__file__).resolve().parent.parent)
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    _here = str(Path(__file__).resolve().parent)
    while _here in sys.path:
        sys.path.remove(_here)

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from core.config import config
from core.task_tracker import task_tracker

from gateway.channels import get_channel_status, set_agent_factory, start_all_channels

from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware

# Realtime layer: async job queue + SSE/WebSocket streaming (see gateway/realtime.py)
try:
    from gateway import realtime as _realtime
    from gateway.queue import job_queue as _job_queue
    from core.run_events import run_bus as _run_bus
except ImportError:  # executed as a plain module (python gateway/gateway.py)
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gateway import realtime as _realtime  # type: ignore
    from gateway.queue import job_queue as _job_queue  # type: ignore
    from core.run_events import run_bus as _run_bus  # type: ignore

# Store agents per user/platform for continuity

# Shared runtime state (agent registry, auth, chat adapter) lives in
# gateway.context; re-exported here so `from gateway.gateway import ...`
# and `monkeypatch.setattr("gateway.gateway.get_agent_for_user", ...)`
# keep working exactly as before the split.
from gateway.context import (  # noqa: F401
    AGENTS,
    _agent_chat,
    _check_gateway_auth,
    _token_matches,
    get_agent_for_user,
)
from gateway.context import _agent_factory  # noqa: F401

async def _background_agent_watchdog():
    """Periodically revive stale/dead persistent background agents."""
    from core.agent_manager import agent_manager

    while True:
        try:
            tick = agent_manager.watchdog_tick(restart=True)
            if tick.get("revived") or tick.get("errors"):
                print(f"[Gateway] background agents tick: {tick}")
        except Exception as e:
            print(f"[Gateway] background agents tick error: {e}")
        await asyncio.sleep(30)


async def _local_engine_watchdog():
    """Keep the routed local engine alive, and let the doctor self-triage.

    Two independent jobs on one slow tick:

    * If the accelerator plan routes work to NoLlama and the server is
      installed with a model on disk but not answering, restart it.  A dead
      engine is otherwise silent until a user notices missing answers.
    * If ``HERMUS_DOCTOR_AUTO=1``, run the doctor's bounded auto-triage
      (cooldown + daily cap live in ``core.doctor``) so failures are explained
      while the evidence is still fresh.
    """
    from core.accelerators import ENGINE_NOLLAMA, cached_plan
    from core.nollama import nollama_manager

    while True:
        try:
            plan = cached_plan()
            wants_nollama = any(
                role.get("engine") == ENGINE_NOLLAMA for role in (plan.get("roles") or {}).values()
            )
            if wants_nollama and nollama_manager.installed() and nollama_manager.installed_models():
                if not nollama_manager.running():
                    device = ""
                    for role in (plan.get("roles") or {}).values():
                        if role.get("engine") == ENGINE_NOLLAMA:
                            device = role.get("device") or ""
                            break
                    started = await asyncio.to_thread(nollama_manager.start, device=device)
                    print(f"[Gateway] local engine auto-start: {started.get('success')} (device={device or 'AUTO'})")
        except Exception as e:
            print(f"[Gateway] local engine watchdog error: {e}")

        if getattr(config, "doctor_enabled", True) and getattr(config, "doctor_auto", False):
            try:
                from core.doctor import doctor

                report = await asyncio.to_thread(doctor.run, auto=True)
                if report.get("status") not in (None, "skipped", "ok"):
                    print(
                        f"[Gateway] hermus-doctor: {report.get('status')} "
                        f"({report.get('finding_count', len(report.get('findings') or []))} findings) "
                        f"-> {report.get('path', 'no report path')}"
                    )
            except Exception as e:
                print(f"[Gateway] hermus-doctor error: {e}")

        await asyncio.sleep(120)


async def _memory_maintenance_loop():
    """Hourly decay/eviction pass so stale memories stop polluting prompts.

    Archive/purge is conservative by default (see core.decay thresholds) and pinned
    or high-importance memories are protected. Failures never take the gateway down.
    """
    minutes = max(1, int(getattr(config, "memory_sweep_minutes", 60)))
    while True:
        if not (_job_queue.enabled and _job_queue._started):
            await asyncio.sleep(60)
            continue
        try:
            await asyncio.sleep(minutes * 60)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        try:
            report = await asyncio.to_thread(_job_queue.submit, "memory.sweep", {})
            if report is not None:
                print(f"[Gateway] memory sweep queued as {getattr(report, 'id', '?')}")
        except Exception as e:
            print(f"[Gateway] memory sweep failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern lifespan handler replacing deprecated on_event."""
    set_agent_factory(_agent_factory)
    if getattr(config, "auto_start_channels", True):
        mode = getattr(config, "telegram_mode", "auto")
        started = start_all_channels(_agent_factory, telegram_mode=mode)
        print(f"[Gateway] Channels started: {started}")
    # Async worker model: intake never blocks on a tool loop.
    queue_info = {}
    try:
        queue_info = await _realtime.startup(app, agent_getter=get_agent_for_user)
    except Exception as e:
        print(f"[Gateway] realtime layer unavailable ({e}) — /command runs inline")
    maintenance_task = None
    if getattr(config, "memory_sweep_minutes", 60) > 0:
        try:
            maintenance_task = asyncio.create_task(_memory_maintenance_loop())
        except Exception as e:
            print(f"[Gateway] memory maintenance failed to start: {e}")
    watchdog_task = None
    if getattr(config, "background_agents_enabled", True):
        try:
            watchdog_task = asyncio.create_task(_background_agent_watchdog())
        except Exception as e:
            print(f"[Gateway] background-agent watchdog failed to start: {e}")
    engine_task = None
    if getattr(config, "nollama_autostart", False) or getattr(config, "doctor_auto", False):
        try:
            engine_task = asyncio.create_task(_local_engine_watchdog())
        except Exception as e:
            print(f"[Gateway] local-engine watchdog failed to start: {e}")
    try:
        yield
    finally:
        if maintenance_task and not maintenance_task.done():
            maintenance_task.cancel()
        if watchdog_task and not watchdog_task.done():
            watchdog_task.cancel()
        if engine_task and not engine_task.done():
            engine_task.cancel()
        try:
            await _realtime.shutdown()
        except Exception:
            pass
        # Release every SQLite handle Hermus still owns (memory, memory2,
        # hybrid index, web-read cache, engine state). Without this, Ctrl+C
        # ended with a screenful of "ResourceWarning: unclosed database".
        try:
            from core.db_registry import close_all as _close_dbs

            report = _close_dbs("gateway_shutdown")
            if report.get("closed") or report.get("errors"):
                print(
                    f"[Gateway] sqlite handles closed: {report.get('closed', 0)}"
                    + (f" ({len(report['errors'])} errors)" if report.get("errors") else "")
                )
        except Exception as e:
            print(f"[Gateway] sqlite shutdown cleanup failed: {e}")
        # Stop the local NoLlama engine we may have started, so the NPU/GPU
        # side is never left running without its parent gateway.
        try:
            from core.nollama import nollama_manager

            stopped = nollama_manager.stop_if_managed()
            if stopped.get("stopped"):
                print(f"[Gateway] local engine stopped: {stopped.get('pid')}")
        except Exception as e:
            print(f"[Gateway] local engine shutdown failed: {e}")


app = FastAPI(title="Hermus Gateway Free", description="Single gateway for all platforms, free - Optimized", lifespan=lifespan)

# --- CORS: secure-by-default, configurable -------------------------------------
# The control room is served same-origin (relative URLs), so browser CORS is not
# required for the UI. Wildcard + credentials is an insecure combination (the
# spec §32 forbids leaving it on without a documented, authenticated model), so we
# default to a restricted allow-list and credentials OFF. Operators may opt into a
# broader set via HERMUS_CORS_ORIGINS (comma-separated); explicit "*" is allowed
# but forces credentials OFF.
def _cors_origins() -> list[str]:
    raw = os.environ.get("HERMUS_CORS_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:3000", "http://127.0.0.1:3000",
    ]


def _cors_credentials() -> bool:
    # Credentials are only safe with an explicit (non-wildcard) origin allow-list.
    raw = os.environ.get("HERMUS_CORS_ORIGINS", "").strip()
    if "*" in [o.strip() for o in raw.split(",") if o.strip()]:
        return False
    return os.environ.get("HERMUS_CORS_CREDENTIALS", "0") not in ("0", "false", "False")


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=_cors_credentials(),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add GZip compression for faster dashboard - optimized
app.add_middleware(GZipMiddleware, minimum_size=500)

# The single production control room is served from /control (see control_room
# below); it is a self-contained snapshot + replay projection with no external
# assets. The legacy dashboard static-assets mount is removed.

# Job queue + SSE + WebSocket + memory/skill/sandbox/delegation endpoints.
# The agent getter is injected later, in the lifespan (get_agent_for_user is
# defined further down this module).
_realtime.install(app)

# Per-concern routers (extracted from this module; see gateway/routes_*.py).
# Mounted after the realtime layer so its routes keep precedence.
from gateway.routes_channels import router as _channels_router  # noqa: E402
from gateway.routes_registry import router as _registry_router  # noqa: E402
from gateway.routes_management import router as _management_router  # noqa: E402
from gateway.routes_subsystems import router as _subsystems_router  # noqa: E402
from gateway.routes_computer import router as _computer_router  # noqa: E402
from gateway.routes_speech import router as _speech_router  # noqa: E402
from gateway.routes_jarvis import router as _jarvis_router  # noqa: E402
from gateway.routes_engine import router as _engine_router  # noqa: E402
from gateway.routes_canonical import router as _canonical_router  # noqa: E402
from gateway.routes_android import router as _android_router  # noqa: E402

app.include_router(_channels_router)
app.include_router(_registry_router)
app.include_router(_management_router)
app.include_router(_subsystems_router)
app.include_router(_computer_router)
app.include_router(_speech_router)
app.include_router(_jarvis_router)
app.include_router(_engine_router)
app.include_router(_canonical_router)
app.include_router(_android_router)


@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    """Open the single canonical control room (spec §21 / Final One-Shot §7).

    Explicitly allows HEAD so health checks / proxies that probe ``/`` with HEAD
    no longer get a 405. Root opens /control; no legacy dashboard surface.
    """
    return RedirectResponse(url="/control", status_code=307)


@app.get("/favicon.ico")
async def favicon():
    """Serve a tiny inline Hermus (caduceus-style) favicon so browsers stop
    logging a 404 on every dashboard load."""
    favicon_svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
        "<rect width='32' height='32' rx='7' fill='#02050e'/>"
        "<text x='16' y='23' font-size='20' text-anchor='middle' fill='#00f2fe'>&#9764;</text>"
        "</svg>"
    )
    return Response(
        content=favicon_svg.encode("utf-8"),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/status")
async def api_status():
    """Machine-readable gateway status previously served from the root path."""
    from core.cache import get_cache_stats
    # Local engine summary (NPU/GPU routing). Probe is off here: /api/status is
    # polled by the dashboard, and the reachable-check lives on /engine/status.
    try:
        from core.accelerators import cached_plan
        from core.nollama import nollama_manager

        plan = cached_plan()
        local_engine = {
            "mode": plan.get("mode"),
            "hardware": {
                "npu": [d["name"] for d in plan.get("hardware", {}).get("npu", [])],
                "gpus": [d["name"] for d in plan.get("hardware", {}).get("gpus", [])],
            },
            "roles": {
                role: {"engine": a.get("engine"), "device": a.get("device"), "model": a.get("model")}
                for role, a in (plan.get("roles") or {}).items()
            },
            "nollama": {
                "installed": nollama_manager.installed(),
                "running": nollama_manager.running(),
                "models": len(nollama_manager.installed_models()),
                "port": nollama_manager.port,
            },
        }
    except Exception as e:  # noqa: BLE001 - status must still answer
        local_engine = {"error": str(e)}
    return {
        "message": "Hermus Gateway Free - Single process for Telegram/Discord/Slack/CLI - Optimized",
        "platforms": ["telegram", "discord", "cli"],
        "agents": len(AGENTS),
        "channels": get_channel_status(),
        "optimized": True,
        "cache_stats": get_cache_stats(),
        "local_engine": local_engine,
        "features": [
            "multi_step_agent_loop",
            "tool_registry",
            "telegram_send_and_poll",
            "discord_bot",
            "mcp_client",
            "semantic_embeddings",
            "public_api_discovery",
            "memory2_typed_scored_memory",
            "model_router2",
            "autonomous_verify_repair_loop",
            "persistent_background_agents",
            "permission_manager",
            "research_pipeline",
            "computer_control_screen",
            "computer_agent_dashboard",
            "remote_control_approval",
            "plugin_ecosystem",
            "resource_monitoring",
            "skill_optimization",
            "multi_agent_delegation",
            "self_healing_watchdog",
            "workspace_projects",
            "profiles_personas",
            "local_engine_routing_npu_gpu",
            "nollama_openvino_engine",
            "hermus_doctor_self_repair",
            "sqlite_lifecycle_registry",
        ],
        "version": "2.2-free-architecture"
    }

@app.get("/control", response_class=HTMLResponse)
@app.get("/controlroom", response_class=HTMLResponse)
async def control_room():
    """Canonical single control room (Rebuild spec §21).

    Pure snapshot + replay projection: state is either a live probe
    (``/api/v1/system/*``, ``/jobs``, ``/queue/status``) or reconstructable from
    the durable canonical event log (``/api/v1/runs/{id}/timeline``). It never
    owns truth. This is the single UI surface the spec calls for.
    """
    html_path = Path(__file__).parent / "control.html"
    if not html_path.exists():
        return HTMLResponse("Control room not found", status_code=404)
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/cache/stats")
async def cache_stats():
    """Get cache stats - for optimization dashboard"""
    from core.cache import get_cache_stats
    return get_cache_stats()

@app.post("/cache/clear")
async def cache_clear():
    """Clear all caches - for optimization"""
    from core.cache import clear_all_caches
    return clear_all_caches()

@app.get("/agents/status")
async def agents_status():
    """Slide panel data - what agents/models are running or doing the task - free"""
    return task_tracker.get_status()

@app.post("/command")
async def command_endpoint(request: Request):
    """Run an agent command, optionally producing local Talking Mode audio.

    Accepts either a JSON body (CLI/channels) or ``multipart/form-data`` (the
    dashboard drag-and-drop chat). Attachments are processed by
    ``core.document_ingest``: text files are inlined, and binary documents
    (DOCX/XLSX/PPTX/PDF/ZIP/…) get their real contents extracted when possible
    (plus the raw bytes persisted to the workspace uploads dir so vision/OCR
    tools can open them) — previously binaries were reduced to name+size.

    ``async``/``async_mode`` (sent by the dashboard) enqueues a
    ``runtime.turn`` job instead of running inline, so the turn survives the
    dashboard being closed and is executed queue-first exactly like every
    other surface. ``talking``/``speak`` is additive: every existing
    CLI/channel caller keeps the original JSON contract.
    """
    from core.dashboard_events import dashboard_event_bus
    from core.document_ingest import attachment_prompt_block, extract_document

    payload: dict = {}
    attachments: list[dict] = []
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        # Scalar fields (text, mode, model, ...) arrive as form strings.
        for key in ("platform", "user_id", "text", "model", "mode", "api_key",
                    "base_url", "provider", "key_name", "profile", "run_id",
                    "autonomous", "async", "async_mode", "talking", "speak",
                    "stream", "voice", "speech_rate", "timeout", "prefer"):
            val = form.get(key)
            if val is not None and val != "":
                payload[key] = val
        uploads_dir = None
        try:
            from core.workspace import workspace

            uploads_dir = workspace.dirs["uploads"]
        except Exception:
            uploads_dir = None
        for upload in form.getlist("files"):
            if not getattr(upload, "filename", None):
                continue
            content = await upload.read()
            doc = extract_document(
                upload.filename,
                content,
                upload.content_type or "",
                save_binary_to=uploads_dir,
            )
            attachments.append(doc)
    else:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
    payload = payload or {}

    # Inject attachment contents into the prompt as an explicit context block.
    if attachments:
        blocks = [attachment_prompt_block(doc) for doc in attachments]
        if blocks:
            payload["text"] = f"{str(payload.get('text') or '').strip()}\n\nThe user attached {len(attachments)} file(s):\n" + "\n\n".join(blocks)
        payload["attachments"] = [doc.to_dict() for doc in attachments]

    platform = payload.get("platform", "cli")
    user_id = payload.get("user_id", "cli_user")
    text = str(payload.get("text", ""))
    if not text.strip():
        return JSONResponse({"error": "text required"}, status_code=400)
    model = payload.get("model")
    mode = payload.get("mode", "agent")
    api_key = payload.get("api_key")
    base_url = payload.get("base_url")
    provider = payload.get("provider")
    key_name = payload.get("key_name")
    autonomous = bool(payload.get("autonomous", False))
    prefer = str(payload.get("prefer") or ("mission" if autonomous else "auto")).lower()
    profile = payload.get("profile") or ""
    as_async = bool(payload.get("async", payload.get("async_mode", False)))
    talking = bool(payload.get("talking", payload.get("speak", False)))
    run_id = str(payload.get("run_id") or f"run_{os.urandom(5).hex()}")

    # Allow selecting a stored key by provider + key name (dashboard chat).
    # The key itself is looked up server-side — never sent from the browser.
    if key_name and provider:
        try:
            from core.multi_key import multi_key_manager

            entry = multi_key_manager.get_entry(provider, key_name)
            if entry:
                api_key = api_key or entry.get("key")
                base_url = base_url or entry.get("base_url")
                if not model and entry.get("default_model"):
                    model = f"{provider}/{entry['default_model']}"
        except Exception:
            pass

    # Async path: hand the turn to the worker pool and answer with a job handle.
    # This is the dashboard's default path (queue-first execution): the turn
    # keeps running server-side even if the browser is closed, and its events
    # stream over SSE via the run bus.
    if as_async and _job_queue.enabled and _job_queue._started:
        job = _job_queue.submit(
            "runtime.turn",
            {
                "text": text, "platform": platform, "user_id": user_id, "model": model,
                "mode": mode, "api_key": api_key, "base_url": base_url, "provider": provider,
                "key_name": key_name, "profile": profile, "talking": talking,
                "speech_rate": payload.get("speech_rate"), "voice": payload.get("voice"),
                "stream": bool(payload.get("stream", True)),
                "prefer": prefer,
                "max_repairs": payload.get("max_repairs"),
                "budget_steps": payload.get("budget_steps"),
            },
            session_key=f"{platform}:{user_id}",
            timeout=payload.get("timeout"),
            run_id=run_id,
        )
        return {
            "async": True, "job_id": job.id, "run_id": job.run_id, "status": job.status,
            "run_kind": "queued",
            "status_url": f"/jobs/{job.id}", "result_url": f"/jobs/{job.id}/result",
            "events_url": f"/jobs/{job.id}/events", "stream_url": f"/stream/run/{job.run_id}",
        }

    agent = get_agent_for_user(
        platform, user_id, model=model, mode=mode, api_key=api_key, base_url=base_url
    )
    if profile:
        agent.profile = profile

    # Mirror the inline run onto the run bus so /stream/run/{run_id} works even
    # when the caller did not use the queue.
    _run_bus.start(run_id, label=f"command:{platform}:{user_id}")

    def _bus_event(event_type: str, data: Optional[dict] = None) -> None:
        try:
            _run_bus.publish(run_id, event_type, dict(data or {}))
        except Exception:
            pass

    dashboard_event_bus.publish("session_started", {
        "run_id": run_id, "text": text[:1000], "mode": mode,
        "model": model or agent.model_name, "talking": talking,
        "platform": platform,
    })
    try:
        stream_tokens = bool(
            getattr(config, "gateway_stream_enabled", True)
            and getattr(config, "gateway_stream_tokens", True)
            and payload.get("stream", True)
        )

        def _run() -> dict:
            # Universal mission runtime: same core for inline /command turns as
            # for queued jobs, the CLI and channels — with live streaming,
            # cooperative cancellation and mid-run steering from the run bus.
            from core.runtime import execute as runtime_execute

            out = runtime_execute(
                text,
                agent=agent,
                prefer=prefer,
                on_event=_bus_event,
                stream=stream_tokens,
                should_cancel=lambda: _run_bus.is_cancelled(run_id),
                steer_source=lambda: _run_bus.pending_steers(run_id),
                max_repairs=int(payload.get("max_repairs") or 2),
            )
            if isinstance(out, dict):
                out.setdefault("steps", out.get("steps", 0))
                if autonomous:
                    out["autonomous"] = True
                if stream_tokens:
                    out["streamed"] = True
            return out

        result = await asyncio.to_thread(_run)
        # Normalize the answer contract: chat returns "response", autonomous
        # reports historically returned "final_answer" and missions use
        # "final_proof". Promote whichever exists to canonical "response" so the
        # dashboard, SSE events and TTS never see an empty answer.
        if isinstance(result, dict) and not result.get("response"):
            for alt in ("final_answer", "final_proof", "answer", "output"):
                if result.get(alt):
                    result["response"] = str(result[alt])
                    break
        if not autonomous:
            # Self-healing watchdog (architecture upgrade)
            from core.integrations import maybe_self_heal

            result = maybe_self_heal(result)
    except Exception as exc:
        dashboard_event_bus.publish("session_failed", {"run_id": run_id, "error": str(exc)})
        try:
            _run_bus.finish(run_id, "error", None, str(exc))
        except Exception:
            pass
        return JSONResponse({"error": str(exc), "run_id": run_id}, status_code=500)

    # Include mode in response
    result["mode"] = agent.mode.value
    result["mode_config"] = {"name": agent.mode_config.name, "description": agent.mode_config.description[:200]}
    result["model"] = agent.model_name
    result["run_id"] = run_id
    result["talking"] = talking
    if attachments:
        # attachment extraction report (inlined text? which method? saved path?)
        result["attachments"] = payload.get("attachments") or [doc.to_dict() for doc in attachments]
    try:
        bundle = agent.llm._resolve_bundle()
        result["resolved"] = {
            "provider": bundle.get("provider") or agent.llm.provider,
            "base_url": bundle.get("base_url") or None,
            "default_model": bundle.get("default_model"),
        }
    except Exception:
        pass

    answer = str(result.get("response") or "")
    dashboard_event_bus.publish("agent_response", {
        "run_id": run_id,
        "text": answer[:12000],
        "model": result.get("model"),
        "mode": result.get("mode"),
        "steps": result.get("steps"),
        "tool_calls": list(result.get("tool_calls") or [])[:30],
    })

    if talking and answer:
        from core.speech import speech_engine

        speech = await asyncio.to_thread(
            speech_engine.synthesize,
            answer,
            payload.get("voice"),
            int(payload.get("speech_rate") or 165),
        )
        speech.pop("path", None)
        if speech.get("success"):
            speech["audio_url"] = f"/speech/audio/{speech['audio_id']}"
            dashboard_event_bus.publish("speech_ready", {
                "run_id": run_id,
                "text": answer[:12000],
                "audio_url": speech["audio_url"],
                "audio_id": speech["audio_id"],
                "backend": speech.get("backend"),
                "estimated_duration": speech.get("estimated_duration"),
            })
        else:
            dashboard_event_bus.publish("speech_unavailable", {
                "run_id": run_id, "error": speech.get("error"), "text": answer[:12000],
            })
        result["speech"] = speech

    dashboard_event_bus.publish("session_finished", {
        "run_id": run_id, "success": True, "talking": talking,
        "model": result.get("model"), "steps": result.get("steps"),
    })
    try:
        _run_bus.publish(run_id, "agent_response", {"text": str(result.get("response") or "")[:8000],
                                                     "steps": result.get("steps")})
        _run_bus.finish(run_id, "finished", result)
    except Exception:
        pass
    return result

@app.post("/run/cancel/{run_id}")
async def run_cancel(run_id: str):
    """Cooperatively cancel a real run and its owning queue job, if any."""
    job_result = None
    for job in _job_queue.list_jobs(limit=500):
        if job.get("run_id") == run_id and job.get("status") in {"queued", "running"}:
            job_result = _job_queue.cancel(job["id"])
            break
    cancelled = _run_bus.cancel(run_id)
    if not cancelled and not (job_result and job_result.get("cancelled")):
        return JSONResponse({"cancelled": False, "run_id": run_id,
                             "error": "active run not found"}, status_code=404)
    return {"cancelled": True, "run_id": run_id, "job": job_result,
            "stage": (job_result or {}).get("stage", "cooperative")}


@app.post("/run/steer")
async def run_steer(payload: dict = None):
    """Send a mid-run constraint for an active run.

    The instruction is queued on the run's steer inbox (``RunBus.steer``): the
    executing agent loop drains it at its next step boundary and injects it
    into the conversation as a user message, and the event is published on the
    run's stream so dashboards/SSE see it live. Mid-token injection into an
    in-flight model call is still not possible — steering applies at the next
    step boundary — but it now genuinely reaches the model instead of only
    being recorded.
    """
    payload = payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    text = str(payload.get("text") or payload.get("steer") or "").strip()
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)

    delivered = False
    queued = False
    run = _run_bus.get(run_id) if run_id else None
    if run is not None:
        # queue on the inbox (the agent drains it) + publish on the stream
        delivered = _run_bus.steer(run_id, text)
        queued = delivered

    # Also signal any matching queued job (used for cooperative cancel/control).
    job_info = None
    try:
        from gateway.queue import job_queue
        for job in job_queue.list_jobs() if hasattr(job_queue, "list_jobs") else []:
            if run_id and getattr(job, "run_id", None) == run_id:
                job_info = job.id
    except Exception:
        pass

    return {"ok": True, "run_id": run_id, "applied_to_stream": delivered,
            "queued_for_agent": queued,
            "job_id": job_info,
            "note": ("Steer queued for the active run: the agent applies it at its "
                     "next step boundary and the event is on the run stream." if delivered
                     else "No active run found for that run_id; the message was not applied.")}


@app.get("/platforms")
async def platforms():
    return {"platforms": list(set([k.split(':')[0] for k in AGENTS.keys()])), "active_agents": len(AGENTS), "task_tracker": task_tracker.get_status()}

# CLI for gateway setup/start - free
def setup(platform: str):
    print(f"[Gateway] Setup for {platform} - Free")
    if platform == "telegram":
        token = os.getenv("TELEGRAM_BOT_TOKEN") or config.telegram_bot_token
        if not token:
            print("Set TELEGRAM_BOT_TOKEN env: https://t.me/BotFather /newbot (free)")
            print("Then: hermus gateway start  (auto long-poll) OR set webhook to /webhook/telegram")
        else:
            print(f"Telegram token found: {token[:10]}... - Ready")
            print("Modes:")
            print("  1) Long-polling (default, no public URL): hermus gateway start")
            print("  2) Webhook: export HERMUS_TELEGRAM_MODE=webhook")
            print(f"     https://api.telegram.org/bot{token}/setWebhook?url=https://yourdomain.com/webhook/telegram")
            print("Bot will REALLY sendMessage replies (not stub).")
    elif platform == "discord":
        token = os.getenv("DISCORD_BOT_TOKEN") or config.discord_bot_token
        if not token:
            print("Set DISCORD_BOT_TOKEN env: https://discord.com/developers/applications (free)")
            print("Enable Message Content Intent in the Discord developer portal.")
        else:
            print(f"Discord token found - bot starts with gateway (mention or DM the bot)")
    else:
        print(f"Platform {platform} setup - just set env token")

def start(port: int = None):
    port = port or config.gateway_port
    print(f"[Gateway] Starting free gateway on port {port} - Single process for all platforms")
    print(f"Endpoints: /webhook/telegram, /command, /platforms, /agents/status, /control")
    print(f"Channels: /channels/status, /channels/start, /telegram/send")
    print(f"Tools/MCP/Embeddings: /tools, /mcp/servers, /mcp/connect, /embeddings/status|/ingest|/search")
    print(f"Docs: http://localhost:{port}/docs")
    print(f"Control room: http://localhost:{port}/control")
    print(f"Computer control (API): http://localhost:{port}/computer/status")
    print(f"Remote control (API): http://localhost:{port}/remote/status")
    print(f"Plugins: /plugins | Resources: /computer/resources | Delegation: /computer/delegate")
    print(f"Telegram mode={getattr(config,'telegram_mode','auto')} | auto_channels={getattr(config,'auto_start_channels',True)}")
    print(f"Cross-platform continuity: Same user across Telegram/Discord/CLI shares memory via SQLite FTS5 + embeddings")
    uvicorn.run(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Hermus Gateway Free")
    parser.add_argument("action", choices=["setup", "start"], help="setup or start")
    parser.add_argument("--platform", default="telegram", help="telegram, discord, etc.")
    parser.add_argument("--port", type=int, default=config.gateway_port)
    args = parser.parse_args()
    if args.action == "setup":
        setup(args.platform)
    else:
        start(args.port)
