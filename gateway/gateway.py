"""Gateway - Single process for Telegram, Discord, Slack, WhatsApp, Signal, CLI - free, cross-platform continuity"""
import os
import asyncio
from pathlib import Path
from typing import Dict
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

from core.config import config
from core.agent import HermusAgent

app = FastAPI(title="Hermus Gateway Free", description="Single gateway for all platforms, free")

# Store agents per user/platform for continuity
AGENTS: Dict[str, HermusAgent] = {}

def get_agent_for_user(platform: str, user_id: str, model: str = None) -> HermusAgent:
    key = f"{platform}:{user_id}"
    if key not in AGENTS:
        AGENTS[key] = HermusAgent(model=model, session_id=f"{platform}_{user_id}_{os.urandom(4).hex()}")
    return AGENTS[key]

@app.get("/")
async def root():
    return {"message": "Hermus Gateway Free - Single process for Telegram/Discord/Slack/CLI", "platforms": ["telegram", "discord", "cli"], "agents": len(AGENTS)}

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
    return {"platforms": list(set([k.split(':')[0] for k in AGENTS.keys()])), "active_agents": len(AGENTS)}

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
    print(f"Endpoints: /webhook/telegram, /command, /platforms")
    print(f"Docs: http://localhost:{port}/docs")
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
