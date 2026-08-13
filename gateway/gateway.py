"""Gateway - Single process for Telegram, Discord, Slack, WhatsApp, Signal, CLI - free, cross-platform continuity - Optimized with caching, compression, rate limiting"""
import os
import asyncio
from pathlib import Path
from typing import Dict, Optional
from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse, HTMLResponse
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

# Store agents per user/platform for continuity
AGENTS: Dict[str, HermusAgent] = {}

def get_agent_for_user(platform: str, user_id: str, model: str = None, mode: str = "agent") -> HermusAgent:
    key = f"{platform}:{user_id}:{mode}"
    if key not in AGENTS:
        AGENTS[key] = HermusAgent(model=model, session_id=f"{platform}_{user_id}_{os.urandom(4).hex()}", mode=mode)
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


@app.get("/")
async def root():
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
        ],
        "version": "2.1-free-versatile"
    }

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
    """Generic command endpoint for CLI, Discord, etc. - free - now supports modes"""
    platform = payload.get("platform", "cli")
    user_id = payload.get("user_id", "cli_user")
    text = payload.get("text", "")
    model = payload.get("model")
    mode = payload.get("mode", "agent")

    agent = get_agent_for_user(platform, user_id, model=model, mode=mode)
    result = agent.chat(text)
    # Include mode in response
    result["mode"] = agent.mode.value
    result["mode_config"] = {"name": agent.mode_config.name, "description": agent.mode_config.description[:200]}
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
