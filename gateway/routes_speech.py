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

# The dashboard/events WebSocket self-authenticates (1028-close token check); it
# lives on its own router so the control-plane HTTP router can be gated by the
# optional gateway token without running a Request dependency on a WebSocket.
ws_router = APIRouter()


@router.get("/dashboard/status")
async def dashboard_status():
    """Small, fast status aggregate used by the futuristic command deck."""
    from core.avatar import get_avatar_service
    from core.presence import get_presence
    from core.speech import speech_engine
    from tools.voice import voice_available_models

    return {
        "gateway": "online",
        "version": "2.3-living-control",
        "timestamp": datetime.now().astimezone().isoformat(),
        "agents": len(AGENTS),
        "tasks": task_tracker.get_status(),
        "channels": get_channel_status(),
        "speech": speech_engine.status(),
        "transcription": voice_available_models(),
        "avatar": get_avatar_service().status(),
        "presence": get_presence().current(),
        "local": True,
    }


@router.get("/speech/status")
async def speech_status():
    """Report the selected local TTS backend for Talking Mode."""
    from core.speech import speech_engine

    return speech_engine.status()


@router.get("/speech/transcription/status")
async def speech_transcription_status():
    """Which backend owns voice commands: the NPU engine, or faster-whisper.

    The dashboard's Voice panel shows this so a user on an Intel NPU box can
    see that transcription runs cool on the NPU while the GPU stays free.
    """
    from tools.voice import local_engine_status, FASTER_WHISPER_AVAILABLE, voice_available_models

    engine = local_engine_status()
    catalog = voice_available_models()
    return {
        "backend": "nollama" if engine.get("ready") else "faster-whisper",
        "local_engine": engine,
        "faster_whisper_installed": FASTER_WHISPER_AVAILABLE,
        "discovered_models": catalog.get("discovered_models") or [],
        "discovered_count": int(catalog.get("discovered_count") or 0),
        "search_dirs": catalog.get("search_dirs") or [],
        "postprocess": catalog.get("postprocess") or {},
        "note": (
            "Voice commands transcribe on the NPU; the GPU is left free for the agent."
            if engine.get("ready")
            else "Voice commands transcribe on the CPU (faster-whisper). "
            "Download a Whisper model in the Local AI Engine pane to move it to the NPU."
        ),
    }


@router.get("/speech/transcription/models")
async def speech_transcription_models():
    """Discover local STT model assets in conventional Handy-style directories."""
    from tools.voice import voice_available_models

    return voice_available_models()


@router.post("/speech/synthesize")
async def speech_synthesize(payload: dict):
    """Generate local WAV speech and return a gateway URL, never a host path."""
    from core.dashboard_events import dashboard_event_bus
    from core.speech import speech_engine

    payload = payload or {}
    text = str(payload.get("text") or "")
    if not text.strip():
        return JSONResponse({"success": False, "error": "text required"}, status_code=400)
    result = await asyncio.to_thread(
        speech_engine.synthesize,
        text,
        payload.get("voice"),
        int(payload.get("rate") or 165),
        backend=payload.get("backend"),
        language=payload.get("language"),
        ref_audio=payload.get("ref_audio") or payload.get("reference_audio"),
        ref_text=payload.get("ref_text") or payload.get("reference_text"),
        instruct=payload.get("instruct"),
        duration=payload.get("duration"),
        speed=payload.get("speed"),
        prompt_id=payload.get("prompt_id"),
        create_prompt_id=payload.get("create_prompt_id"),
        normalize_text=bool(payload.get("normalize_text", False)),
    )
    if not result.get("success"):
        return JSONResponse(result, status_code=503)
    result.pop("path", None)
    result["audio_url"] = f"/speech/audio/{result['audio_id']}"
    try:
        from core.presence import get_presence

        get_presence().record_moment(
            "speech_generated", "Generated a local spoken response",
            session_id=str(payload.get("session_id") or ""),
            user_id=str(payload.get("user_id") or "default"),
            metadata={"backend": result.get("backend"), "audio_id": result.get("audio_id")},
            emit=False,
        )
    except Exception:
        pass
    dashboard_event_bus.publish("speech_ready", {
        "audio_url": result["audio_url"],
        "audio_id": result["audio_id"],
        "backend": result.get("backend"),
        "estimated_duration": result.get("estimated_duration"),
        "session_id": payload.get("session_id"),
        "prompt_id": result.get("prompt_id"),
        "voice_clone": result.get("voice_clone"),
        "voice_design": result.get("voice_design"),
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
async def speech_transcribe(
    request: Request,
    model: str = "base",
    language: str = None,
    remember: bool = False,
    session_id: str = "",
    project: str = "",
    normalize: bool = True,
    strip_fillers: bool = False,
):
    """Transcribe raw browser microphone audio with local faster-whisper.

    The browser sends the recorded Blob directly (not multipart), avoiding an
    additional python-multipart dependency. Input is capped and deleted after
    transcription. Successful transcripts can optionally be written into the
    canonical MemoryFacade session history.
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
        result = await asyncio.to_thread(
            transcribe_audio,
            str(path),
            model,
            language,
            normalize=normalize,
            strip_fillers=strip_fillers,
            remember=remember,
            session_id=session_id,
            project=project,
        )
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    if result.get("success"):
        result.pop("audio_path", None)
        dashboard_event_bus.publish("user_transcript", {
            "text": result.get("text", ""),
            "session_id": session_id,
            "remembered": bool((result.get("memory") or {}).get("remembered")),
        })
        return result
    return JSONResponse(result, status_code=503)


@router.get("/speech/avatar/status")
async def speech_avatar_status(probe: bool = False):
    """Status of the optional local talking-avatar connector."""
    from core.avatar import get_avatar_service

    return get_avatar_service().status(probe=probe)


@router.post("/speech/avatar/prepare-voice")
async def speech_avatar_prepare_voice(payload: dict):
    """Create a reusable local avatar voice profile from reference audio."""
    from core.avatar import get_avatar_service
    from core.dashboard_events import dashboard_event_bus

    payload = payload or {}
    result = await asyncio.to_thread(
        get_avatar_service().prepare_voice,
        str(payload.get("reference_audio") or payload.get("ref_audio") or ""),
        lang=str(payload.get("lang") or payload.get("language") or "en"),
        fmt=str(payload.get("format") or ""),
    )
    if result.get("success"):
        profile = result.get("voice_profile") or {}
        try:
            from core.presence import get_presence

            get_presence().record_moment(
                "avatar_voice_prepared", "Prepared an optional talking-avatar voice profile",
                user_id=str(payload.get("user_id") or "default"),
                metadata={"voice_profile_id": profile.get("voice_profile_id"), "lang": profile.get("lang")},
                emit=False,
            )
        except Exception:
            pass
        dashboard_event_bus.publish("avatar_voice_prepared", {
            "voice_profile_id": profile.get("voice_profile_id"),
            "lang": profile.get("lang"),
            "reference_audio": profile.get("reference_audio"),
        })
        return result
    return JSONResponse(result, status_code=503)


@router.post("/speech/avatar/render")
async def speech_avatar_render(payload: dict):
    """Submit a talking-avatar render via the local HeyGem-style connector."""
    from core.avatar import get_avatar_service
    from core.dashboard_events import dashboard_event_bus

    payload = payload or {}
    result = await asyncio.to_thread(
        get_avatar_service().render_from_text,
        str(payload.get("text") or ""),
        str(payload.get("avatar_video_path") or payload.get("video_path") or ""),
        voice_profile_id=str(payload.get("voice_profile_id") or ""),
        reference_audio=str(payload.get("reference_audio") or payload.get("ref_audio") or ""),
        reference_text=str(payload.get("reference_text") or payload.get("ref_text") or ""),
        lang=str(payload.get("lang") or payload.get("language") or "en"),
        code=str(payload.get("code") or ""),
    )
    if result.get("success"):
        try:
            from core.presence import get_presence

            get_presence().record_moment(
                "avatar_render_submitted", "Submitted an optional talking-avatar render",
                user_id=str(payload.get("user_id") or "default"),
                metadata={"code": result.get("code"), "backend": result.get("backend")},
                emit=False,
            )
        except Exception:
            pass
        dashboard_event_bus.publish("avatar_render_submitted", {
            "code": result.get("code"),
            "backend": result.get("backend"),
            "voice_profile_id": (result.get("audio") or {}).get("voice_profile_id"),
        })
        return result
    return JSONResponse(result, status_code=503)


@router.get("/speech/avatar/jobs/{code}")
async def speech_avatar_job_status(code: str):
    """Query a submitted local avatar render by task code."""
    from core.avatar import get_avatar_service

    result = await asyncio.to_thread(get_avatar_service().query_video, code)
    if result.get("success") or result.get("terminal"):
        return result
    return JSONResponse(result, status_code=503)


@ws_router.websocket("/dashboard/events")
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

