"""Voice transcription — local Whisper / NPU, plus Handy-style local model discovery.

Two transcription backends, in order:

1. **NoLlama on the NPU** when the hardware plan gives the ``background`` role
   to the local engine and a Whisper IR has been downloaded.
2. **faster-whisper on the CPU** as the default fallback.

Handy-inspired integrations added here — without importing its desktop app
architecture — are deliberately scoped to what fits Hermus cleanly:

* discover compatible local STT model files/directories in conventional paths;
* normalize transcripts before returning them;
* optionally write successful transcripts into the canonical MemoryFacade so
  voice input becomes part of the same searchable session history.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

from core.config import config

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

_FILLERS = {
    "en": {"um", "uh", "erm", "hmm", "mm"},
}
_PARakeet_DIRS = {
    "parakeet-tdt-0.6b-v2-int8": {"engine": "parakeet", "name": "Parakeet V2", "languages": ["en"]},
    "parakeet-tdt-0.6b-v3-int8": {
        "engine": "parakeet",
        "name": "Parakeet V3",
        "languages": [
            "en", "de", "fr", "es", "it", "pt", "nl", "pl", "sv", "da", "nb", "fi", "cs",
            "sk", "bg", "hr", "ro", "et", "hu", "ru", "uk", "ja", "ko", "zh", "ar",
        ],
    },
}


def _get_model(model_name: str = "base"):
    global _model, _model_name
    if not FASTER_WHISPER_AVAILABLE:
        return None, "faster-whisper not installed. Install free: pip install faster-whisper (local Whisper, no API key)"

    if _model is None or _model_name != model_name:
        try:
            _model = WhisperModel(model_name, device="cpu", compute_type="int8")
            _model_name = model_name
        except Exception as e:  # noqa: BLE001
            return None, f"Failed to load Whisper model {model_name}: {e}. Try base or small."

    return _model, None


def normalize_transcription_text(text: str) -> str:
    """Normalize raw transcript text into stable readable output."""
    value = str(text or "")
    value = value.replace("\u200b", " ").replace("\ufeff", " ")
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s+([,.;!?])", r"\1", value)
    return value



def strip_filler_words(text: str, language: Optional[str] = None) -> str:
    """Remove a small conservative filler-word set when requested."""
    lang = str((language or "en")).lower().split("-", 1)[0]
    fillers = _FILLERS.get(lang)
    if not fillers:
        return normalize_transcription_text(text)
    words = normalize_transcription_text(text).split()
    kept = [w for w in words if w.lower().strip(",.!?;:") not in fillers]
    return normalize_transcription_text(" ".join(kept))



def _remember_transcript(text: str, *, session_id: str = "", project: str = "", backend: str = "") -> dict[str, Any]:
    if not session_id:
        return {"remembered": False, "reason": "session_id required"}
    try:
        from core.memory import get_memory

        memory = get_memory()
        memory.add_session_message(session_id, "user", text, project=project or None)
        return {"remembered": True, "session_id": session_id, "project": project or None, "backend": backend}
    except Exception as exc:  # noqa: BLE001
        return {"remembered": False, "reason": str(exc), "backend": backend}



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



def transcribe_audio(
    audio_path: str,
    model: str = "base",
    language: str = None,
    *,
    normalize: Optional[bool] = None,
    strip_fillers: Optional[bool] = None,
    remember: bool = False,
    session_id: str = "",
    project: str = "",
) -> dict:
    """Transcribe audio to text - local engine first, faster-whisper fallback."""
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
            return _postprocess_result(
                engine,
                normalize=normalize,
                strip_fillers=strip_fillers,
                remember=remember,
                session_id=session_id,
                project=project,
            )
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
                "text": segment.text,
            })

        full_text = " ".join(text_parts).strip()
        result = {
            "success": True,
            "backend": "faster-whisper",
            "audio_path": audio_path,
            "model": model,
            "language": info.language if hasattr(info, "language") else language,
            "language_probability": info.language_probability if hasattr(info, "language_probability") else None,
            "text": full_text,
            "segments": segments_list[:20],
            "duration": info.duration if hasattr(info, "duration") else None,
        }
        if engine_error:
            result["local_engine_error"] = engine_error
        return _postprocess_result(
            result,
            normalize=normalize,
            strip_fillers=strip_fillers,
            remember=remember,
            session_id=session_id,
            project=project,
        )
    except Exception as e:  # noqa: BLE001
        return {
            "success": False,
            "backend": "faster-whisper",
            "error": f"Transcription failed: {e}",
            **({"local_engine_error": engine_error} if engine_error else {}),
        }



def _postprocess_result(
    result: dict[str, Any],
    *,
    normalize: Optional[bool],
    strip_fillers: Optional[bool],
    remember: bool,
    session_id: str,
    project: str,
) -> dict[str, Any]:
    if not result.get("success"):
        return result
    raw = str(result.get("text") or "")
    do_normalize = config.stt_normalize_default if normalize is None else bool(normalize)
    do_strip = config.stt_strip_fillers_default if strip_fillers is None else bool(strip_fillers)
    text = normalize_transcription_text(raw) if do_normalize else raw
    if do_strip:
        text = strip_filler_words(text, result.get("language"))
    out = dict(result)
    out["raw_text"] = raw
    out["text"] = text
    out["blank"] = not bool(text.strip())
    out["postprocess"] = {"normalized": do_normalize, "strip_fillers": do_strip}
    if remember and text.strip():
        out["memory"] = _remember_transcript(
            text,
            session_id=session_id,
            project=project,
            backend=str(out.get("backend") or ""),
        )
    return out



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



def _default_handy_model_dirs() -> list[str]:
    dirs = []
    env_dirs = str(getattr(config, "handy_model_dirs", "") or os.getenv("HERMUS_HANDY_MODELS_DIRS", "")).strip()
    if env_dirs:
        parts = env_dirs.replace("\n", os.pathsep).replace(",", os.pathsep).split(os.pathsep)
        for part in parts:
            part = part.strip()
            if part:
                dirs.append(part)
    home = Path.home()
    dirs.extend([
        str(home / ".config" / "com.pais.handy" / "models"),
        str(home / "Library" / "Application Support" / "com.pais.handy" / "models"),
        str(home / "models"),
    ])
    appdata = os.getenv("APPDATA")
    if appdata:
        dirs.append(str(Path(appdata) / "com.pais.handy" / "models"))
    return _dedupe(dirs)



def discover_local_stt_models(search_dirs: Optional[list[str]] = None) -> dict[str, Any]:
    """Discover Handy-compatible local STT models in conventional directories."""
    dirs = _dedupe(search_dirs or _default_handy_model_dirs())
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for base in dirs:
        root = Path(os.path.expanduser(base))
        if not root.exists() or not root.is_dir():
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            low = name.lower()
            if name.startswith(".") or low.endswith(".partial"):
                continue
            if entry.is_dir():
                preset = _PARakeet_DIRS.get(name)
                if preset:
                    key = f"dir:{entry.resolve()}"
                    if key not in seen:
                        seen.add(key)
                        models.append({
                            "id": key,
                            "name": preset["name"],
                            "engine": preset["engine"],
                            "format": "directory",
                            "path": str(entry.resolve()),
                            "languages": list(preset.get("languages") or []),
                            "size_bytes": None,
                            "source_dir": str(root),
                        })
                continue
            if entry.suffix.lower() not in (".bin", ".gguf"):
                continue
            if low.endswith(".bin") and not (low.startswith("ggml-") or "whisper" in low):
                continue
            key = f"file:{entry.resolve()}"
            if key in seen:
                continue
            seen.add(key)
            models.append({
                "id": key,
                "name": _friendly_model_name(entry.stem),
                "engine": _guess_engine(entry.name),
                "format": entry.suffix.lower().lstrip("."),
                "path": str(entry.resolve()),
                "languages": _guess_languages(entry.name),
                "size_bytes": _safe_size(entry),
                "source_dir": str(root),
            })
    models.sort(key=lambda row: (row.get("engine") != "parakeet", row.get("name") or ""))
    return {"count": len(models), "search_dirs": dirs, "models": models}



def voice_available_models() -> dict:
    """List available Whisper models plus discovered local files/directories."""
    models = ["tiny", "base", "small", "medium", "large-v2", "large-v3"]
    engine = local_engine_status()
    discovered = discover_local_stt_models()
    return {
        "available_models": models,
        "current_model": _model_name if _model else "none",
        "faster_whisper_installed": FASTER_WHISPER_AVAILABLE,
        "local_engine": engine,
        "preferred_backend": "nollama" if engine.get("ready") else "faster-whisper",
        "install": "pip install faster-whisper",
        "note": "Models are downloaded automatically first time, free, local, no API key. tiny=39M, base=74M, small=244M, medium=769M, large-v2=1550M",
        "discovered_models": discovered["models"],
        "discovered_count": discovered["count"],
        "search_dirs": discovered["search_dirs"],
        "postprocess": {
            "normalize_default": bool(getattr(config, "stt_normalize_default", True)),
            "strip_fillers_default": bool(getattr(config, "stt_strip_fillers_default", False)),
        },
    }



def voice_discover_local_models() -> dict:
    """Expose only the on-disk discovery view."""
    return discover_local_stt_models()



def _friendly_model_name(stem: str) -> str:
    text = stem.replace("_", " ").replace("-", " ").strip()
    return " ".join(word.capitalize() for word in text.split()) or stem



def _guess_engine(name: str) -> str:
    low = name.lower()
    if low.endswith(".bin"):
        return "whisper-ggml"
    for engine in ("parakeet", "canary", "nemotron", "granite", "cohere"):
        if engine in low:
            return engine
    return "gguf-transcribe"



def _guess_languages(name: str) -> list[str]:
    low = name.lower()
    if "unified-en" in low or "english" in low:
        return ["en"]
    if "multilingual" in low or "transcribe" in low or "parakeet" in low or "canary" in low:
        return ["auto"]
    return []



def _safe_size(path: Path) -> Optional[int]:
    try:
        return path.stat().st_size
    except OSError:
        return None



def _dedupe(items: list[str]) -> list[str]:
    out = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "transcribe_audio",
            "description": "Transcribe audio to text via the local engine or faster-whisper. Also supports Handy-style transcript normalization, on-disk model discovery, and optional MemoryFacade logging.",
            "parameters": {
                "type": "object",
                "properties": {
                    "audio_path": {"type": "string", "description": "Path to audio file"},
                    "model": {"type": "string", "description": "Whisper model: tiny, base, small, medium, large-v2", "default": "base"},
                    "language": {"type": "string", "description": "Language code e.g., en, es, fr, or None for auto-detect"},
                    "normalize": {"type": "boolean", "default": True},
                    "strip_fillers": {"type": "boolean", "default": False},
                    "remember": {"type": "boolean", "default": False},
                    "session_id": {"type": "string", "description": "Store the transcript in session history under this session id"},
                    "project": {"type": "string", "description": "Optional project scope for remembered transcripts"},
                },
                "required": ["audio_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "voice_available_models",
            "description": "List faster-whisper choices plus discovered local Handy-compatible GGML/GGUF/Parakeet assets.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "voice_discover_local_models",
            "description": "Discover on-disk Handy-compatible local speech-to-text model assets in conventional directories.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

TOOL_MAP = {
    "transcribe_audio": transcribe_audio,
    "transcribe_voice_memo": transcribe_voice_memo,
    "voice_available_models": voice_available_models,
    "voice_discover_local_models": voice_discover_local_models,
}
