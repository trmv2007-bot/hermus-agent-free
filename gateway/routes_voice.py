"""Voice-first (Jarvis) endpoints: speak first, work in the background.

The problem this solves is perceived latency. A normal agent turn ships the whole
tool catalog (~20K prompt tokens) and takes seconds to tens of seconds before
anything comes back. In a typed dashboard that is tolerable; in a voice
conversation it is silence, and silence reads as failure.

So ``POST /voice/command`` deliberately splits the two:

1. transcribe the caller's microphone audio locally,
2. synthesize and return a short spoken acknowledgment *immediately* — by
   default from a canned phrase pool, so no model call happens at all,
3. enqueue the real work as a ``voice.reply`` job and hand back a 202 with the
   job handle.

The client starts playing the acknowledgment while the job runs, streams
progress over ``/jobs/{id}/events``, and speaks the answer when it lands. The
acknowledgment costs roughly one local TTS pass instead of a full model turn.

Why not just call the model with a smaller prompt? Because the acknowledgment
does not need a model at all. "On it." carries the same information regardless
of who wrote it, and the canned path removes model latency and the 20K-token
prefill from the critical path entirely. Set ``HERMUS_VOICE_ACK_MODE=llm`` if
you want a personalised acknowledgment and are willing to pay for it.
"""
from __future__ import annotations

import asyncio
import os
import random
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.config import config

try:
    from gateway.queue import job_queue as _job_queue
except ImportError:  # executed as a plain module (python gateway/gateway.py)
    from queue import job_queue as _job_queue  # type: ignore

router = APIRouter()

_DEFAULT_ACK = "On it."
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_SUFFIXES = {
    "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/wav": ".wav",
    "audio/x-wav": ".wav", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _ack_pool() -> list[str]:
    phrases = [p.strip() for p in str(config.voice_ack_phrases or "").split("|")]
    return [p for p in phrases if p] or [_DEFAULT_ACK]


def pick_ack(transcript: str = "") -> str:
    """Choose the acknowledgment to speak.

    Canned mode never touches a model, which is the whole point: it keeps the
    acknowledgment off the ~20K-token critical path.
    """
    pool = _ack_pool()
    return random.choice(pool)


def synthesize_speech(text: str) -> dict:
    """Render text to a local WAV clip. Synchronous — safe inside job workers.

    Never raises: a TTS failure degrades to ``spoken: False`` so the client can
    fall back to the browser's own speech synthesis instead of erroring out.
    """
    text = str(text or "").strip()
    if not text:
        return {"spoken": False, "text": "", "audio_url": None, "error": "empty text"}
    try:
        from core.speech import speech_engine

        result = speech_engine.synthesize(text)
    except Exception as exc:
        return {"spoken": False, "text": text, "audio_url": None,
                "error": f"{type(exc).__name__}: {exc}"[:200]}
    if not isinstance(result, dict) or not result.get("success"):
        reason = (result or {}).get("error") if isinstance(result, dict) else "speech engine returned no result"
        return {"spoken": False, "text": text, "audio_url": None, "error": str(reason)[:200]}
    return {
        "spoken": True,
        "text": text,
        "audio_url": f"/speech/audio/{result['audio_id']}",
        "backend": result.get("backend"),
        "estimated_duration": result.get("estimated_duration"),
    }


async def _synthesize_async(text: str) -> dict:
    return await asyncio.to_thread(synthesize_speech, text)


async def _make_ack(transcript: str) -> dict:
    """Build the acknowledgment payload returned alongside the 202."""
    mode = str(config.voice_ack_mode or "canned").lower()
    if mode == "off":
        return {"mode": "off", "spoken": False, "text": "", "audio_url": None}
    if mode == "llm":
        # Tools-free on purpose: a normal agent call carries the full tool
        # catalog (~20K tokens). An acknowledgment does not need tools, and
        # dropping them cuts the prompt by more than 10x.
        text = await asyncio.to_thread(_llm_ack, transcript)
        if not text:
            text = pick_ack(transcript)  # model unavailable → stay fast, stay audible
        clip = await _synthesize_async(text)
        return {"mode": "llm", **clip}
    text = pick_ack(transcript)
    clip = await _synthesize_async(text)
    return {"mode": "canned", **clip}


def _llm_ack(transcript: str) -> str:
    """One short, tools-free model call for a personalised acknowledgment."""
    try:
        from gateway.context import _agent_factory

        # The shared factory is per-platform/user; using it without those
        # arguments silently forced every LLM acknowledgement into the fallback
        # canned phrase.
        agent = _agent_factory("voice", "default", mode="chat")
        llm = getattr(agent, "llm", None)
        if llm is None:
            return ""
        response = llm.chat([
            {"role": "system", "content": (
                "You are a voice assistant acknowledging a spoken request. Reply with ONE "
                "short spoken sentence (max 12 words) confirming you are starting the work. "
                "No preamble, no markdown, no tool calls."
            )},
            {"role": "user", "content": str(transcript or "")[:500]},
        ], tools=None)
        return str(getattr(response, "content", "") or "").strip()[:200]
    except Exception:
        return ""


async def _transcribe_body(body: bytes, content_type: str, *, model: str,
                           language: str | None, session_id: str) -> dict:
    """Write the browser blob to a temp file and run local STT on it."""
    from core.speech import speech_root
    from tools.voice import transcribe_audio

    suffix = _SUFFIXES.get((content_type or "audio/webm").split(";", 1)[0].lower(), ".webm")
    input_dir = speech_root() / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    path = input_dir / f"voice_{os.urandom(8).hex()}{suffix}"
    path.write_bytes(body)
    try:
        return await asyncio.to_thread(
            transcribe_audio, str(path), model or config.voice_stt_model, language,
            normalize=True, strip_fillers=False, remember=False,
            session_id=session_id or "", project="",
        )
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def _enqueue(payload: dict, session_key: str, dedupe_key: str = "") -> dict | None:
    """Queue a voice.reply job. Returns the job handle, or None if unavailable."""
    try:
        job = _job_queue.submit(
            "voice.reply", payload, session_key=session_key, dedupe_key=dedupe_key,
        )
        return {"job_id": job.id, "run_id": job.run_id,
                "status_url": f"/jobs/{job.id}",
                "result_url": f"/jobs/{job.id}/result",
                "events_url": f"/jobs/{job.id}/events",
                "stream_url": f"/stream/run/{job.run_id}"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


def _run_inline(payload: dict) -> dict:
    """Fallback when the queue is unavailable: run now, return the answer inline."""
    try:
        from core.runtime import execute as runtime_execute

        result = runtime_execute(payload)
        answer = str(result.get("response") or result.get("error") or "")
        speech = synthesize_speech(answer[:config.voice_answer_max_chars]) \
            if answer and config.voice_speak_answer else {"spoken": False, "audio_url": None}
        return {"answer": answer[:4000], "speech": speech, "inline": True}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:300], "inline": True}


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #
@router.get("/voice/status")
async def voice_status():
    """What voice-first mode will actually do on this install."""
    from core.speech import speech_engine

    tts = {}
    try:
        tts = speech_engine.status() or {}
    except Exception as exc:
        tts = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    stt = {}
    try:
        from tools.voice import voice_available_models

        stt = voice_available_models() or {}
    except Exception as exc:
        stt = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    from core.presence import get_presence

    return {
        "enabled": bool(config.voice_enabled),
        "ack_mode": str(config.voice_ack_mode or "canned"),
        "ack_phrases": _ack_pool(),
        "speak_answer": bool(config.voice_speak_answer),
        "answer_max_chars": int(config.voice_answer_max_chars),
        "stt_model": str(config.voice_stt_model),
        "handsfree": {
            "enabled": bool(config.voice_handsfree),
            "wake_word": str(config.voice_wake_word or ""),
            "wake_aliases": list(getattr(config, "voice_wake_aliases", []) or []),
            "wake_required": bool(config.voice_wake_required),
            "silence_ms": int(config.voice_silence_ms),
            "speech_ms": int(config.voice_speech_ms),
            "min_utterance_ms": int(config.voice_min_utterance_ms),
            "max_utterance_ms": int(config.voice_max_utterance_ms),
            "barge_in": bool(config.voice_barge_in),
        },
        "queue_ready": bool(getattr(_job_queue, "enabled", False)
                            and getattr(_job_queue, "_started", False)),
        "presence": get_presence().current(),
        "tts": tts,
        "stt": stt,
    }


@router.post("/voice/ack")
async def voice_ack(payload: dict | None = None):
    """Speak a single acknowledgment phrase. Used by the UI and by tests."""
    if not config.voice_enabled:
        return JSONResponse({"success": False, "error": "voice mode disabled"}, status_code=503)
    payload = payload or {}
    text = str(payload.get("text") or "").strip() or pick_ack()
    clip = await _synthesize_async(text)
    try:
        from core.presence import get_presence

        get_presence().record_moment(
            "voice_ack", "Spoke a voice acknowledgement",
            session_id=str(payload.get("session_id") or ""),
            user_id=str(payload.get("user_id") or "default"),
            metadata={"spoken": bool(clip.get("spoken")), "mode": "manual"},
            emit=False,
        )
    except Exception:
        pass
    return {"success": bool(clip.get("spoken")), **clip}


@router.post("/voice/command")
async def voice_command(
    request: Request,
    model: str = "",
    language: str | None = None,
    session_id: str = "",
    user_id: str = "default",
):
    """Speak first, work second.

    Accepts a raw microphone Blob (not multipart, matching /speech/transcribe),
    transcribes it locally, returns a spoken acknowledgment, and queues the real
    work. The client plays the ack immediately and streams the answer later.
    """
    if not config.voice_enabled:
        return JSONResponse({"success": False, "error": "voice mode disabled "
                                                        "(set HERMUS_VOICE_ENABLED=1)"}, status_code=503)
    started = time.time()
    body = await request.body()
    if not body:
        return JSONResponse({"success": False, "error": "audio body required"}, status_code=400)
    if len(body) > _MAX_AUDIO_BYTES:
        return JSONResponse({"success": False, "error": "audio exceeds 25 MB limit"}, status_code=413)

    transcript = await _transcribe_body(
        body, request.headers.get("content-type") or "audio/webm",
        model=model, language=language, session_id=session_id,
    )
    if not transcript.get("success"):
        return JSONResponse({"success": False, "stage": "transcribe", **transcript}, status_code=503)

    text = str(transcript.get("text") or "").strip()
    if not text:
        return JSONResponse({"success": False, "stage": "transcribe",
                             "error": "no speech detected"}, status_code=422)

    # Acknowledgment first: this is the latency the user actually perceives.
    ack = await _make_ack(text)
    try:
        from core.presence import get_presence

        get_presence().record_moment(
            "voice_request", "Received a voice request",
            session_id=session_id, user_id=user_id or "default",
            metadata={"ack_spoken": bool(ack.get("spoken")), "input": "microphone"},
            emit=False,
        )
    except Exception:
        pass

    queued = _enqueue(
        {"text": text, "platform": "voice", "session_id": session_id,
         "user_id": user_id or "default", "prefer": "auto", "voice": True},
        session_key=f"voice:{session_id or 'default'}",
        dedupe_key=f"voice:{session_id}:{int(started * 1000)}",
    )
    ack_ms = int((time.time() - started) * 1000)

    if queued is None or queued.get("error"):
        # No queue: degrade to running inline rather than dropping the request.
        result = _run_inline({"text": text, "prefer": "auto"})
        return JSONResponse({"success": bool(result.get("answer") or result.get("error")),
                             "queued": False, "transcript": text, "ack": ack,
                             "ack_ms": ack_ms, **result})

    return JSONResponse(
        {"success": True, "queued": True, "transcript": text, "ack": ack,
         "ack_ms": ack_ms, **queued},
        status_code=202,
    )


@router.post("/voice/say")
async def voice_say(payload: dict | None = None):
    """Typed input, spoken response. Same ack-then-queue flow without the mic."""
    if not config.voice_enabled:
        return JSONResponse({"success": False, "error": "voice mode disabled"}, status_code=503)
    started = time.time()
    payload = payload or {}
    text = str(payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"success": False, "error": "text required"}, status_code=400)
    session_id = str(payload.get("session_id") or "")
    user_id = str(payload.get("user_id") or "default")

    ack = await _make_ack(text)
    try:
        from core.presence import get_presence

        get_presence().record_moment(
            "voice_request", "Received a typed voice-mode request",
            session_id=session_id, user_id=user_id,
            metadata={"ack_spoken": bool(ack.get("spoken")), "input": "typed"},
            emit=False,
        )
    except Exception:
        pass
    queued = _enqueue(
        {"text": text, "platform": "voice", "session_id": session_id,
         "user_id": user_id,
         "prefer": str(payload.get("prefer") or "auto"), "voice": True},
        session_key=f"voice:{session_id or 'default'}",
        dedupe_key=f"voice:{session_id}:{int(started * 1000)}",
    )
    ack_ms = int((time.time() - started) * 1000)
    if queued is None or queued.get("error"):
        result = _run_inline({"text": text, "prefer": str(payload.get("prefer") or "auto")})
        return JSONResponse({"success": bool(result.get("answer") or result.get("error")),
                             "queued": False, "transcript": text, "ack": ack,
                             "ack_ms": ack_ms, **result})
    return JSONResponse({"success": True, "queued": True, "transcript": text,
                         "ack": ack, "ack_ms": ack_ms, **queued}, status_code=202)
