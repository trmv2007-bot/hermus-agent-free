"""Channel endpoints: Telegram webhook (queued or inline), channel status/start,
and direct Telegram sends."""
from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.config import config
from gateway.context import AGENTS, _agent_factory
from gateway.queue import job_queue as _job_queue

try:
    from gateway.channels import (
        get_channel_status,
        get_discord_token,
        get_telegram_token,
        handle_telegram_update,
        start_all_channels,
        telegram_send_message,
    )
except ImportError:  # executed as a plain module (python gateway/gateway.py)
    from channels import (  # type: ignore
        get_channel_status,
        get_discord_token,
        get_telegram_token,
        handle_telegram_update,
        start_all_channels,
        telegram_send_message,
    )

router = APIRouter()

# The inbound webhook lives on `router` and stays ungated (an external service
# cannot attach an auth header). The channel *control* actions live on
# `control_router`, which the gateway gates with the optional gateway token.
control_router = APIRouter()


@router.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Telegram webhook — acknowledged immediately, agent runs on the queue.

    Telegram retries (and eventually disables a webhook) when a handler is slow,
    and one long tool loop should not pin an HTTP worker. So: parse the update,
    enqueue a `channel.reply` job, return 202 with the job handle. Set
    HERMUS_WEBHOOK_SYNC=1 to restore the old inline behaviour.

    When the optional HERMUS_TELEGRAM_WEBHOOK_SECRET is configured (register the
    webhook with setWebhook's secret_token), the request must carry the matching
    X-Telegram-Bot-Api-Secret-Token header; otherwise the route stays open for
    setups that register a webhook without a secret.
    """
    from gateway.context import _token_matches

    expected = config.telegram_webhook_secret or os.getenv("HERMUS_TELEGRAM_WEBHOOK_SECRET")
    if expected:
        provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not _token_matches(provided, expected):
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"invalid body: {e}"}, status_code=400)

    sync_mode = os.getenv("HERMUS_WEBHOOK_SYNC", "0") not in ("0", "false", "False")
    if not sync_mode and _job_queue.enabled and _job_queue._started:
        try:
            msg = ((data or {}).get("message") or (data or {}).get("edited_message")
                   or (data or {}).get("channel_post") or {})
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            text = ""
            if isinstance(msg.get("text"), str):
                text = msg["text"]
            elif isinstance(msg.get("voice"), dict) or isinstance(msg.get("audio"), dict):
                text = ""  # voice notes are resolved inside the job handler
            if chat_id and str(text).strip():
                job = _job_queue.submit(
                    "channel.reply",
                    {"text": str(text), "platform": "telegram", "chat_id": chat_id,
                     "user_id": str(chat.get("id") or msg.get("from", {}).get("id") or "tg")},
                    session_key=f"telegram:{chat_id}",
                    dedupe_key=str(msg.get("message_id")) and f"tg:{chat_id}:{msg.get('message_id')}",
                )
                return JSONResponse({"ok": True, "queued": True, "job_id": job.id,
                                     "run_id": job.run_id, "events_url": f"/jobs/{job.id}/events"},
                                    status_code=202)
        except Exception as e:
            print(f"[Gateway] telegram queueing failed ({e}) — running inline")

    try:
        # Full handler: chat + sendMessage (+ voice transcribe)
        result = handle_telegram_update(data, agent_factory=_agent_factory)
        return JSONResponse(result if isinstance(result, dict) else {"ok": True, "result": result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@control_router.get("/channels/status")
async def channels_status():
    """Telegram/Discord channel runtime status"""
    return {
        "channels": get_channel_status(),
        "telegram_token_set": bool(get_telegram_token()),
        "discord_token_set": bool(get_discord_token()),
        "agents": len(AGENTS),
    }


@control_router.post("/channels/start")
async def channels_start(payload: dict = None):
    """Manually start Telegram polling + Discord bot"""
    payload = payload or {}
    mode = payload.get("telegram_mode") or getattr(config, "telegram_mode", "auto")
    started = start_all_channels(_agent_factory, telegram_mode=mode)
    return {"started": started, "status": get_channel_status()}


@control_router.post("/telegram/send")
async def telegram_send(payload: dict):
    """Send a Telegram message directly (ops / cron delivery)"""
    chat_id = payload.get("chat_id")
    text = payload.get("text", "")
    if not chat_id or not text:
        return JSONResponse({"ok": False, "error": "need chat_id and text"}, status_code=400)
    return telegram_send_message(chat_id, text)


