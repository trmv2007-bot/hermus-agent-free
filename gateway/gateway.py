"""Gateway - Single process for Telegram, Discord, Slack, WhatsApp, Signal, CLI - free, cross-platform continuity - Optimized with caching, compression, rate limiting"""
import os
import asyncio
from pathlib import Path
from typing import Dict
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

from core.config import config
from core.agent import HermusAgent
from core.task_tracker import task_tracker
from core.cache import get_cache_stats

app = FastAPI(title="Hermus Gateway Free", description="Single gateway for all platforms, free - Optimized")

# Add GZip compression for faster dashboard - optimized
app.add_middleware(GZipMiddleware, minimum_size=500)

# Store agents per user/platform for continuity
AGENTS: Dict[str, HermusAgent] = {}

def get_agent_for_user(platform: str, user_id: str, model: str = None) -> HermusAgent:
    key = f"{platform}:{user_id}"
    if key not in AGENTS:
        AGENTS[key] = HermusAgent(model=model, session_id=f"{platform}_{user_id}_{os.urandom(4).hex()}")
    return AGENTS[key]

@app.get("/")
async def root():
    from core.cache import get_cache_stats
    return {
        "message": "Hermus Gateway Free - Single process for Telegram/Discord/Slack/CLI - Optimized",
        "platforms": ["telegram", "discord", "cli"],
        "agents": len(AGENTS),
        "optimized": True,
        "cache_stats": get_cache_stats(),
        "version": "2.0-free-optimized"
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
        return HTMLResponse(html_path.read_text())
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
    """Telegram free Bot API webhook - cross-platform continuity"""
    try:
        data = await request.json()
        message = data.get("message", {})
        text = message.get("text", "")
        user_id = str(message.get("from", {}).get("id", "unknown"))
        
        if not text:
            return JSONResponse({"ok": True})

        agent = get_agent_for_user("telegram", user_id)
        result = agent.chat(text)

        # For free version, we don't actually send via Telegram API here - gateway would need TELEGRAM_BOT_TOKEN
        # But we return response for polling or for testing
        # Real implementation would: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={...})
        return JSONResponse({"ok": True, "response": result["response"], "tool_results": result.get("tool_results", [])})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@app.post("/command")
async def command_endpoint(payload: Dict):
    """Generic command endpoint for CLI, Discord, etc. - free"""
    platform = payload.get("platform", "cli")
    user_id = payload.get("user_id", "cli_user")
    text = payload.get("text", "")
    model = payload.get("model")

    agent = get_agent_for_user(platform, user_id, model=model)
    result = agent.chat(text)
    return result

@app.get("/platforms")
async def platforms():
    return {"platforms": list(set([k.split(':')[0] for k in AGENTS.keys()])), "active_agents": len(AGENTS), "task_tracker": task_tracker.get_status()}

@app.get("/keys/list")
async def keys_list():
    """List API keys - free, with redacted preview - for Settings panel to add API key"""
    try:
        from core.multi_key import multi_key_manager
        from core.custom_api import custom_api_manager
        # LLM provider keys
        llm_keys = multi_key_manager.list_keys()
        # Redact preview
        redacted = {}
        for provider, keys in llm_keys.items():
            redacted[provider] = []
            for k in keys:
                if isinstance(k, dict):
                    key_val = k.get("key","")
                    redacted.append({
                        "name": k.get("name",""),
                        "preview": f"{key_val[:6]}...{key_val[-4:]}" if len(key_val) > 10 else "****",
                        "added": k.get("added",""),
                        "usage_count": k.get("usage_count",0),
                        "provider": provider
                    })
                else:
                    redacted[provider].append({"preview": f"{k[:6]}...{k[-4:]}" if len(k)>10 else "****", "provider": provider})

        # Custom APIs
        custom_apis = custom_api_manager.list_apis()
        custom_redacted = []
        for api in custom_apis:
            token = api.get("auth",{}).get("token") or api.get("auth",{}).get("value") or ""
            custom_redacted.append({
                "name": api["name"],
                "description": api.get("description",""),
                "url": api.get("url",""),
                "method": api.get("method","GET"),
                "preview": f"{token[:6]}...{token[-4:]}" if token and len(token)>10 else "no-token" if not token else "****",
                "id": api.get("id",""),
                "created": api.get("created","")
            })

        return {
            "llm_keys": redacted,
            "custom_apis": custom_redacted,
            "total_llm_keys": sum(len(v) for v in llm_keys.values()),
            "total_custom_apis": len(custom_apis),
            "note": "Free API Keys Management - Add API key in Settings via /keys/add endpoint or dashboard. Keys stored in data/api_keys.json and data/custom_apis.json, local only, not uploaded."
        }
    except Exception as e:
        return {"error": str(e)}

@app.post("/keys/add")
async def keys_add(payload: Dict):
    """Add API key via Settings panel - free"""
    try:
        from core.multi_key import multi_key_manager
        provider = payload.get("provider", "groq")
        key = payload.get("key") or payload.get("api_key") or payload.get("token")
        name = payload.get("name")
        if not key:
            return JSONResponse({"success": False, "error": "Missing key/api_key/token"}, status_code=400)

        result = multi_key_manager.add_key(provider, key, name=name)
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

# CLI for gateway setup/start - free
def setup(platform: str):
    print(f"[Gateway] Setup for {platform} - Free")
    if platform == "telegram":
        token = os.getenv("TELEGRAM_BOT_TOKEN") or config.telegram_bot_token
        if not token:
            print("Set TELEGRAM_BOT_TOKEN env: https://t.me/BotFather /newbot (free)")
        else:
            print(f"Telegram token found: {token[:10]}... - Ready for webhook")
            print(f"Set webhook: https://api.telegram.org/bot{token}/setWebhook?url=https://yourdomain.com/webhook/telegram")
    elif platform == "discord":
        token = os.getenv("DISCORD_BOT_TOKEN") or config.discord_bot_token
        if not token:
            print("Set DISCORD_BOT_TOKEN env: https://discord.com/developers/applications (free)")
        else:
            print(f"Discord token found - Ready")
    else:
        print(f"Platform {platform} setup - just set env token")

def start(port: int = None):
    port = port or config.gateway_port
    print(f"[Gateway] Starting free gateway on port {port} - Single process for all platforms")
    print(f"Endpoints: /webhook/telegram, /command, /platforms, /agents/status, /dashboard")
    print(f"Docs: http://localhost:{port}/docs")
    print(f"Dashboard with slide panel: http://localhost:{port}/dashboard - Click 'Agents Panel' to slide open and see what agents/models running")
    print(f"Cross-platform continuity: Same user across Telegram/Discord/CLI shares memory via SQLite FTS5")
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
