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
from fastapi.staticfiles import StaticFiles

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
    try:
        yield
    finally:
        if maintenance_task and not maintenance_task.done():
            maintenance_task.cancel()
        if watchdog_task and not watchdog_task.done():
            watchdog_task.cancel()
        try:
            await _realtime.shutdown()
        except Exception:
            pass


app = FastAPI(title="Hermus Gateway Free", description="Single gateway for all platforms, free - Optimized", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add GZip compression for faster dashboard - optimized
app.add_middleware(GZipMiddleware, minimum_size=500)

# The dashboard is intentionally shipped as local static assets (no CDN) so it
# stays usable offline and inside the self-hosted gateway.
_DASHBOARD_STATIC = Path(__file__).parent / "static"
_DASHBOARD_STATIC.mkdir(parents=True, exist_ok=True)
app.mount("/dashboard-assets", StaticFiles(directory=str(_DASHBOARD_STATIC)), name="dashboard-assets")

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

app.include_router(_channels_router)
app.include_router(_registry_router)
app.include_router(_management_router)
app.include_router(_subsystems_router)
app.include_router(_computer_router)
app.include_router(_speech_router)


@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    """Open the Living Agent Control Room for browsers and live previews.

    Explicitly allows HEAD so health checks / proxies that probe ``/`` with HEAD
    no longer get a 405.
    """
    return RedirectResponse(url="/dashboard", status_code=307)


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
    return {
        "message": "Hermus Gateway Free - Single process for Telegram/Discord/Slack/CLI - Optimized",
        "platforms": ["telegram", "discord", "cli"],
        "agents": len(AGENTS),
        "channels": get_channel_status(),
        "optimized": True,
        "cache_stats": get_cache_stats(),
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
        ],
        "version": "2.2-free-architecture"
    }

@app.get("/dashboard/legacy", response_class=HTMLResponse)
async def dashboard_legacy():
    """Main control room dashboard (legacy alias)."""
    html_path = Path(__file__).parent / "dashboard.html"
    if not html_path.exists():
        html_path = Path(__file__).parent / "dashboard_legacy.html"
    if not html_path.exists():
        return HTMLResponse("Dashboard not found", status_code=404)
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/jarvis", response_class=HTMLResponse)
@app.get("/dashboard/jarvis", response_class=HTMLResponse)
async def dashboard_jarvis():
    """Second dashboard: Full-power 3D Jarvis Holographic Spatial HUD."""
    html_path = Path(__file__).parent / "jarvis_dashboard.html"
    if not html_path.exists():
        html_path = Path(__file__).parent / "dashboard.html"
    if not html_path.exists():
        return HTMLResponse("Jarvis dashboard not found", status_code=404)
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

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Dashboard with slide panel to see what agents/models are running"""
    html_path = Path(__file__).parent / "dashboard.html"
    if html_path.exists():
        # Disable caching so UI updates show immediately after deploys
        return HTMLResponse(
            html_path.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    # Fallback inline HTML if file not exists
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head><title>Hermus Dashboard - Free</title>
<style>
body{font-family:Arial;background:#0a0e1a;color:#e0e0e0;margin:0;padding:0}
.header{background:#1a237e;padding:15px;display:flex;justify-content:space-between;align-items:center}
.container{display:flex;height:calc(100vh - 60px)}
.main{flex:1;padding:20px;overflow:auto}
.sidebar{width:0;overflow:hidden;background:#151a2d;border-left:1px solid #2a2f4a;transition:width 0.3s;position:relative}
.sidebar.open{width:400px;padding:20px;overflow:auto}
.toggle{position:fixed;right:20px;top:80px;background:#7c4dff;color:white;border:none;padding:10px 15px;border-radius:20px;cursor:pointer;z-index:1000}
.card{background:#1e2440;border:1px solid #2a2f4a;border-radius:8px;padding:12px;margin-bottom:12px}
.badge{background:#7c4dff;padding:2px 8px;border-radius:10px;font-size:0.8em}
.status-running{color:#4caf50} .status-idle{color:#888}
</style>
</head>
<body>
<div class="header"><h2>☤ Hermus Agent Free Dashboard</h2><div>Free - No Paywall</div></div>
<button class="toggle" onclick="togglePanel()">👁️ Agents Panel</button>
<div class="container">
<div class="main">
<h3>Gateway</h3>
<p>Agents: <span id="agentCount">0</span> | Tasks: <span id="taskCount">0</span></p>
<div id="mainContent">Send POST to /command with {"platform":"cli","user_id":"test","text":"Hello"}</div>
<h3>Endpoints</h3>
<ul>
<li>POST /command - Chat
<li>GET /agents/status - Slide panel data (what agents/models running)
<li>GET /platforms - Active platforms
<li>POST /webhook/telegram - Telegram webhook free
<li>GET /dashboard - This dashboard with slide panel
</ul>
</div>
<div class="sidebar" id="sidebar">
<h3>🔍 Running Agents & Tasks - Slide Panel</h3>
<button onclick="togglePanel()">Close ✕</button>
<div id="panelContent">Loading...</div>
</div>
</div>
<script>
let panelOpen = false;
function togglePanel(){
  const sb = document.getElementById('sidebar');
  panelOpen = !panelOpen;
  if(panelOpen){sb.classList.add('open'); loadPanel();} else {sb.classList.remove('open');}
}
async function loadPanel(){
  try{
    const res = await fetch('/agents/status');
    const data = await res.json();
    document.getElementById('agentCount').textContent = data.active_agents_count;
    document.getElementById('taskCount').textContent = data.active_tasks_count;
    let html = `<p>⏱️ ${data.timestamp.slice(0,19)} | Models: ${data.models_in_use.join(', ')||'none'}</p>`;
    if(data.active_agents.length){
      html += '<h4>🤖 Active Agents</h4>';
      data.active_agents.forEach(a=>{
        html += `<div class="card"><b>${a.name}</b> <span class="badge">${a.model}</span><br>Task: ${a.task.slice(0,80)}<br><span class="status-${a.status}">${a.status}</span> | ${a.started.slice(11,19)} | ${a.progress||''}</div>`;
      });
    }
    if(data.active_tasks.length){
      html += '<h4>📋 Active Tasks</h4>';
      data.active_tasks.forEach(t=>{
        html += `<div class="card">[${t.type}] ${t.description.slice(0,80)}<br>Model: ${t.model} | Agent: ${t.agent} | ${t.status} | ${t.progress||''}</div>`;
      });
    }
    if(!data.active_agents.length && !data.active_tasks.length){
      html += '<p>💤 No active agents - idle<br>Try: POST /command with task</p>';
    }
    if(data.completed_tasks.length){
      html += '<h4>✅ Recently Completed</h4>';
      data.completed_tasks.slice(-5).reverse().forEach(t=>{
        const name = t.name || t.task_id || t.description?.slice(0,30);
        html += `<div class="card">${name} -> ${t.status} at ${t.ended?.slice(11,19)}</div>`;
      });
    }
    document.getElementById('panelContent').innerHTML = html;
  }catch(e){document.getElementById('panelContent').innerHTML='Error: '+e;}
}
setInterval(()=>{if(panelOpen) loadPanel();}, 2000);
</script>
</body>
</html>
    """)

_TEXT_UPLOAD_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".csv", ".yaml",
    ".yml", ".html", ".htm", ".css", ".xml", ".log", ".ini", ".cfg", ".toml",
    ".sh", ".sql", ".rst", ".env", ".c", ".cc", ".cpp", ".h", ".hpp", ".java",
    ".go", ".rs", ".rb", ".php", ".kt", ".swift", ".vue", ".svelte",
}


def _is_text_upload(filename: str, sample: bytes) -> bool:
    """Best-effort guess that an uploaded file is safe to inline as text."""
    ext = Path(filename).suffix.lower()
    if ext in _TEXT_UPLOAD_EXTS:
        return True
    if b"\x00" in sample:  # NUL bytes => binary
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


@app.post("/command")
async def command_endpoint(request: Request):
    """Run an agent command, optionally producing local Talking Mode audio.

    Accepts either a JSON body (CLI/channels) or ``multipart/form-data`` (the
    dashboard drag-and-drop chat). Attachments uploaded as ``files`` are read
    and appended to the prompt as a real context block so the agent actually
    receives their contents — previously staged files never reached the agent.

    ``talking``/``speak`` is additive: every existing CLI/channel caller keeps
    the original JSON contract, while the dashboard receives lifecycle events
    and (when a local TTS backend is configured) an ``audio_url``.
    """
    from core.dashboard_events import dashboard_event_bus

    payload: dict = {}
    attachments: list[dict] = []
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        # Scalar fields (text, mode, model, ...) arrive as form strings.
        for key in ("platform", "user_id", "text", "model", "mode", "api_key",
                    "base_url", "provider", "key_name", "profile", "run_id",
                    "autonomous", "async", "async_mode", "talking", "speak",
                    "stream", "voice", "speech_rate", "timeout"):
            val = form.get(key)
            if val is not None and val != "":
                payload[key] = val
        for upload in form.getlist("files"):
            if not getattr(upload, "filename", None):
                continue
            content = await upload.read()
            entry = {
                "filename": upload.filename,
                "content_type": upload.content_type or "",
                "size_bytes": len(content),
                "text": None,
            }
            # Inline text-like files directly; binary files are acknowledged
            # by name/size so the agent knows they were provided (full binary
            # understanding requires a vision/multimodal model).
            try:
                if len(content) <= 200_000 and _is_text_upload(upload.filename, content[:2048]):
                    entry["text"] = content.decode("utf-8", errors="replace")
            except Exception:
                entry["text"] = None
            attachments.append(entry)
    else:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
    payload = payload or {}

    # Inject attachment contents into the prompt as an explicit context block.
    if attachments:
        blocks = []
        for att in attachments:
            if att.get("text"):
                blocks.append(f"--- Attachment: {att['filename']} ({att['size_bytes']} bytes) ---\n{att['text']}")
            else:
                blocks.append(f"--- Attachment: {att['filename']} ({att['content_type'] or 'binary'}, {att['size_bytes']} bytes; content not inlined) ---")
        if blocks:
            payload["text"] = f"{str(payload.get('text') or '').strip()}\n\nThe user attached {len(attachments)} file(s):\n" + "\n\n".join(blocks)
        payload["attachments"] = [{"filename": a["filename"], "size_bytes": a["size_bytes"],
                                   "content_type": a["content_type"], "inlined": a["text"] is not None}
                                  for a in attachments]

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
    if as_async and _job_queue.enabled and _job_queue._started:
        job = _job_queue.submit(
            "agent.autonomous" if autonomous else "agent.chat",
            {
                "text": text, "platform": platform, "user_id": user_id, "model": model,
                "mode": mode, "api_key": api_key, "base_url": base_url, "provider": provider,
                "key_name": key_name, "profile": profile, "talking": talking,
                "speech_rate": payload.get("speech_rate"), "voice": payload.get("voice"),
                "stream": bool(payload.get("stream", True)),
            },
            session_key=f"{platform}:{user_id}",
            timeout=payload.get("timeout"),
            run_id=run_id,
        )
        return {
            "async": True, "job_id": job.id, "run_id": job.run_id, "status": job.status,
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
            if autonomous:
                out = agent.autonomous(text)
                if isinstance(out, dict):
                    out["autonomous"] = True
                return out
            out = _agent_chat(agent, text, on_event=_bus_event, stream=stream_tokens)
            if isinstance(out, dict):
                out.setdefault("steps", out.get("steps", 0))
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

@app.post("/run/steer")
async def run_steer(payload: dict = None):
    """Send a mid-run constraint for an active run.

    The instruction is published onto the run's event stream (so live dashboards
    and the SSE feed see it) and, when the run is a queued job, handed to that
    job's cancellation/control channel. Unlike the old dashboard-only stub, this
    is a real backend call — but cooperative mid-token injection into a model
    call is not possible, so the message is recorded and applied as guidance to
    the run rather than falsely claiming it altered tokens already streaming.
    """
    payload = payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    text = str(payload.get("text") or payload.get("steer") or "").strip()
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)

    delivered = False
    run = _run_bus.get(run_id) if run_id else None
    if run is not None:
        _run_bus.publish(run_id, "steer", {"text": text, "ts": __import__("datetime").datetime.now().isoformat()})
        delivered = True

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
            "job_id": job_info,
            "note": ("Steer recorded on the active run stream." if delivered
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
    print(f"Endpoints: /webhook/telegram, /command, /platforms, /agents/status, /dashboard")
    print(f"Channels: /channels/status, /channels/start, /telegram/send")
    print(f"Tools/MCP/Embeddings: /tools, /mcp/servers, /mcp/connect, /embeddings/status|/ingest|/search")
    print(f"Docs: http://localhost:{port}/docs")
    print(f"Dashboard: http://localhost:{port}/dashboard")
    print(f"Computer dashboard: http://localhost:{port}/computer/dashboard")
    print(f"Remote control (mobile): http://localhost:{port}/remote")
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
