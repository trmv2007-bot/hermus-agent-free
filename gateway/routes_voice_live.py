"""Push-to-talk and spoken replies for the companion room.

The room asks one question on load — what can this install actually do — and
then uses exactly three primitives: open a listen, feed it microphone bytes
while the person speaks, close it for the final transcript. Speech out mirrors
that: hand it a sentence, get a clip, and stop it the moment the person talks.

Every endpoint here degrades to a stated reason rather than a failure that
arrives after a person has already acted. That is the whole difference between a
disabled control with one sentence of explanation and a dead button.
"""

from __future__ import annotations

import asyncio
import threading

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_MAX_CHUNK_BYTES = 2 * 1024 * 1024
_warm_started = False


def _warm_once() -> None:
    """Load the models and the voice once, as the room opens.

    Everything here costs seconds — a Whisper model has to be read into memory,
    a Piper voice has to be decompressed — and every one of those seconds would
    otherwise land on the first press or the first spoken sentence. The room asks
    for this status while a person is still looking at it, which is exactly the
    quiet moment to spend them.
    """
    from core.speech import speech_engine
    from core.voice.listen import partial_model_name, warm_models

    warm_models((partial_model_name(),))
    from core.voice.listen import final_model_name

    warm_models((final_model_name(),))
    warm = getattr(speech_engine, "warm", None)
    if warm is not None:
        warm()


def _warm_in_background() -> None:
    global _warm_started
    if _warm_started:
        return
    _warm_started = True
    threading.Thread(target=_warm_once, name="hermus-voice-warm", daemon=True).start()


def _tts_status() -> dict:
    from core.speech import speech_engine

    try:
        status = speech_engine.status()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "backend": None, "reason": f"speech engine failed: {exc}", "fix": None}
    available = bool(status.get("available"))
    setup = status.get("setup")
    return {
        "available": available,
        "backend": status.get("backend"),
        "reason": None if available else "No local voice is installed, so answers stay on screen",
        "fix": None if available else setup,
    }


@router.get("/voice/live/status")
async def voice_live_status():
    """What the microphone and the speaker will actually do on this install."""
    from core.config import config
    from core.voice.listen import stt_status
    from core.voice.speak import speech_director

    _warm_in_background()
    stt = stt_status()
    tts = _tts_status()
    return {
        "enabled": bool(getattr(config, "voice_enabled", True)),
        "stt": stt,
        "tts": tts,
        "speaking": speech_director.speaking(),
        "truth": {
            "mic": None if stt["available"] else stt["fix"],
            "speech": None if tts["available"] else tts["fix"],
        },
    }


@router.post("/voice/live/start")
async def voice_live_start(payload: dict | None = None):
    """Open one utterance. The status told the room whether this would work."""
    from core.voice.listen import SpeechToTextUnavailableError, listen_manager, stt_status

    payload = payload or {}
    try:
        session = listen_manager.start(str(payload.get("session_id") or ""))
    except SpeechToTextUnavailableError as exc:
        status = stt_status()
        return JSONResponse(
            {"success": False, "stage": "start", "error": status.get("reason"), "fix": str(exc)},
            status_code=503,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"success": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500)
    return {"success": True, **session}


@router.post("/voice/live/chunk")
async def voice_live_chunk(request: Request):
    """Microphone bytes (16 kHz mono PCM16) for the utterance named in the header."""
    from core.voice.listen import listen_manager

    listen_id = request.headers.get("X-Hermus-Listen", "").strip()
    if not listen_id:
        return JSONResponse({"success": False, "error": "X-Hermus-Listen header required"}, status_code=400)
    body = await request.body()
    if not body:
        return JSONResponse({"success": False, "error": "audio body required"}, status_code=400)
    if len(body) > _MAX_CHUNK_BYTES:
        return JSONResponse({"success": False, "error": "chunk exceeds 2 MB"}, status_code=413)
    try:
        accepted = listen_manager.append(listen_id, body)
    except KeyError:
        return JSONResponse({"success": False, "error": "unknown listen session"}, status_code=404)

    partial = await asyncio.to_thread(listen_manager.partial, listen_id)
    return {"success": True, "accepted": accepted, **partial}


@router.post("/voice/live/stop")
async def voice_live_stop(payload: dict | None = None):
    """Close the utterance and return the transcript the turn is built from."""
    from core.voice.listen import listen_manager

    payload = payload or {}
    listen_id = str(payload.get("id") or "")
    try:
        result = await asyncio.to_thread(listen_manager.finish, listen_id, payload.get("language"))
    except KeyError:
        return JSONResponse({"success": False, "error": "unknown listen session"}, status_code=404)
    if result.get("silent"):
        return JSONResponse(
            {"success": False, "stage": "transcribe", "error": "no speech detected", **result},
            status_code=422,
        )
    return {"success": True, **result}


@router.post("/voice/live/cancel")
async def voice_live_cancel(payload: dict | None = None):
    """Throw the utterance away — the person pressed stop before speaking."""
    from core.voice.listen import listen_manager

    payload = payload or {}
    return {"success": listen_manager.cancel(str(payload.get("id") or ""))}


@router.post("/voice/speak")
async def voice_speak(payload: dict | None = None):
    """Turn one sentence of the answer into audio, or say why it cannot."""
    from core.voice.speak import speech_director

    payload = payload or {}
    text = str(payload.get("text") or "")
    if not text.strip():
        return JSONResponse({"success": False, "error": "text required"}, status_code=400)
    result = await asyncio.to_thread(
        speech_director.speak,
        text,
        session_id=str(payload.get("session_id") or ""),
        voice=payload.get("voice"),
    )
    if not result.get("spoken"):
        return JSONResponse({"success": False, "error": result.get("error"), **result}, status_code=503)
    return {"success": True, **result}


@router.post("/voice/speak/stop")
async def voice_speak_stop(payload: dict | None = None):
    """Cut speech off now. Anything already synthesized for it becomes stale."""
    from core.voice.speak import speech_director

    payload = payload or {}
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return JSONResponse({"success": False, "error": "session_id is required"}, status_code=400)
    generation = speech_director.interrupt(session_id)
    return {"success": True, "session_id": session_id, "output_generation": generation}


@router.post("/voice/live/playing")
async def voice_live_playing(payload: dict | None = None):
    """Report real playback state, so the character reflects actual sound."""
    from core.voice.speak import speech_director

    payload = payload or {}
    return speech_director.note_playing(
        str(payload.get("session_id") or ""),
        bool(payload.get("playing")),
        int(payload.get("duration_ms") or 0),
    )
