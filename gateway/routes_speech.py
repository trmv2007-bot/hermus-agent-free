"""Talking Mode endpoints: dashboard status aggregate, local TTS synthesize/
audio/transcribe, and the dashboard lifecycle WebSocket."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse

from core.config import config
from core.task_tracker import task_tracker
from gateway.context import AGENTS, _token_matches

try:
    from gateway.channels import get_channel_status
except ImportError:  # executed as a plain module (python gateway/gateway.py)
    from channels import get_channel_status  # type: ignore

router = APIRouter()


@router.get("/dashboard/status")
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


@router.get("/speech/status")
async def speech_status():
    """Report the selected local TTS backend for Talking Mode."""
    from core.speech import speech_engine

    return speech_engine.status()


@router.post("/speech/synthesize")
async def speech_synthesize(payload: dict):
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


@router.get("/speech/audio/{audio_id}")
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


@router.post("/speech/transcribe")
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


@router.websocket("/dashboard/events")
async def dashboard_events_ws(websocket: WebSocket):
    """Live chat/speech lifecycle stream used by fullscreen Talking Mode."""
    from core.dashboard_events import dashboard_event_bus

    expected = config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN")
    provided = websocket.query_params.get("token") or websocket.headers.get("X-Hermus-Token")
    if expected and not _token_matches(provided, expected):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)

    def enqueue(event: dict) -> None:
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

