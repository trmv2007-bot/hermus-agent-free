"""
Real messaging channels - Telegram + Discord.
- Telegram: Bot API sendMessage + optional long-polling OR webhook reply
- Discord: discord.py bot listener (background thread/async)
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import traceback
from typing import Any, Callable, Dict, Optional

import requests

from core.config import config

# Optional: shared agent factory injected by gateway
_agent_factory: Optional[Callable] = None
_telegram_poller_thread: Optional[threading.Thread] = None
_telegram_poller_stop = threading.Event()
_discord_thread: Optional[threading.Thread] = None
_channel_status: Dict[str, Any] = {
    "telegram": {"running": False, "mode": None, "last_error": None, "updates": 0},
    "discord": {"running": False, "last_error": None, "messages": 0},
}


def set_agent_factory(factory: Callable):
    global _agent_factory
    _agent_factory = factory


def get_channel_status() -> Dict:
    return dict(_channel_status)


# --------------- Telegram ---------------

def get_telegram_token() -> Optional[str]:
    return os.getenv("TELEGRAM_BOT_TOKEN") or config.telegram_bot_token


def telegram_api(method: str, payload: Dict = None, token: str = None) -> Dict:
    token = token or get_telegram_token()
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set"}
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        resp = requests.post(url, json=payload or {}, timeout=60)
        data = resp.json()
        return data
    except Exception as e:
        return {"ok": False, "error": str(e)}


def telegram_send_message(
    chat_id: str | int,
    text: str,
    reply_to_message_id: int = None,
    parse_mode: str = None,
    token: str = None,
) -> Dict:
    """Send a message via Telegram Bot API (real send)."""
    if not text:
        return {"ok": False, "error": "empty text"}
    # Telegram limit ~4096 chars
    chunks = []
    t = text
    while t:
        chunks.append(t[:4000])
        t = t[4000:]
    last = None
    for i, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_to_message_id and i == 0:
            payload["reply_to_message_id"] = reply_to_message_id
        last = telegram_api("sendMessage", payload, token=token)
        if not last.get("ok"):
            return last
    return last or {"ok": False, "error": "no send"}


def telegram_send_chat_action(chat_id: str | int, action: str = "typing", token: str = None) -> Dict:
    return telegram_api("sendChatAction", {"chat_id": chat_id, "action": action}, token=token)


def handle_telegram_update(update: Dict, agent_factory: Callable = None) -> Dict:
    """
    Process one Telegram update dict.
    Replies via sendMessage. Supports text + voice (downloads + Whisper if available).
    """
    factory = agent_factory or _agent_factory
    message = update.get("message") or update.get("edited_message") or {}
    if not message:
        return {"ok": True, "skipped": "no message"}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    from_user = message.get("from") or {}
    user_id = str(from_user.get("id", chat_id or "unknown"))
    message_id = message.get("message_id")
    text = message.get("text") or message.get("caption") or ""

    # Voice / audio memo → transcribe
    if not text and (message.get("voice") or message.get("audio")):
        try:
            text = _telegram_transcribe_voice(message)
            if text:
                telegram_send_message(chat_id, f"🎤 Transcribed: {text[:500]}")
        except Exception as e:
            telegram_send_message(chat_id, f"Voice transcription failed: {e}")
            return {"ok": False, "error": str(e)}

    if not text:
        return {"ok": True, "skipped": "no text"}

    # Slash commands handled lightly
    if text.strip() in ("/start", "/help"):
        reply = (
            "☤ Hermus Agent Free\n"
            "Send any message and I'll help.\n"
            "Commands: /new /status /help\n"
            "Voice memos are transcribed locally with Whisper when available."
        )
        return telegram_send_message(chat_id, reply, reply_to_message_id=message_id)

    if not factory:
        return {"ok": False, "error": "agent factory not set"}

    try:
        telegram_send_chat_action(chat_id, "typing")
        agent = factory("telegram", user_id)
        if text.strip().lower() in ("/new", "/reset"):
            agent.new_session()
            return telegram_send_message(chat_id, "✨ New session started.", reply_to_message_id=message_id)
        if text.strip().lower() == "/status":
            from core.task_tracker import task_tracker

            st = task_tracker.get_status()
            msg = (
                f"Agents: {st.get('active_agents_count', 0)} | "
                f"Tasks: {st.get('active_tasks_count', 0)} | "
                f"Models: {', '.join(st.get('models_in_use') or []) or 'n/a'}"
            )
            return telegram_send_message(chat_id, msg, reply_to_message_id=message_id)

        result = agent.chat(text)
        reply = result.get("response") or "(empty response)"
        tools_used = result.get("tool_results") or []
        if tools_used:
            names = ", ".join(tr.get("tool", "?") for tr in tools_used[:8])
            reply = f"{reply}\n\n🔧 tools: {names}"
        steps = result.get("steps")
        if steps and steps > 1:
            reply = f"{reply}\n\n🔁 steps: {steps}"
        send_result = telegram_send_message(chat_id, reply, reply_to_message_id=message_id)
        _channel_status["telegram"]["updates"] = _channel_status["telegram"].get("updates", 0) + 1
        return {
            "ok": True,
            "send": send_result,
            "user_id": user_id,
            "response_preview": reply[:200],
        }
    except Exception as e:
        err = f"Hermus error: {e}"
        _channel_status["telegram"]["last_error"] = err
        try:
            telegram_send_message(chat_id, err[:1000])
        except Exception:
            pass
        return {"ok": False, "error": err, "trace": traceback.format_exc()[-500:]}


def _telegram_transcribe_voice(message: Dict) -> str:
    token = get_telegram_token()
    voice = message.get("voice") or message.get("audio") or {}
    file_id = voice.get("file_id")
    if not file_id or not token:
        return ""
    # getFile
    meta = telegram_api("getFile", {"file_id": file_id}, token=token)
    if not meta.get("ok"):
        raise RuntimeError(meta.get("description") or meta.get("error") or "getFile failed")
    file_path = meta["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    raw = requests.get(url, timeout=60)
    raw.raise_for_status()
    from pathlib import Path

    tmp = Path(config.resolve_path("data/tmp"))
    tmp.mkdir(parents=True, exist_ok=True)
    ext = Path(file_path).suffix or ".ogg"
    local = tmp / f"tg_voice_{file_id}{ext}"
    local.write_bytes(raw.content)
    from tools.voice import transcribe_audio

    result = transcribe_audio(str(local))
    return (result.get("text") or result.get("transcription") or "").strip()


def start_telegram_polling(agent_factory: Callable = None, offset_file: str = None):
    """Background long-polling loop (no public URL needed)."""
    global _telegram_poller_thread
    factory = agent_factory or _agent_factory
    if not get_telegram_token():
        _channel_status["telegram"]["last_error"] = "TELEGRAM_BOT_TOKEN not set"
        print("[Telegram] No TELEGRAM_BOT_TOKEN - polling not started")
        return False

    if _telegram_poller_thread and _telegram_poller_thread.is_alive():
        print("[Telegram] Poller already running")
        return True

    _telegram_poller_stop.clear()
    offset_path = config.resolve_path(offset_file or "data/telegram_offset.txt")
    offset_path.parent.mkdir(parents=True, exist_ok=True)

    def loop():
        _channel_status["telegram"]["running"] = True
        _channel_status["telegram"]["mode"] = "polling"
        offset = 0
        if offset_path.exists():
            try:
                offset = int(offset_path.read_text().strip() or "0")
            except Exception:
                offset = 0
        # Drop webhook so polling works
        telegram_api("deleteWebhook", {"drop_pending_updates": False})
        print("[Telegram] Long-polling started")
        while not _telegram_poller_stop.is_set():
            try:
                data = telegram_api(
                    "getUpdates",
                    {"timeout": 25, "offset": offset, "allowed_updates": ["message", "edited_message"]},
                )
                if not data.get("ok"):
                    _channel_status["telegram"]["last_error"] = str(data)
                    time.sleep(3)
                    continue
                for upd in data.get("result") or []:
                    offset = max(offset, int(upd.get("update_id", 0)) + 1)
                    try:
                        offset_path.write_text(str(offset))
                    except Exception:
                        pass
                    handle_telegram_update(upd, agent_factory=factory)
            except Exception as e:
                _channel_status["telegram"]["last_error"] = str(e)
                time.sleep(3)
        _channel_status["telegram"]["running"] = False
        print("[Telegram] Poller stopped")

    _telegram_poller_thread = threading.Thread(target=loop, name="telegram-poller", daemon=True)
    _telegram_poller_thread.start()
    return True


def stop_telegram_polling():
    _telegram_poller_stop.set()


# --------------- Discord ---------------

def get_discord_token() -> Optional[str]:
    return os.getenv("DISCORD_BOT_TOKEN") or config.discord_bot_token


def start_discord_bot(agent_factory: Callable = None):
    """Start discord.py bot in a background thread with its own event loop."""
    global _discord_thread
    factory = agent_factory or _agent_factory
    token = get_discord_token()
    if not token:
        _channel_status["discord"]["last_error"] = "DISCORD_BOT_TOKEN not set"
        print("[Discord] No DISCORD_BOT_TOKEN - bot not started")
        return False

    if _discord_thread and _discord_thread.is_alive():
        print("[Discord] Bot already running")
        return True

    try:
        import discord
        from discord.ext import commands
    except ImportError:
        _channel_status["discord"]["last_error"] = "discord.py not installed - pip install discord.py"
        print("[Discord] discord.py missing")
        return False

    def runner():
        intents = discord.Intents.default()
        intents.message_content = True
        bot = commands.Bot(command_prefix="!", intents=intents)

        @bot.event
        async def on_ready():
            _channel_status["discord"]["running"] = True
            print(f"[Discord] Logged in as {bot.user}")

        @bot.event
        async def on_message(message):
            if message.author.bot:
                return
            # Respond on mention or DM
            is_dm = isinstance(message.channel, discord.DMChannel)
            mentioned = bot.user and bot.user.mentioned_in(message)
            content = message.content or ""
            if bot.user:
                content = content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
            if not (is_dm or mentioned):
                await bot.process_commands(message)
                return
            if not content:
                await message.channel.send("Send a message after mentioning me, or DM me.")
                return
            if not factory:
                await message.channel.send("Agent factory not configured.")
                return
            user_id = str(message.author.id)
            try:
                async with message.channel.typing():
                    # Run sync agent.chat in executor
                    loop = asyncio.get_event_loop()

                    def _chat():
                        agent = factory("discord", user_id)
                        if content.lower() in ("/new", "/reset"):
                            agent.new_session()
                            return {"response": "✨ New session started."}
                        return agent.chat(content)

                    result = await loop.run_in_executor(None, _chat)
                    reply = result.get("response") or "(empty)"
                    tools_used = result.get("tool_results") or []
                    if tools_used:
                        names = ", ".join(tr.get("tool", "?") for tr in tools_used[:6])
                        reply = f"{reply}\n\n🔧 tools: {names}"
                    # Discord 2000 char limit
                    for i in range(0, len(reply), 1900):
                        await message.channel.send(reply[i : i + 1900])
                    _channel_status["discord"]["messages"] = _channel_status["discord"].get("messages", 0) + 1
            except Exception as e:
                _channel_status["discord"]["last_error"] = str(e)
                await message.channel.send(f"Error: {e}"[:1500])

        try:
            bot.run(token)
        except Exception as e:
            _channel_status["discord"]["last_error"] = str(e)
            _channel_status["discord"]["running"] = False
            print(f"[Discord] Bot crashed: {e}")

    _discord_thread = threading.Thread(target=runner, name="discord-bot", daemon=True)
    _discord_thread.start()
    return True


def start_all_channels(agent_factory: Callable, telegram_mode: str = "auto"):
    """
    telegram_mode:
      - polling: always long-poll
      - webhook: don't poll (assume webhook configured)
      - auto: poll if token set (dev-friendly)
    """
    set_agent_factory(agent_factory)
    started = {}
    if get_telegram_token():
        if telegram_mode in ("polling", "auto"):
            started["telegram"] = start_telegram_polling(agent_factory)
        else:
            started["telegram"] = "webhook"
            _channel_status["telegram"]["mode"] = "webhook"
    else:
        started["telegram"] = False
    if get_discord_token():
        started["discord"] = start_discord_bot(agent_factory)
    else:
        started["discord"] = False
    return started
