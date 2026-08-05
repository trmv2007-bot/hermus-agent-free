"""Voice Memo Transcription faster-whisper Free - Local Whisper, no API key, free"""
from pathlib import Path
from typing import Dict

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

def transcribe_audio(audio_path: str, model: str = "base", language: str = None) -> Dict:
    """Transcribe voice memo audio to text via faster-whisper free local - no API key"""
    p = Path(audio_path)
    if not p.exists():
        return {"success": False, "error": f"Audio file not found: {audio_path}"}

    model_obj, err = _get_model(model)
    if err:
        return {"success": False, "error": err}

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
            "audio_path": audio_path,
            "model": model,
            "language": info.language if hasattr(info, 'language') else language,
            "language_probability": info.language_probability if hasattr(info, 'language_probability') else None,
            "text": full_text,
            "segments": segments_list[:20],  # first 20 segments
            "duration": info.duration if hasattr(info, 'duration') else None
        }

    except Exception as e:
        return {"success": False, "error": f"Transcription failed: {e}"}

def transcribe_voice_memo(audio_path: str, model: str = "base") -> Dict:
    """Alias for transcribe_audio - for voice memo transcription feature"""
    return transcribe_audio(audio_path, model=model)

def voice_available_models() -> Dict:
    """List available Whisper models - free local"""
    models = ["tiny", "base", "small", "medium", "large-v2", "large-v3"]
    return {
        "available_models": models,
        "current_model": _model_name if _model else "none",
        "faster_whisper_installed": FASTER_WHISPER_AVAILABLE,
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
