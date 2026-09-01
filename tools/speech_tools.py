"""Tool wrappers for the canonical speech subsystem.

The actual TTS/clone/design logic lives in :mod:`core.speech`. This module only
surfaces it through the ToolRegistry so agent turns and missions can use the same
capability through the ToolGateway.
"""
from __future__ import annotations

from core.speech import speech_engine


def speech_status() -> dict:
    """Report the local speech capability stack and prompt cache."""
    return speech_engine.status()


def speech_synthesize(
    text: str,
    voice: str = "",
    rate: int = 165,
    backend: str = "",
    language: str = "",
    ref_audio: str = "",
    ref_text: str = "",
    instruct: str = "",
    duration: float = 0.0,
    speed: float = 0.0,
    prompt_id: str = "",
    create_prompt_id: str = "",
    normalize_text: bool = False,
) -> dict:
    """Generate local speech, optionally using OmniVoice cloning/design."""
    return speech_engine.synthesize(
        text,
        voice or None,
        int(rate or 165),
        backend=backend or None,
        language=language or None,
        ref_audio=ref_audio or None,
        ref_text=ref_text or None,
        instruct=instruct or None,
        duration=(float(duration) if duration not in (None, "", 0) else None),
        speed=(float(speed) if speed not in (None, "", 0) else None),
        prompt_id=prompt_id or None,
        create_prompt_id=create_prompt_id or None,
        normalize_text=bool(normalize_text),
    )


def speech_clone_prompt_create(ref_audio: str, ref_text: str = "", prompt_id: str = "", label: str = "") -> dict:
    """Create and persist a reusable OmniVoice clone prompt."""
    return speech_engine.create_clone_prompt(
        ref_audio,
        ref_text=ref_text or None,
        prompt_id=prompt_id or None,
        label=label,
    )


def speech_clone_prompts() -> dict:
    """List cached OmniVoice clone prompts."""
    prompts = speech_engine.list_clone_prompts()
    return {"count": len(prompts), "prompts": prompts}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "speech_status",
            "description": "Report local speech backends, OmniVoice availability, and cached voice-clone prompts.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "speech_synthesize",
            "description": "Generate local speech. Supports ordinary local TTS or advanced OmniVoice multilingual synthesis, voice cloning, and voice design when installed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "voice": {"type": "string", "description": "Simple backend voice/speaker identifier"},
                    "rate": {"type": "integer", "default": 165},
                    "backend": {"type": "string", "description": "auto, omnivoice, piper, espeak, pyttsx3"},
                    "language": {"type": "string", "description": "Target language/code for OmniVoice"},
                    "ref_audio": {"type": "string", "description": "Reference audio for voice cloning"},
                    "ref_text": {"type": "string", "description": "Transcript for the reference audio"},
                    "instruct": {"type": "string", "description": "OmniVoice voice-design instruction such as 'female, low pitch, british accent'"},
                    "duration": {"type": "number"},
                    "speed": {"type": "number"},
                    "prompt_id": {"type": "string", "description": "Cached OmniVoice clone prompt id"},
                    "create_prompt_id": {"type": "string", "description": "Persist a new OmniVoice clone prompt before synthesis"},
                    "normalize_text": {"type": "boolean", "default": False},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "speech_clone_prompt_create",
            "description": "Create a reusable OmniVoice voice-clone prompt from a reference audio clip.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref_audio": {"type": "string"},
                    "ref_text": {"type": "string"},
                    "prompt_id": {"type": "string"},
                    "label": {"type": "string"},
                },
                "required": ["ref_audio"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "speech_clone_prompts",
            "description": "List cached OmniVoice clone prompts that can be reused across sessions.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

TOOL_MAP = {
    "speech_status": speech_status,
    "speech_synthesize": speech_synthesize,
    "speech_clone_prompt_create": speech_clone_prompt_create,
    "speech_clone_prompts": speech_clone_prompts,
}
