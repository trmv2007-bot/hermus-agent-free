"""Gateway - Single process for Telegram, Discord, Slack, WhatsApp, Signal, CLI - free, cross-platform continuity - Optimized with caching, compression, rate limiting"""
import json
import os
import shutil
import threading
import asyncio
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from fastapi import FastAPI, Request, Header, WebSocket
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, Response, RedirectResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

from core.config import config
from core.agent import HermusAgent
from core.task_tracker import task_tracker
from core.cache import get_cache_stats
try:
    from gateway.channels import (
        handle_telegram_update,
        start_all_channels,
        get_channel_status,
        telegram_send_message,
        get_telegram_token,
        get_discord_token,
        set_agent_factory,
    )
except ImportError:
    from channels import (  # type: ignore
        handle_telegram_update,
        start_all_channels,
        get_channel_status,
        telegram_send_message,
        get_telegram_token,
        get_discord_token,
        set_agent_factory,
    )

app = FastAPI(title="Hermus Gateway Free", description="Single gateway for all platforms, free - Optimized")

# Add GZip compression for faster dashboard - optimized
app.add_middleware(GZipMiddleware, minimum_size=500)

# The dashboard is intentionally shipped as local static assets (no CDN) so it
# stays usable offline and inside the self-hosted gateway.
_DASHBOARD_STATIC = Path(__file__).parent / "static"
_DASHBOARD_STATIC.mkdir(parents=True, exist_ok=True)
app.mount("/dashboard-assets", StaticFiles(directory=str(_DASHBOARD_STATIC)), name="dashboard-assets")

# Store agents per user/platform for continuity
AGENTS: Dict[str, HermusAgent] = {}

def get_agent_for_user(
    platform: str,
    user_id: str,
    model: str = None,
    mode: str = "agent",
    api_key: str = None,
    base_url: str = None,
) -> HermusAgent:
    # The cache key includes model + base_url so switching the model or the
    # custom URL/API in chat actually takes effect (previously a changed model
    # was silently ignored because the cached agent kept the old one).
    model = model or config.model
    key = f"{platform}:{user_id}:{mode}:{model}:{base_url or ''}"
    if key not in AGENTS:
        AGENTS[key] = HermusAgent(
            model=model,
            session_id=f"{platform}_{user_id}_{os.urandom(4).hex()}",
            mode=mode,
            api_key=api_key,
            base_url=base_url,
        )
    return AGENTS[key]


def _agent_factory(platform: str, user_id: str, model: str = None, mode: str = "agent"):
    return get_agent_for_user(platform, user_id, model=model, mode=mode)


def _check_gateway_auth(request: Request, x_hermus_token: Optional[str] = None) -> Optional[JSONResponse]:
    """Optional gateway token auth via HERMUS_GATEWAY_TOKEN / config.gateway_api_token."""
    expected = config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN")
    if not expected:
        return None  # open (local default)
    provided = x_hermus_token or request.headers.get("X-Hermus-Token") or request.query_params.get("token")
    if provided != expected:
        return JSONResponse({"error": "Unauthorized - set X-Hermus-Token header"}, status_code=401)
    return None


@app.on_event("startup")
async def _startup_channels():
    """Auto-start Telegram polling + Discord bot when tokens present."""
    set_agent_factory(_agent_factory)
    if getattr(config, "auto_start_channels", True):
        mode = getattr(config, "telegram_mode", "auto")
        started = start_all_channels(_agent_factory, telegram_mode=mode)
        print(f"[Gateway] Channels started: {started}")
    if getattr(config, "background_agents_enabled", True):
        try:
            _watchdog_task = asyncio.create_task(_background_agent_watchdog())
        except Exception as e:
            print(f"[Gateway] background-agent watchdog failed to start: {e}")


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


@app.get("/")
async def root():
    """Open the Living Agent Control Room for browsers and live previews."""
    return RedirectResponse(url="/dashboard", status_code=307)


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
    """Previous all-in-one dashboard, kept as a compatibility escape hatch."""
    html_path = Path(__file__).parent / "dashboard_legacy.html"
    if not html_path.exists():
        return HTMLResponse("Legacy dashboard not found", status_code=404)
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/cache/stats")
async def cache_stats():
    """Get cache stats - for optimization dashboard"""
    from core.cache import get_cache_stats, clear_all_caches
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

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Telegram free Bot API webhook - real sendMessage reply + multi-step agent"""
    try:
        data = await request.json()
        # Full handler: chat + sendMessage (+ voice transcribe)
        result = handle_telegram_update(data, agent_factory=_agent_factory)
        return JSONResponse(result if isinstance(result, dict) else {"ok": True, "result": result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/channels/status")
async def channels_status():
    """Telegram/Discord channel runtime status"""
    return {
        "channels": get_channel_status(),
        "telegram_token_set": bool(get_telegram_token()),
        "discord_token_set": bool(get_discord_token()),
        "agents": len(AGENTS),
    }


@app.post("/channels/start")
async def channels_start(payload: Dict = None):
    """Manually start Telegram polling + Discord bot"""
    payload = payload or {}
    mode = payload.get("telegram_mode") or getattr(config, "telegram_mode", "auto")
    started = start_all_channels(_agent_factory, telegram_mode=mode)
    return {"started": started, "status": get_channel_status()}


@app.post("/telegram/send")
async def telegram_send(payload: Dict):
    """Send a Telegram message directly (ops / cron delivery)"""
    chat_id = payload.get("chat_id")
    text = payload.get("text", "")
    if not chat_id or not text:
        return JSONResponse({"ok": False, "error": "need chat_id and text"}, status_code=400)
    return telegram_send_message(chat_id, text)


@app.get("/tools")
async def tools_list():
    """List registered tools from auto tool registry"""
    from core.tool_registry import tool_registry

    return tool_registry.list_tools()


@app.get("/public-apis/search")
async def public_apis_search(
    query: str = "",
    category: str = "",
    auth: str = "any",
    https_only: bool = True,
    cors: str = "any",
    limit: int = 10,
    refresh: bool = False,
):
    """Discover APIs from the bundled/updatable public-apis catalog."""
    from tools.public_apis import public_api_catalog

    return public_api_catalog.search(
        query=query,
        category=category,
        auth=auth,
        https_only=https_only,
        cors=cors,
        limit=limit,
        refresh=refresh,
    )


@app.get("/public-apis/categories")
async def public_apis_categories():
    from tools.public_apis import public_api_catalog

    return public_api_catalog.categories()


@app.post("/public-apis/refresh")
async def public_apis_refresh():
    from tools.public_apis import public_api_catalog

    return public_api_catalog.refresh()


@app.get("/mcp/servers")
async def mcp_servers():
    from core.mcp_client import mcp_manager

    return {"servers": mcp_manager.list_servers()}


@app.post("/mcp/connect")
async def mcp_connect():
    from core.mcp_client import mcp_manager
    from core.tool_registry import tool_registry

    result = mcp_manager.connect_enabled()
    tool_registry.load(force=True)
    return {**result, "tools": tool_registry.list_tools()}


@app.get("/embeddings/status")
async def embeddings_status():
    from core.embeddings import embedding_store

    return embedding_store.backend_info()


@app.get("/providers")
async def providers_list():
    from core.providers import list_providers

    return {"providers": list_providers()}


@app.get("/eval/summary")
async def eval_summary():
    """Eval harness summary (Phase 4) for the dashboard Reasoning pane."""
    try:
        from core.reasoning.eval import eval_harness

        return eval_harness.summary()
    except Exception as e:
        return {"error": str(e)}


@app.get("/counsel/status")
async def counsel_status():
    """Counsel constitution + amendment status (Phase 2/4) for the dashboard."""
    try:
        from core.counsel.meta import meta_counsel

        return meta_counsel.status()
    except Exception as e:
        return {"error": str(e)}


@app.get("/fleet/workers")
async def fleet_workers():
    from core.model_fleet import model_fleet

    return model_fleet.list_workers()


@app.post("/fleet/run")
async def fleet_run(payload: Dict):
    from core.model_fleet import model_fleet

    goal = payload.get("goal") or payload.get("prompt") or ""
    if not goal:
        return JSONResponse({"error": "goal required"}, status_code=400)
    strategy = payload.get("strategy", "auto")
    models = payload.get("models")
    if isinstance(models, str):
        models = [m.strip() for m in models.split(",") if m.strip()]
    providers = payload.get("providers")
    if isinstance(providers, str):
        providers = [p.strip() for p in providers.split(",") if p.strip()]
    return model_fleet.auto_distribute(
        goal,
        strategy=strategy,
        models=models,
        providers=providers,
        max_workers=int(payload.get("max_workers") or 4),
    )


@app.get("/keys/health")
async def keys_health(provider: str = None):
    from core.multi_key import multi_key_manager

    return {"results": multi_key_manager.check_all_health(provider)}


@app.get("/keys/rates")
async def keys_rates(provider: str = None):
    from core.multi_key import multi_key_manager

    return multi_key_manager.rate_status(provider)


@app.get("/keys/models")
async def keys_models(provider: str, key: str = None, base_url: str = None):
    from core.multi_key import multi_key_manager

    return multi_key_manager.discover_models(provider, api_key=key, base_url=base_url)


@app.post("/embeddings/ingest")
async def embeddings_ingest(payload: Dict):
    from core.embeddings import embedding_store

    path = payload.get("path")
    if not path:
        return JSONResponse({"error": "path required"}, status_code=400)
    return embedding_store.ingest_path(path, source=payload.get("source"))


@app.post("/embeddings/search")
async def embeddings_search(payload: Dict):
    from core.embeddings import embedding_store

    query = payload.get("query", "")
    limit = int(payload.get("limit", 5))
    hybrid = payload.get("hybrid", True)
    if hybrid:
        return embedding_store.hybrid_search(query, limit=limit)
    return embedding_store.search(query, limit=limit)

@app.post("/command")
async def command_endpoint(payload: Dict):
    """Run an agent command, optionally producing local Talking Mode audio.

    ``talking``/``speak`` is additive: every existing CLI/channel caller keeps
    the original JSON contract, while the dashboard receives lifecycle events
    and (when a local TTS backend is configured) an ``audio_url``.
    """
    from core.dashboard_events import dashboard_event_bus

    payload = payload or {}
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

    agent = get_agent_for_user(
        platform, user_id, model=model, mode=mode, api_key=api_key, base_url=base_url
    )
    if profile:
        agent.profile = profile

    dashboard_event_bus.publish("session_started", {
        "run_id": run_id, "text": text[:1000], "mode": mode,
        "model": model or agent.model_name, "talking": talking,
        "platform": platform,
    })
    try:
        if autonomous:
            result = await asyncio.to_thread(agent.autonomous, text)
            result["autonomous"] = True
        else:
            result = await asyncio.to_thread(agent.chat, text)
            # Self-healing watchdog (architecture upgrade)
            from core.integrations import maybe_self_heal

            result = maybe_self_heal(result)
    except Exception as exc:
        dashboard_event_bus.publish("session_failed", {"run_id": run_id, "error": str(exc)})
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
    return result

@app.get("/platforms")
async def platforms():
    return {"platforms": list(set([k.split(':')[0] for k in AGENTS.keys()])), "active_agents": len(AGENTS), "task_tracker": task_tracker.get_status()}

@app.get("/keys/list")
async def keys_list():
    """List API keys - redacted preview + health/models metadata for dashboard"""
    try:
        from core.multi_key import multi_key_manager
        from core.custom_api import custom_api_manager
        # Prefer rich redacted listing from multi_key manager
        redacted = multi_key_manager.list_keys(redact=True)
        llm_raw = multi_key_manager.list_keys(redact=False)
        total_llm = sum(len(v) for v in llm_raw.values())

        custom_apis = custom_api_manager.list_apis()
        custom_redacted = []
        for api in custom_apis:
            token = api.get("auth", {}).get("token") or api.get("auth", {}).get("value") or ""
            custom_redacted.append({
                "name": api["name"],
                "description": api.get("description", ""),
                "url": api.get("url", ""),
                "method": api.get("method", "GET"),
                "preview": f"{token[:6]}...{token[-4:]}" if token and len(token) > 10 else ("no-token" if not token else "****"),
                "id": api.get("id", ""),
                "created": api.get("created", ""),
            })

        return {
            "llm_keys": redacted,
            "custom_apis": custom_redacted,
            "total_llm_keys": total_llm,
            "total_custom_apis": len(custom_apis),
            "rates": multi_key_manager.rate_status(),
            "note": "Add any OpenAI-compatible key via dashboard Keys pane or hermus multikey add. Local only.",
        }
    except Exception as e:
        return {"error": str(e)}

@app.post("/keys/add")
async def keys_add(payload: Dict):
    """Add ANY AI API key — auto health + model discovery"""
    try:
        from core.multi_key import multi_key_manager
        provider = payload.get("provider", "groq")
        key = payload.get("key") or payload.get("api_key") or payload.get("token")
        name = payload.get("name")
        if not key and provider not in ("ollama", "lmstudio"):
            return JSONResponse({"success": False, "error": "Missing key/api_key/token"}, status_code=400)

        result = multi_key_manager.add_key(
            provider,
            key or "",
            name=name,
            base_url=payload.get("base_url"),
            default_model=payload.get("model") or payload.get("default_model"),
            rpm_limit=payload.get("rpm") or payload.get("rpm_limit"),
            tpm_limit=payload.get("tpm") or payload.get("tpm_limit"),
            auto_discover=payload.get("auto_discover", True),
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post("/keys/remove")
async def keys_remove(payload: Dict):
    """Remove API key via Settings"""
    try:
        from core.multi_key import multi_key_manager
        provider = payload.get("provider")
        key = payload.get("key") or payload.get("name")
        if not provider or not key:
            return JSONResponse({"success": False, "error": "Need provider and key/name"}, status_code=400)
        result = multi_key_manager.remove_key(provider, key)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post("/custom-apis/add")
async def custom_apis_add(payload: Dict):
    """Add custom API via Settings panel - free"""
    try:
        from core.custom_api import custom_api_manager
        result = custom_api_manager.add_api(payload)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.get("/custom-apis/list")
async def custom_apis_list():
    """List custom APIs"""
    try:
        from core.custom_api import custom_api_manager
        apis = custom_api_manager.list_apis()
        return {"custom_apis": apis, "count": len(apis)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/custom-apis/remove")
async def custom_apis_remove(payload: Dict):
    """Remove custom API by name or id - for Settings panel add API key in settings"""
    try:
        from core.custom_api import custom_api_manager
        name = payload.get("name")
        api_id = payload.get("id")
        # Try by id first, then name
        if api_id:
            result = custom_api_manager.remove_api(api_id)
            if not result.get("success"):
                # Try by name
                result = custom_api_manager.remove_api(name)
        else:
            result = custom_api_manager.remove_api(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/response-times")
async def response_times_list():
    """Get response time history - for Settings panel response test"""
    try:
        from core.response_tester import response_tester
        history = response_tester.get_history(limit=50)
        stats = response_tester.get_stats()
        return {"history": history, "stats": stats}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/response-times/test")
async def response_times_test(payload: Dict):
    """Test response time for API key - how much time does API key take to get response from AI model - free"""
    try:
        from core.response_tester import response_tester
        provider = payload.get("provider", "groq")
        api_key = payload.get("api_key") or payload.get("key")
        model = payload.get("model")
        prompt = payload.get("prompt", "Hello, what is Python async?")
        api_name = payload.get("api_name")

        if api_name:
            # Test custom API response time
            test_args = payload.get("test_args") or {}
            if isinstance(test_args, str):
                try:
                    import json
                    test_args = json.loads(test_args)
                except:
                    test_args = {}
            if api_key:
                result = response_tester.test_custom_api_key(api_name, api_key=api_key, test_args=test_args)
            else:
                # Test all keys for same custom API name
                results = response_tester.test_all_keys_for_custom_api(api_name, test_args=test_args)
                return {"results": results, "count": len(results), "api_name": api_name}
        else:
            # Test LLM provider key
            if api_key:
                result = response_tester.test_llm_key(provider, api_key, model=model, prompt=prompt)
                return result
            else:
                # Test all keys for provider
                results = response_tester.test_all_keys_for_provider(provider, prompt=prompt, model=model)
                return {"results": results, "count": len(results), "provider": provider}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/update/check")
async def update_check():
    """Check if update available from GitHub - shows update in dashboard and CLI too - free"""
    try:
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        result = updater.check_for_updates()
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/update/pull")
async def update_pull():
    """Update from GitHub via git pull + pip install - like hermes update - free - shows update in dashboard and CLI"""
    try:
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        result = updater.update()
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/update/local")
async def update_local():
    """Get local commit info"""
    try:
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        return updater.get_local_commit()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/update/remote")
async def update_remote():
    """Get remote commit info from GitHub API free"""
    try:
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        return updater.get_remote_commit()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ---- Architecture-upgrade endpoints ------------------------------------------

@app.get("/agents")
async def background_agents_list():
    from core.agent_manager import agent_manager

    return {"agents": agent_manager.list()}


@app.post("/agents/start")
async def background_agents_start(payload: Dict):
    from core.agent_manager import agent_manager

    name = payload.get("name", "")
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    return agent_manager.start(name)


@app.post("/agents/stop")
async def background_agents_stop(payload: Dict):
    from core.agent_manager import agent_manager

    name = payload.get("name", "")
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    return agent_manager.stop(name)


@app.post("/agents/create")
async def background_agents_create(payload: Dict):
    from core.agent_manager import agent_manager

    name = payload.get("name", "")
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    return agent_manager.create(name, role=payload.get("role", "generic"),
                                model=payload.get("model"), persona=payload.get("persona"))


@app.get("/workspace")
async def workspace_info():
    from core.workspace import workspace as ws

    return {"base_dir": str(ws.base_dir), "projects": ws.list_projects(),
            "current": ws.current_project(), "dirs": {k: str(v) for k, v in ws.dirs.items()}}


@app.post("/workspace/create")
async def workspace_create(payload: Dict):
    from core.workspace import workspace as ws

    return ws.create_project(payload.get("name", ""), description=payload.get("description", ""))


@app.post("/workspace/use")
async def workspace_use(payload: Dict):
    from core.workspace import workspace as ws

    return ws.set_current_project(payload.get("name", ""))


@app.post("/memory2/remember")
async def memory2_remember(payload: Dict):
    from core.memory2 import memory2

    return memory2.remember(payload.get("kind", "semantic"), payload.get("content", ""),
                            importance=payload.get("importance", 5.0),
                            success=payload.get("success"), project=payload.get("project"))


@app.post("/memory2/recall")
async def memory2_recall(payload: Dict):
    from core.memory2 import memory2

    return {"results": memory2.recall(payload.get("query", ""), limit=int(payload.get("limit", 10)),
                                      kinds=payload.get("kinds"), project=payload.get("project"))}


@app.get("/permissions/log")
async def permissions_log(limit: int = 20):
    from core.permissions import permission_manager

    return {"log": permission_manager.recent(limit=limit)}


@app.post("/permissions/check")
async def permissions_check(payload: Dict):
    from core.permissions import permission_manager

    return permission_manager.check(payload.get("tool", ""), agent=payload.get("agent"),
                                    args=payload.get("args"))


@app.post("/permissions/set")
async def permissions_set(payload: Dict):
    from core.permissions import permission_manager

    return permission_manager.set_policy(payload.get("tool", ""), payload.get("decision", "ask"),
                                         agent=payload.get("agent"))


@app.post("/research")
async def research_run(payload: Dict):
    from core.research import research_pipeline

    return research_pipeline.run(payload.get("query", ""), limit=int(payload.get("limit", 10)))


@app.post("/router/select")
async def router_select(payload: Dict):
    from core.router2 import router2

    return router2.select(payload.get("text", ""))


@app.get("/screen/status")
async def screen_status():
    from core.integrations import _screen_recorder

    return _screen_recorder().status()


@app.post("/screen/start")
async def screen_start(payload: Dict = None):
    from core.computer import recording_policy
    from core.integrations import _screen_recorder

    payload = payload or {}
    fps = float(payload.get("fps", 10.0))
    max_seconds = float(payload.get("max_seconds", 30.0))
    valid = recording_policy.validate_settings(fps, max_seconds)
    if not valid.get("ok"):
        return {"success": False, "error": valid["error"]}
    output = payload.get("output_path") or None
    if output:
        try:
            output = str(recording_policy.output_path(output))
        except (ValueError, PermissionError) as exc:
            return {"success": False, "error": str(exc)}
    return _screen_recorder().start(max_seconds=max_seconds, fps=fps, output_path=output)


@app.post("/screen/stop")
async def screen_stop():
    from core.integrations import _screen_recorder

    return _screen_recorder().stop()


@app.post("/screen/save")
async def screen_save(payload: Dict):
    from core.computer import recording_policy
    from core.integrations import _screen_recorder

    try:
        path = recording_policy.output_path(payload.get("path", "recording.mp4"))
    except (ValueError, PermissionError) as exc:
        return {"success": False, "error": str(exc)}
    seconds = float(payload.get("seconds", 0.0))
    return _screen_recorder().save(str(path), seconds=seconds if seconds > 0 else None)


@app.post("/screen/analyze")
async def screen_analyze(payload: Dict):
    from core.computer import VideoAnalyzer
    from core.integrations import _screen_recorder

    frames = _screen_recorder().recent(float(payload.get("seconds", 10.0)))
    analyzer = (VideoAnalyzer.with_ollama(payload.get("model", "llava:7b"))
                if payload.get("use_vision", True) else VideoAnalyzer())
    return await asyncio.to_thread(
        analyzer.analyze,
        frames,
        payload.get("task", ""),
        int(payload.get("max_events", 12)),
    )


@app.post("/screen/watch")
async def screen_watch(payload: Dict):
    from core.computer import ScreenWatcher, VideoAnalyzer
    from core.integrations import _screen_recorder

    analyzer = VideoAnalyzer.with_ollama(payload.get("model", "llava:7b"))
    watcher = ScreenWatcher(_screen_recorder(), analyzer=analyzer)
    return await asyncio.to_thread(
        watcher.watch,
        payload.get("condition", ""),
        float(payload.get("timeout", 60.0)),
        0.25,
        int(payload.get("stable_matches", 1)),
        False,
    )


@app.post("/screen/action/before")
async def screen_action_before(payload: Dict):
    from core.integrations import _screen_action_manager

    return _screen_action_manager().before(
        payload.get("action", ""), payload.get("expected_state", "")
    )


@app.post("/screen/action/after")
async def screen_action_after(payload: Dict):
    from core.computer import ScreenVerifier, VideoAnalyzer
    from core.integrations import _screen_action_manager

    analyzer = (VideoAnalyzer.with_ollama(payload.get("model", "llava:7b"))
                if payload.get("use_vision", False) else None)
    verifier = ScreenVerifier(
        vision_model=analyzer.evaluate_condition if analyzer else None,
        transition_model=analyzer.evaluate_transition if analyzer else None,
    )
    return await asyncio.to_thread(
        _screen_action_manager().after,
        payload.get("action_id", ""),
        verifier,
    )


@app.post("/watchdog/handle")
async def watchdog_handle(payload: Dict):
    from core.watchdog import watchdog

    return watchdog.handle(payload.get("error", ""), context=payload.get("context", ""))


@app.get("/profiles")
async def profiles_list():
    from core.profiles import profile_manager

    return {"profiles": profile_manager.list()}


@app.post("/profiles/create")
async def profiles_create(payload: Dict):
    from core.profiles import profile_manager

    return profile_manager.create(payload.get("name", ""), persona=payload.get("persona"),
                                  model=payload.get("model"))


# ===========================================================================
# Computer Agent dashboard API - autonomous desktop agent (live status,
# tasks/checkpoints, plan graphs, world state, repairs, skills, recordings)
# ===========================================================================

def _computer_task_store():
    from core.computer import TaskStore
    return TaskStore()


def _task_dir(task_id: str) -> Path:
    return _computer_task_store().directory(task_id)


def _read_task_json(task_id: str, filename: str, default):
    path = _task_dir(task_id) / filename
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _recording_info(task_id: str) -> Optional[Dict]:
    directory = _task_dir(task_id)
    candidates = sorted(directory.glob("recording.*")) if directory.exists() else []
    if not candidates:
        return None
    video = candidates[0]
    try:
        size_mb = round(video.stat().st_size / (1024 * 1024), 2)
    except OSError:
        size_mb = None
    suffix = video.suffix.lower()
    return {
        "path": str(video),
        "filename": video.name,
        "size_mb": size_mb,
        "media_type": "video/mp4" if suffix == ".mp4" else ("video/webm" if suffix == ".webm" else None),
        "url": f"/computer/recording/{task_id}/video",
    }


def _checkpoint_summary(task_id: str) -> Optional[Dict]:
    """Compact live view of one persisted task checkpoint (world + counters)."""
    store = _computer_task_store()
    checkpoint = store.load(task_id)
    if checkpoint is None:
        return None
    data = checkpoint.to_dict()
    world = data.get("world_state") or {}
    completed = list(data.get("completed_states") or [])
    pending = list(data.get("pending_states") or [])
    total = max(1, len(completed) + len(pending))
    actions = _read_task_json(task_id, "actions.json", [])
    verifications = _read_task_json(task_id, "verification.json", [])
    repairs_payload = _read_task_json(task_id, "repairs.json", {})
    repairs = repairs_payload.get("repairs") if isinstance(repairs_payload, dict) else []
    retries = 0
    for action in actions if isinstance(actions, list) else []:
        if isinstance(action, dict) and int(action.get("attempt", 1) or 1) > 1 and action.get("phase") == "original_action":
            retries += 1
    timeline = _read_task_json(task_id, "timeline.json", {})
    return {
        "task_id": task_id,
        "task": data.get("task"),
        "status": data.get("status"),
        "current_state": data.get("current_state"),
        "task_state": world.get("task_state") or data.get("current_state"),
        "confidence": world.get("confidence", 0.0),
        "completed": len(completed),
        "pending": len(pending),
        "failed": len(list(data.get("failed_states") or [])),
        "progress": round(len(completed) / total * 100, 1),
        "plan_size": len(list(data.get("plan") or [])),
        "actions": len(actions) if isinstance(actions, list) else 0,
        "retries": retries,
        "repairs": len(repairs) if isinstance(repairs, list) else 0,
        "verifications": len(verifications) if isinstance(verifications, list) else 0,
        "timeline_events": len(timeline.get("events") or []) if isinstance(timeline, dict) else 0,
        "last_action": world.get("last_action"),
        "world": {
            key: world.get(key) for key in
            ("active_application", "active_window", "visible_targets", "dialogs", "task_state", "confidence")
        },
        "error": data.get("error"),
        "result": data.get("result"),
        "recordings": list(data.get("recordings") or []),
        "resume_count": data.get("resume_count", 0),
        "attempts": data.get("attempts", 0),
        "updated_at": data.get("updated_at"),
        "created_at": data.get("created_at"),
        "recording": _recording_info(task_id),
    }


def _repairs_aggregate(limit_recent: int = 12) -> Dict:
    """Aggregate repair history across all persisted tasks."""
    import re

    tasks = _computer_task_store().list()
    by_kind: Dict[str, Dict] = {}
    recent: list = []
    total = successes = 0
    for task in tasks:
        task_id = task["task_id"]
        payload = _read_task_json(task_id, "repairs.json", {})
        if not isinstance(payload, dict):
            continue
        diagnoses_by_state: Dict[str, list] = {}
        for d in payload.get("diagnoses") or []:
            if isinstance(d, dict):
                diagnoses_by_state.setdefault(str(d.get("state")), []).append(d)

        for repair in payload.get("repairs") or []:
            if not isinstance(repair, dict):
                continue
            candidates = diagnoses_by_state.get(
                str(repair.get("repair_for") or repair.get("state") or "")
            ) or []
            failure_text = repair.get("failure") or repair.get("failure_reason") or ""
            words = set(re.findall(r"[a-z0-9]+", str(failure_text).lower()))
            best, best_score = None, 0
            for candidate in candidates:
                score = len(words & set(re.findall(
                    r"[a-z0-9]+", str(candidate.get("failure_reason") or "").lower()
                )))
                if score > best_score:
                    best, best_score = candidate, score
            diagnosis = best or (candidates[-1] if candidates else None)
            kind = "unknown"
            if isinstance(diagnosis, dict) and isinstance(diagnosis.get("diagnosis"), dict):
                kind = str(diagnosis["diagnosis"].get("kind") or "unknown")
            ok = bool(repair.get("success", repair.get("outcome") == "success"))
            total += 1
            successes += 1 if ok else 0
            action = repair.get("action")
            action_name = action.get("action") if isinstance(action, dict) else None
            by_kind.setdefault(kind, {"count": 0, "successes": 0})
            by_kind[kind]["count"] += 1
            by_kind[kind]["successes"] += 1 if ok else 0
            recent.append({
                "task_id": task_id,
                "task": task.get("task"),
                "kind": kind,
                "state": repair.get("repair_for") or repair.get("state"),
                "action": action_name,
                "success": ok,
                "failure_reason": repair.get("failure") or repair.get("failure_reason"),
                "updated": task.get("updated_at"),
            })
    recent.sort(key=lambda r: str(r.get("updated") or ""), reverse=True)
    for kind in by_kind:
        by_kind[kind]["success_rate"] = round(
            by_kind[kind]["successes"] / max(1, by_kind[kind]["count"]) * 100, 1
        )
    return {
        "total": total,
        "successes": successes,
        "success_rate": round(successes / total * 100, 1) if total else None,
        "by_kind": by_kind,
        "recent": recent[:limit_recent],
    }


def _skill_stats(skills: list) -> Dict:
    runs = sum(int(s.get("runs", 0) or 0) for s in skills)
    successes = sum(int(s.get("successes", 0) or 0) for s in skills)
    return {
        "count": len(skills),
        "total_runs": runs,
        "total_successes": successes,
        "avg_success_rate": round(successes / runs * 100, 1) if runs else None,
    }


@app.get("/computer/status")
async def computer_status():
    """Live dashboard status: control center, current task, world, repairs, skills."""
    from core.computer import (
        ComputerActionController,
        ComputerSkillStore,
        ControlCenter,
        emergency_stop,
    )
    from core.computer.events import computer_event_bus
    from core.integrations import _screen_recorder

    store = _computer_task_store()
    tasks = sorted(
        store.list(),
        key=lambda t: str(t.get("updated_at") or ""),
        reverse=True,
    )
    running = [t for t in tasks if t.get("status") == "running"]
    current_id = running[0]["task_id"] if running else (tasks[0]["task_id"] if tasks else None)
    current = _checkpoint_summary(current_id) if current_id else None

    skills = ComputerSkillStore().list_skills()
    try:
        recorder_status = _screen_recorder().status()
    except Exception:
        recorder_status = {"running": False}

    try:
        control = ControlCenter(ComputerActionController()).status()
    except Exception:
        control = {"active": not emergency_stop.halted, "halted": emergency_stop.halted, "backends": {}}

    stats = {"total": len(tasks), "running": 0, "interrupted": 0, "failed": 0, "success": 0}
    for task in tasks:
        status = str(task.get("status") or "created")
        stats[status] = stats.get(status, 0) + 1

    return {
        "active": not emergency_stop.halted,
        "halted": emergency_stop.halted,
        "halt_reason": emergency_stop.reason,
        "control": control,
        "recording": recorder_status,
        "current_task": current,
        "tasks": [_checkpoint_summary(t["task_id"]) for t in tasks[:20]],
        "task_stats": stats,
        "world": (current or {}).get("world"),
        "skills": {"skills": skills[:10], "stats": _skill_stats(skills)},
        "repair_stats": _repairs_aggregate(),
        "recent_events": computer_event_bus.recent(40),
        "timestamp": datetime.now().astimezone().isoformat(),
    }


@app.get("/computer/tasks")
async def computer_tasks():
    """Task history with resume/checkpoint metadata."""
    store = _computer_task_store()
    tasks = sorted(
        store.list(),
        key=lambda t: str(t.get("updated_at") or ""),
        reverse=True,
    )
    stats = {"total": len(tasks), "running": 0, "interrupted": 0, "failed": 0, "success": 0}
    for task in tasks:
        status = str(task.get("status") or "created")
        stats[status] = stats.get(status, 0) + 1
    return {
        "tasks": [_checkpoint_summary(t["task_id"]) for t in tasks],
        "stats": stats,
    }


@app.get("/computer/task/{task_id}")
async def computer_task(task_id: str):
    """Full detail for one task: plan, timeline, actions, verifications, repairs."""
    store = _computer_task_store()
    checkpoint = store.load(task_id)
    if checkpoint is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    data = checkpoint.to_dict()
    actions = _read_task_json(task_id, "actions.json", [])
    verifications = _read_task_json(task_id, "verification.json", [])
    repairs_payload = _read_task_json(task_id, "repairs.json", {})
    timeline = _read_task_json(task_id, "timeline.json", {})
    summary_path = _task_dir(task_id) / "summary.md"
    retries = 0
    for action in actions if isinstance(actions, list) else []:
        if isinstance(action, dict) and int(action.get("attempt", 1) or 1) > 1 and action.get("phase") == "original_action":
            retries += 1
    return {
        **data,
        "graph": store.load_graph(task_id),
        "timeline": timeline,
        "actions": actions,
        "verifications": verifications,
        "repairs": repairs_payload,
        "summary": summary_path.read_text(encoding="utf-8") if summary_path.exists() else "",
        "recording": _recording_info(task_id),
        "stats": {
            "actions": len(actions) if isinstance(actions, list) else 0,
            "retries": retries,
            "verifications": len(verifications) if isinstance(verifications, list) else 0,
            "repairs": len(repairs_payload.get("repairs") or []) if isinstance(repairs_payload, dict) else 0,
            "timeline_events": len(timeline.get("events") or []) if isinstance(timeline, dict) else 0,
        },
    }


@app.get("/computer/world")
async def computer_world():
    """Latest shared WorldState snapshot (application, window, dialogs, state)."""
    store = _computer_task_store()
    tasks = sorted(
        store.list(),
        key=lambda t: str(t.get("updated_at") or ""),
        reverse=True,
    )
    if not tasks:
        return {"task_id": None, "task": None, "world": None}
    task_id = tasks[0]["task_id"]
    checkpoint = store.load(task_id)
    world = (checkpoint.world_state if checkpoint else {}) or {}
    return {"task_id": task_id, "task": tasks[0].get("task"), "world": world}


@app.get("/computer/plan/{task_id}")
async def computer_plan(task_id: str):
    """Task graph for the visual plan (nodes, goals, transitions, validation)."""
    store = _computer_task_store()
    checkpoint = store.load(task_id)
    if checkpoint is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    graph = store.load_graph(task_id)
    return {
        "task_id": task_id,
        "task": checkpoint.task,
        "graph": graph,
        "current_state": checkpoint.current_state,
        "completed": list(checkpoint.completed_states),
        "failed": list(checkpoint.failed_states),
    }


@app.get("/computer/repairs/{task_id}")
async def computer_repairs(task_id: str):
    """Repair history for one task (diagnoses + repair steps)."""
    store = _computer_task_store()
    checkpoint = store.load(task_id)
    if checkpoint is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    payload = _read_task_json(task_id, "repairs.json", {})
    repairs = payload.get("repairs") if isinstance(payload, dict) else []
    diagnoses = payload.get("diagnoses") if isinstance(payload, dict) else []
    successes = sum(1 for r in repairs if isinstance(r, dict) and bool(r.get("success", r.get("outcome") == "success")))
    return {
        "task_id": task_id,
        "task": checkpoint.task,
        "diagnoses": diagnoses,
        "repairs": repairs,
        "total": len(repairs),
        "successes": successes,
        "success_rate": round(successes / max(1, len(repairs)) * 100, 1),
    }


@app.get("/computer/repairs")
async def computer_repairs_all():
    """Aggregate repair history + success rate across all tasks."""
    return _repairs_aggregate(limit_recent=30)


@app.get("/computer/recording/{task_id}")
async def computer_recording(task_id: str):
    """Recording metadata: video, timeline, detected/analyzed/verified events."""
    if _computer_task_store().load(task_id) is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    info = _recording_info(task_id)
    timeline = _read_task_json(task_id, "timeline.json", {})
    events = _read_task_json(task_id, "events.json", [])
    verifications = _read_task_json(task_id, "verification.json", [])
    actions = _read_task_json(task_id, "actions.json", [])
    return {
        "task_id": task_id,
        "recording": info,
        "timeline_events": len(timeline.get("events") or []) if isinstance(timeline, dict) else 0,
        "detected_events": len(events) if isinstance(events, list) else 0,
        "verified": len(verifications) if isinstance(verifications, list) else 0,
        "actions": len(actions) if isinstance(actions, list) else 0,
    }


@app.get("/computer/recording/{task_id}/video")
async def computer_recording_video(task_id: str):
    """Stream the recorded screen video for a task."""
    info = _recording_info(task_id)
    if info is None:
        return JSONResponse({"success": False, "error": f"no recording for task '{task_id}'"}, status_code=404)
    return FileResponse(info["path"], media_type=info["media_type"] or "video/mp4")


@app.get("/computer/skills")
async def computer_skills():
    """Learned computer skills with reliability analytics."""
    from core.computer import ComputerSkillStore

    skills = ComputerSkillStore().list_skills()
    return {"skills": skills, "stats": _skill_stats(skills)}


@app.get("/computer/skills/{skill_name}")
async def computer_skill_detail(skill_name: str):
    """Full detail for one learned skill (procedure, failures, repairs, history)."""
    from core.computer import ComputerSkillStore

    skill = ComputerSkillStore().get_skill(skill_name)
    if skill is None:
        return JSONResponse({"success": False, "error": f"skill '{skill_name}' not found"}, status_code=404)
    data = skill.to_dict()
    history = []
    evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    runs = evidence.get("runs")
    if isinstance(runs, list):
        history = runs[-25:]
    data["history"] = history
    return data


# ---- Episode Memory Endpoints -----------------------------------------------

@app.get("/computer/episodes")
async def computer_episodes(limit: int = 50, outcome: str = "", tag: str = ""):
    """List recorded episodes (task recordings with full action traces)."""
    from core.computer import get_episode_store
    store = get_episode_store()
    return {
        "episodes": store.list(
            limit=limit,
            outcome=outcome if outcome else None,
            tag=tag if tag else None,
        ),
        "stats": store.stats(),
    }


@app.get("/computer/episodes/search")
async def computer_episodes_search(q: str = "", limit: int = 10):
    """Search episodes by task description."""
    from core.computer import get_episode_store
    store = get_episode_store()
    return {"results": store.search(q, limit=limit)}


@app.get("/computer/episodes/stats")
async def computer_episodes_stats():
    """Aggregate statistics across all episodes."""
    from core.computer import get_episode_store
    store = get_episode_store()
    return store.stats()


@app.get("/computer/episodes/{task_id}")
async def computer_episode_detail(task_id: str):
    """Get full detail for one recorded episode."""
    from core.computer import get_episode_store
    store = get_episode_store()
    episode = store.load(task_id)
    if episode is None:
        return JSONResponse({"success": False, "error": f"episode '{task_id}' not found"}, status_code=404)
    return episode.to_dict()


@app.delete("/computer/episodes/{task_id}")
async def computer_episode_delete(task_id: str):
    """Delete a recorded episode."""
    from core.computer import get_episode_store
    store = get_episode_store()
    if store.delete(task_id):
        return {"success": True, "deleted": task_id}
    return JSONResponse({"success": False, "error": f"episode '{task_id}' not found"}, status_code=404)


@app.post("/computer/episodes/clear")
async def computer_episodes_clear():
    """Delete all episodes."""
    from core.computer import get_episode_store
    store = get_episode_store()
    count = store.clear()
    return {"success": True, "deleted_count": count}


@app.get("/computer/episodes/recall")
async def computer_episodes_recall(task: str = ""):
    """Recall the most recent successful episode for a task description."""
    from core.computer import get_episode_store
    store = get_episode_store()
    trajectory = store.recall_trajectory(task)
    if trajectory is None:
        return {"success": False, "error": "no matching episode found"}
    return {"success": True, "trajectory": trajectory}


# ---- Benchmark Endpoints ----------------------------------------------------

@app.get("/computer/benchmark/tasks")
async def computer_benchmark_tasks(category: str = "", max_difficulty: int = 3):
    """List available benchmark tasks."""
    from core.computer.benchmark import list_tasks, get_categories
    if category:
        tasks = list_tasks(category=category, max_difficulty=max_difficulty)
    else:
        tasks = list_tasks(max_difficulty=max_difficulty)
    return {
        "tasks": [t.to_dict() for t in tasks],
        "count": len(tasks),
        "categories": {cat: len(tasks) for cat, tasks in get_categories().items()},
    }


@app.post("/computer/benchmark/run")
async def computer_benchmark_run(payload: Dict = None):
    """Run the benchmark and return results."""
    from core.computer.benchmark import run_benchmark
    payload = payload or {}
    result = run_benchmark(
        dry_run=bool(payload.get("dry_run", True)),
        max_tasks=int(payload.get("max_tasks", 0)),
        categories=payload.get("categories"),
        max_difficulty=int(payload.get("max_difficulty", 3)),
    )
    return result.to_dict()


@app.get("/computer/benchmark/task/{task_id}")
async def computer_benchmark_task(task_id: str):
    """Get details for a specific benchmark task."""
    from core.computer.benchmark import get_task
    task = get_task(task_id)
    if task is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    return task.to_dict()


@app.post("/computer/run")
async def computer_run(payload: Dict = None):
    """Start a new autonomous computer task in the background (dry-run by default
    so the dashboard never moves the mouse unless the user opts in)."""
    from core.computer.events import publish

    payload = payload or {}
    task = str(payload.get("task") or "").strip()
    if not task:
        return JSONResponse({"success": False, "error": "task is required"}, status_code=400)
    dry_run = bool(payload.get("dry_run", True))
    task_id = str(payload.get("task_id") or "").strip() or None

    def _run() -> None:
        try:
            from core.computer import ComputerAgent

            agent = ComputerAgent()
            agent.run(task, task_id=task_id, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            publish("task_interrupted", {"task_id": task_id or task, "reason": f"run failed: {exc}"})

    threading.Thread(target=_run, daemon=True).start()
    return {"success": True, "started": True, "task": task, "task_id": task_id,
            "dry_run": dry_run, "note": "task running in background; watch the Computer page"}


@app.post("/computer/resume/{task_id}")
async def computer_resume(task_id: str, payload: Dict = None):
    """Resume a paused/interrupted task in the background."""
    from core.computer import TaskStore
    from core.computer.events import publish

    if TaskStore().load(task_id) is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    dry_run = bool((payload or {}).get("dry_run", True))

    def _run() -> None:
        try:
            from core.computer import ComputerAgent

            ComputerAgent().resume(task_id, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            publish("task_interrupted", {"task_id": task_id, "reason": f"resume failed: {exc}"})

    threading.Thread(target=_run, daemon=True).start()
    return {"success": True, "started": True, "task_id": task_id, "dry_run": dry_run,
            "note": "resume running in background; watch the Computer page"}


@app.delete("/computer/task/{task_id}")
async def computer_task_delete(task_id: str):
    """Delete a persisted task and its artifacts."""
    store = _computer_task_store()
    if store.load(task_id) is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    directory = store.directory(task_id)
    try:
        shutil.rmtree(directory)
    except OSError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
    return {"success": True, "deleted": task_id}


@app.post("/computer/stop")
async def computer_stop(payload: Dict = None):
    """Emergency stop - halt all mouse/keyboard/autonomous control."""
    from core.computer import emergency_stop

    reason = (payload or {}).get("reason") or "emergency stop from dashboard"
    emergency_stop.halt(reason)
    return {"success": True, "halted": True, "reason": reason,
            "note": "Computer actions are halted. Release via POST /computer/release."}


@app.post("/computer/release")
async def computer_release():
    """Release the emergency stop latch (re-enables computer control)."""
    from core.computer import emergency_stop

    emergency_stop.release()
    return {"success": True, "halted": False}


# ---- Task Control Endpoints (Pause/Resume/Cancel) ---------------------------

@app.get("/computer/control/status")
async def computer_control_status():
    """Get overall task control status - all running/paused tasks and control state."""
    from core.computer.task_control import get_task_control

    control = get_task_control()
    return control.get_status()


@app.post("/computer/control/pause/{task_id}")
async def computer_control_pause(task_id: str, payload: Dict = None):
    """Request pause for a task at next safe boundary.
    
    The task will pause after completing its current action.
    Use /computer/control/resume/{task_id} to continue.
    """
    from core.computer.task_control import get_task_control

    control = get_task_control()
    reason = (payload or {}).get("reason", "")
    success = control.request_pause(task_id, reason)
    
    if success:
        return {
            "success": True,
            "task_id": task_id,
            "action": "pause_requested",
            "note": "Task will pause at next safe boundary",
            "reason": reason,
        }
    else:
        ctx = control.get_task_context(task_id)
        return {
            "success": False,
            "task_id": task_id,
            "error": f"Cannot pause: task is {ctx.control_state.value if ctx else 'not found'}",
        }


@app.post("/computer/control/resume/{task_id}")
async def computer_control_resume(task_id: str):
    """Resume a paused task from its saved state."""
    from core.computer.task_control import get_task_control

    control = get_task_control()
    success = control.resume(task_id)
    
    if success:
        ctx = control.get_task_context(task_id)
        return {
            "success": True,
            "task_id": task_id,
            "action": "resumed",
            "resume_count": ctx.resume_count if ctx else 0,
            "note": "Task resumed from paused state",
        }
    else:
        ctx = control.get_task_context(task_id)
        return {
            "success": False,
            "task_id": task_id,
            "error": f"Cannot resume: task is {ctx.control_state.value if ctx else 'not found'}",
        }


@app.post("/computer/control/cancel/{task_id}")
async def computer_control_cancel(task_id: str, payload: Dict = None):
    """Request cancellation of a task.
    
    The task will be marked as cancelled and terminated.
    This is different from pause - cancelled tasks cannot be resumed.
    """
    from core.computer.task_control import get_task_control

    control = get_task_control()
    reason = (payload or {}).get("reason", "")
    success = control.request_cancel(task_id, reason)
    
    if success:
        return {
            "success": True,
            "task_id": task_id,
            "action": "cancel_requested",
            "note": "Task cancellation requested",
            "reason": reason,
        }
    else:
        ctx = control.get_task_context(task_id)
        return {
            "success": False,
            "task_id": task_id,
            "error": f"Cannot cancel: task is {ctx.control_state.value if ctx else 'not found'}",
        }


@app.post("/computer/control/emergency-stop")
async def computer_emergency_stop(payload: Dict = None):
    """EMERGENCY STOP - immediately block all computer actions.
    
    This is the safety override that stops ALL computer control instantly.
    Use /computer/control/emergency-release to re-enable control.
    """
    from core.computer.task_control import get_task_control

    control = get_task_control()
    reason = (payload or {}).get("reason", "")
    control.emergency_stop(reason)
    
    return {
        "success": True,
        "action": "emergency_stop_activated",
        "reason": reason,
        "note": "All computer actions blocked. Use POST /computer/control/emergency-release to restore.",
    }


@app.post("/computer/control/emergency-release")
async def computer_emergency_release():
    """Release emergency stop - re-enable computer control.
    
    After calling this, computer actions can resume normally.
    """
    from core.computer.task_control import get_task_control

    control = get_task_control()
    success = control.release_emergency_stop()
    
    return {
        "success": success,
        "action": "emergency_stop_released" if success else "emergency_stop_not_active",
        "note": "Computer control restored" if success else "Emergency stop was not active",
    }


@app.get("/computer/control/{task_id}")
async def computer_control_task(task_id: str):
    """Get control context for a specific task."""
    from core.computer.task_control import get_task_control

    control = get_task_control()
    ctx = control.get_task_context(task_id)
    
    if ctx is None:
        return {"success": False, "error": f"Task '{task_id}' not found"}
    
    return {
        "success": True,
        **ctx.to_dict(),
    }


# ===========================================================================
# Phase C & D — remote control, resources, delegation, skill profiles, plugins
# ===========================================================================

@app.get("/computer/dashboard", response_class=HTMLResponse)
async def computer_dashboard_page():
    """Serve the live computer-agent dashboard (Phase A UI)."""
    html_path = Path(__file__).parent / "dashboard_computer.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store, max-age=0"})
    return HTMLResponse("<!DOCTYPE html><html><body><h1>dashboard_computer.html missing</h1></body></html>")


@app.get("/remote", response_class=HTMLResponse)
async def remote_control_page():
    """Mobile-friendly remote control page (Phase C remote Android/web)."""
    html_path = Path(__file__).parent / "remote.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store, max-age=0"})
    return HTMLResponse("<!DOCTYPE html><html><body><h1>remote.html missing</h1></body></html>")


@app.get("/computer/live-frame")
async def computer_live_frame():
    """Latest screen frame as JPEG (live screen for remote/dashboard panes)."""
    try:
        from core.integrations import _screen_recorder

        frame = _screen_recorder().latest()
    except Exception:  # noqa: BLE001
        frame = None
    if frame is None:
        return JSONResponse({"error": "no live frame available"}, status_code=404)
    data = frame.get("data")
    if not data:
        return JSONResponse({"error": "live frame empty"}, status_code=404)
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/computer/resources")
async def computer_resources():
    """Performance / resource telemetry for the dashboard (Phase D)."""
    from core.computer import get_resource_monitor

    return get_resource_monitor().sample()


@app.get("/computer/skills/{skill_name}/profile")
async def computer_skill_profile(skill_name: str):
    """Reliability profile for one skill (Phase C skill optimization)."""
    from core.computer import ComputerSkillStore

    profile = ComputerSkillStore().profile(skill_name)
    if profile is None:
        return JSONResponse({"success": False, "error": f"skill '{skill_name}' not found"}, status_code=404)
    return profile


@app.post("/computer/delegate")
async def computer_delegate(payload: Dict = None):
    """Delegate a task across persistent agents (Phase C multi-agent delegation).

    Accepts either a free-form ``task`` (decomposed heuristically) or a custom
    ``plan`` dict with ``units`` (WorkUnit records) for full control.
    """
    payload = payload or {}
    from core.computer import MultiAgentDelegator, DelegationPlan, WorkUnit

    delegator = MultiAgentDelegator()
    dry_run = bool(payload.get("dry_run", False))
    wait = not bool(payload.get("no_wait", False))
    if payload.get("plan") is not None:
        units = [
            WorkUnit(
                unit_id=str(u.get("unit_id")),
                role=str(u.get("role", "generic")),
                task=str(u.get("task", "")),
                depends_on=list(u.get("depends_on") or []),
                agent=u.get("agent"),
                payload=dict(u.get("payload") or {}),
            )
            for u in (payload["plan"].get("units") or [])
        ]
        plan = DelegationPlan(task=str(payload.get("task", "")), units=units)
    else:
        plan = delegator.plan(str(payload.get("task", "")))
    return delegator.execute(
        plan,
        wait=wait,
        timeout_per_unit=float(payload.get("timeout", 180.0)),
        dry_run=dry_run,
    )


@app.get("/computer/delegations")
async def computer_delegations():
    """Persisted delegation plans/results (Phase C)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "data" / "delegations"
    entries = []
    if root.is_dir():
        for path in sorted(root.glob("*.json"), reverse=True):
            try:
                entries.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                continue
    return {"delegations": entries[:50], "total": len(entries)}


# -- Remote / approval control (Phase C) --------------------------------------

@app.get("/remote/status")
async def remote_status():
    """Consolidated remote view: approval gate, control state, events, emergency."""
    from core.computer import remote_control

    return remote_control.snapshot()


@app.get("/remote/approvals")
async def remote_approvals():
    """Pending + recent approval prompts."""
    from core.computer import remote_approval

    return {"status": remote_approval.status(), "history": remote_approval.history(20)}


@app.post("/remote/approval/enable")
async def remote_approval_enable(payload: Dict = None):
    """Enable/disable the remote approval gate (per-action human approval)."""
    payload = payload or {}
    from core.computer import remote_approval
    from core.computer.permissions import RiskLevel

    try:
        risk = RiskLevel(str(payload.get("required_risk", "medium")).lower())
    except ValueError:
        risk = RiskLevel.MEDIUM
    return remote_approval.set_enabled(bool(payload.get("enabled", True)), required_risk=risk)


@app.post("/remote/approve")
async def remote_approve(payload: Dict = None):
    """Approve a pending action prompt (by prompt_id)."""
    payload = payload or {}
    from core.computer import remote_approval

    return remote_approval.approve(str(payload.get("prompt_id", "")), by=str(payload.get("by") or "remote"))


@app.post("/remote/reject")
async def remote_reject(payload: Dict = None):
    """Reject a pending action prompt."""
    payload = payload or {}
    from core.computer import remote_approval

    return remote_approval.reject(
        str(payload.get("prompt_id", "")),
        reason=str(payload.get("reason", "") or ""),
        by=str(payload.get("by") or "remote"),
    )


@app.post("/remote/control")
async def remote_control_action(payload: Dict = None):
    """Remote lifecycle control: pause / resume / cancel / emergency-stop / release."""
    payload = payload or {}
    from core.computer import remote_control

    action = str(payload.get("action", "")).lower()
    task_id = str(payload.get("task_id", "") or "")
    reason = str(payload.get("reason", "")) or f"remote {action}"
    if action == "pause":
        return remote_control.pause(task_id, reason)
    if action == "resume":
        return remote_control.resume(task_id)
    if action == "cancel":
        return remote_control.cancel(task_id, reason)
    if action in ("emergency-stop", "stop"):
        return remote_control.emergency_stop(reason)
    if action == "release":
        return remote_control.release()
    return JSONResponse({"success": False, "error": f"unknown remote action '{action}'"}, status_code=400)


# -- Dashboard live events + local Talking Mode speech -----------------------

@app.get("/dashboard/status")
async def dashboard_status():
    """Small, fast status aggregate used by the futuristic command deck."""
    from core.speech import speech_engine

    return {
        "gateway": "online",
        "version": "2.3-living-control",
        "timestamp": datetime.now().astimezone().isoformat(),
        "agents": len(AGENTS),
        "tasks": task_tracker.get_status(),
        "channels": get_channel_status(),
        "speech": speech_engine.status(),
        "local": True,
    }


@app.get("/speech/status")
async def speech_status():
    """Report the selected local TTS backend for Talking Mode."""
    from core.speech import speech_engine

    return speech_engine.status()


@app.post("/speech/synthesize")
async def speech_synthesize(payload: Dict):
    """Generate local WAV speech and return a gateway URL, never a host path."""
    from core.dashboard_events import dashboard_event_bus
    from core.speech import speech_engine

    text = str((payload or {}).get("text") or "")
    if not text.strip():
        return JSONResponse({"success": False, "error": "text required"}, status_code=400)
    result = await asyncio.to_thread(
        speech_engine.synthesize,
        text,
        (payload or {}).get("voice"),
        int((payload or {}).get("rate") or 165),
    )
    if not result.get("success"):
        return JSONResponse(result, status_code=503)
    result.pop("path", None)
    result["audio_url"] = f"/speech/audio/{result['audio_id']}"
    dashboard_event_bus.publish("speech_ready", {
        "audio_url": result["audio_url"],
        "audio_id": result["audio_id"],
        "backend": result.get("backend"),
        "estimated_duration": result.get("estimated_duration"),
        "session_id": (payload or {}).get("session_id"),
    })
    return result


@app.get("/speech/audio/{audio_id}")
async def speech_audio(audio_id: str):
    """Serve one generated local speech clip with traversal-safe lookup."""
    from core.speech import speech_engine

    path = speech_engine.audio_path(audio_id)
    if path is None:
        return JSONResponse({"success": False, "error": "audio not found"}, status_code=404)
    return FileResponse(
        str(path), media_type="audio/wav", filename=f"hermus-{audio_id[:8]}.wav",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.post("/speech/transcribe")
async def speech_transcribe(request: Request, model: str = "base", language: str = None):
    """Transcribe raw browser microphone audio with local faster-whisper.

    The browser sends the recorded Blob directly (not multipart), avoiding an
    additional python-multipart dependency.  Input is capped and deleted after
    transcription.
    """
    from core.dashboard_events import dashboard_event_bus
    from core.speech import speech_root
    from tools.voice import transcribe_audio

    body = await request.body()
    if not body:
        return JSONResponse({"success": False, "error": "audio body required"}, status_code=400)
    if len(body) > 25 * 1024 * 1024:
        return JSONResponse({"success": False, "error": "audio exceeds 25 MB limit"}, status_code=413)
    content_type = (request.headers.get("content-type") or "audio/webm").split(";", 1)[0].lower()
    suffix = {
        "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/wav": ".wav",
        "audio/x-wav": ".wav", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
    }.get(content_type, ".webm")
    input_dir = speech_root() / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    path = input_dir / f"mic_{os.urandom(8).hex()}{suffix}"
    path.write_bytes(body)
    try:
        result = await asyncio.to_thread(transcribe_audio, str(path), model, language)
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    if result.get("success"):
        result.pop("audio_path", None)
        dashboard_event_bus.publish("user_transcript", {"text": result.get("text", "")})
        return result
    return JSONResponse(result, status_code=503)


@app.websocket("/dashboard/events")
async def dashboard_events_ws(websocket: WebSocket):
    """Live chat/speech lifecycle stream used by fullscreen Talking Mode."""
    from core.dashboard_events import dashboard_event_bus

    expected = config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN")
    provided = websocket.query_params.get("token") or websocket.headers.get("X-Hermus-Token")
    if expected and provided != expected:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)

    def enqueue(event: Dict) -> None:
        def put() -> None:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
        loop.call_soon_threadsafe(put)

    unsubscribe = dashboard_event_bus.subscribe(enqueue)
    try:
        await websocket.send_json({"kind": "snapshot", "events": dashboard_event_bus.recent(100)})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                await websocket.send_json({"kind": "ping", "ts": datetime.now().astimezone().isoformat()})
    except Exception:
        pass
    finally:
        unsubscribe()
        try:
            await websocket.close()
        except Exception:
            pass


# -- Plugin / MCP ecosystem (Phase D) -----------------------------------------

@app.get("/plugins")
async def plugins_list():
    """Discover + list loaded plugins and their registered tools."""
    from core.plugins import plugin_registry

    plugin_registry.load_all()
    return {
        "plugins": plugin_registry.list(),
        "tools": plugin_registry.tools(),
        "logs": plugin_registry.logs(20),
    }


@app.post("/plugins/reload")
async def plugins_reload():
    """Reload all plugins (re-discovers modules and re-runs register())."""
    from core.plugins import plugin_registry

    return {"result": plugin_registry.load_all(reload=True), "tools": plugin_registry.tools()}


@app.post("/plugins/invoke")
async def plugins_invoke(payload: Dict = None):
    """Invoke a plugin-registered tool by name with keyword arguments."""
    payload = payload or {}
    from core.plugins import plugin_registry, PluginError

    name = str(payload.get("tool", ""))
    kwargs = dict(payload.get("args") or {})
    try:
        return {"success": True, "tool": name, "result": plugin_registry.invoke_tool(name, **kwargs)}
    except PluginError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=404)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.websocket("/computer/events")
async def computer_events_ws(websocket: WebSocket):
    """Live event stream: task_started, state_changed, action_*, verification,
    repair_*, task_completed, emergency_stop, world_changed, ..."""
    from core.computer.events import computer_event_bus

    expected = config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN")
    provided = websocket.query_params.get("token") or websocket.headers.get("X-Hermus-Token")
    if expected and provided != expected:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    def enqueue(event: Dict) -> None:
        def put() -> None:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
        loop.call_soon_threadsafe(put)

    unsubscribe = computer_event_bus.subscribe(enqueue)

    # Snapshot first, then stream. The cursor is taken *before* the snapshot is
    # rendered so nothing written in between is skipped; ids already shown in
    # the snapshot are suppressed once so they are not replayed as live.
    cursor = computer_event_bus.journal_offset()
    snapshot = computer_event_bus.recent(100)
    seen = deque(maxlen=4096)
    seen_ids = {str(event.get("id")) for event in snapshot if event.get("id")}
    seen.extend(seen_ids)

    def remember(event_id: str) -> bool:
        """Record an id; return True if it is new (not already delivered)."""
        if not event_id:
            return True
        if event_id in seen_ids:
            return False
        if len(seen) == seen.maxlen:
            seen_ids.discard(seen[0])
        seen.append(event_id)
        seen_ids.add(event_id)
        return True

    try:
        await websocket.send_json({"kind": "snapshot", "events": snapshot})
        last_send = time.monotonic()
        while True:
            # In-process events arrive instantly via the subscriber queue.
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.25)
                if remember(str(event.get("id") or "")):
                    await websocket.send_json(event)
                    last_send = time.monotonic()
            except asyncio.TimeoutError:
                pass

            # Cross-process events (e.g. `hermus computer run` in a terminal)
            # arrive through the journal. Tail only the bytes appended since
            # the last cursor, so cost tracks new activity - not journal size -
            # and old rows are never replayed as live activity.
            events, cursor = computer_event_bus.tail(cursor)
            for event in events:
                if remember(str(event.get("id") or "")):
                    await websocket.send_json(event)
                    last_send = time.monotonic()

            # Keepalive: idle connections send nothing, and reverse proxies
            # drop silent WebSockets (commonly after 30-60s). A periodic ping
            # keeps the live stream alive through them.
            now = time.monotonic()
            if now - last_send >= 20:
                await websocket.send_json({"kind": "ping", "ts": datetime.now().astimezone().isoformat()})
                last_send = now
    except Exception:
        pass
    finally:
        unsubscribe()
        try:
            await websocket.close()
        except Exception:
            pass


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
