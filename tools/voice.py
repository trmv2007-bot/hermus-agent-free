"""Voice Memo Transcription - local Whisper, no API key, free.

Two local backends, in order:

1. **NoLlama on the NPU** when the hardware plan puts the ``background`` role
   there and a Whisper IR has been downloaded from the dashboard. The NPU runs
   cool and silent, which is what continuous voice commands want, and it leaves
   the GPU free for the agent's generative work.
2. **faster-whisper on the CPU** - always the fallback, and the only backend on
   machines without an Intel accelerator.

Set ``HERMUS_STT_BACKEND=cpu`` to pin the CPU path, or ``=nollama`` to require
the local engine (errors out instead of falling back when it cannot serve).
"""
import os
from pathlib import Path

# faster-whisper optional - free local Whisper
FASTER_WHISPER_AVAILABLE = False
try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False

# Global model cache
_model = None
_model_name = "base"

def _get_model(model_name: str = "base"):
    global _model, _model_name
    if not FASTER_WHISPER_AVAILABLE:
        return None, "faster-whisper not installed. Install free: pip install faster-whisper (local Whisper, no API key)"

    if _model is None or _model_name != model_name:
        try:
            # base, small, medium, large-v2 are free local models
            _model = WhisperModel(model_name, device="cpu", compute_type="int8")
            _model_name = model_name
        except Exception as e:
            return None, f"Failed to load Whisper model {model_name}: {e}. Try base or small."

    return _model, None

def local_engine_transcribe(audio_path: str, language: str = None) -> dict:
    """Transcribe on the local engine, or report why it cannot.

    Never raises: every refusal comes back as ``{"success": False, ...}`` so the
    caller can fall back to the CPU without a try/except around it.
    """
    try:
        from core.accelerators import ENGINE_NOLLAMA, role_assignment

        if role_assignment("background").get("engine") != ENGINE_NOLLAMA:
            return {"success": False, "error": "local engine does not own the background role"}
        from core.nollama import nollama_manager

        return nollama_manager.transcribe(audio_path, language=language)
    except Exception as e:  # noqa: BLE001 - routing must never break voice input
        return {"success": False, "error": f"{type(e).__name__}: {e}"[:200]}


def transcribe_audio(audio_path: str, model: str = "base", language: str = None) -> dict:
    """Transcribe voice memo audio to text - local engine first, faster-whisper fallback."""
    p = Path(audio_path)
    if not p.exists():
        return {"success": False, "error": f"Audio file not found: {audio_path}"}

    backend = str(os.getenv("HERMUS_STT_BACKEND", "auto") or "auto").strip().lower()
    engine_error = None
    if backend in ("auto", "nollama", "local"):
        engine = local_engine_transcribe(str(p), language)
        if engine.get("success"):
            engine.setdefault("backend", "nollama")
            engine["audio_path"] = audio_path
            return engine
        engine_error = engine.get("error")
        if backend in ("nollama", "local"):
            return {"success": False, "backend": "nollama", "error": engine_error}

    model_obj, err = _get_model(model)
    if err:
        return {"success": False, "backend": "faster-whisper", "error": err,
                **({"local_engine_error": engine_error} if engine_error else {})}

    try:
        segments, info = model_obj.transcribe(str(p), language=language, beam_size=5)
        text_parts = []
        segments_list = []
        for segment in segments:
            text_parts.append(segment.text)
            segments_list.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text
            })

        full_text = " ".join(text_parts).strip()

        return {
            "success": True,
            "backend": "faster-whisper",
            "audio_path": audio_path,
            "model": model,
            "language": info.language if hasattr(info, 'language') else language,
            "language_probability": info.language_probability if hasattr(info, 'language_probability') else None,
            "text": full_text,
            "segments": segments_list[:20],  # first 20 segments
            "duration": info.duration if hasattr(info, 'duration') else None
        }

    except Exception as e:
        return {"success": False, "backend": "faster-whisper",
                "error": f"Transcription failed: {e}",
                **({"local_engine_error": engine_error} if engine_error else {})}

def transcribe_voice_memo(audio_path: str, model: str = "base") -> dict:
    """Alias for transcribe_audio - for voice memo transcription feature"""
    return transcribe_audio(audio_path, model=model)

def local_engine_status() -> dict:
    """Is the NPU Whisper path actually usable right now? (for /speech/status)"""
    try:
        from core.accelerators import ENGINE_NOLLAMA, role_assignment
        from core.nollama import nollama_manager

        assigned = role_assignment("background").get("engine") == ENGINE_NOLLAMA
        whisper = nollama_manager.whisper_model() if assigned else None
        return {
            "engine": "nollama",
            "assigned_to_background_role": assigned,
            "running": bool(assigned and nollama_manager.running()),
            "whisper_model": (whisper or {}).get("name") or None,
            "ready": bool(assigned and nollama_manager.running() and whisper),
        }
    except Exception as e:  # noqa: BLE001
        return {"engine": "nollama", "ready": False, "error": f"{type(e).__name__}: {e}"[:120]}


def voice_available_models() -> dict:
    """List available Whisper models - free local"""
    models = ["tiny", "base", "small", "medium", "large-v2", "large-v3"]
    engine = local_engine_status()
    return {
        "available_models": models,
        "current_model": _model_name if _model else "none",
        "faster_whisper_installed": FASTER_WHISPER_AVAILABLE,
        "local_engine": engine,
        "preferred_backend": "nollama" if engine.get("ready") else "faster-whisper",
        "install": "pip install faster-whisper",
        "note": "Models are downloaded automatically first time, free, local, no API key. tiny=39M, base=74M, small=244M, medium=769M, large-v2=1550M"
    }

# Tool definitions for free LLM
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "transcribe_audio",
            "description": "Transcribe voice memo audio to text via faster-whisper free local Whisper - no API key, no cloud, runs offline. Supports mp3, wav, m4a, ogg, etc. For voice memo transcription feature in gateway.",
            "parameters": {
                "type": "object",
                "properties": {
                    "audio_path": {"type": "string", "description": "Path to audio file"},
                    "model": {"type": "string", "description": "Whisper model: tiny, base, small, medium, large-v2", "default": "base"},
                    "language": {"type": "string", "description": "Language code e.g., en, es, fr, or None for auto-detect"}
                },
                "required": ["audio_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "voice_available_models",
            "description": "List available faster-whisper models - free local",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    }
]

TOOL_MAP = {
    "transcribe_audio": transcribe_audio,
    "transcribe_voice_memo": transcribe_voice_memo,
    "voice_available_models": voice_available_models,
}
