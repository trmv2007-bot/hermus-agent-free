"""Tool wrappers for the local talking-avatar connector.

These wrap :mod:`core.avatar` so the agent can use a HeyGem-style local render
pipeline through the canonical ToolGateway instead of calling service endpoints
ad hoc.
"""
from __future__ import annotations

from core.avatar import get_avatar_service


def avatar_service_status(probe: bool = False) -> dict:
    """Report whether the local avatar services are configured/reachable."""
    return get_avatar_service().status(probe=bool(probe))


def avatar_prepare_voice(reference_audio: str, lang: str = "en", format: str = "") -> dict:
    """Prepare a HeyGem-style voice profile from reference audio."""
    return get_avatar_service().prepare_voice(reference_audio, lang=lang or "en", fmt=format)


def avatar_synthesize_audio(
    text: str,
    voice_profile_id: str = "",
    reference_audio: str = "",
    reference_text: str = "",
    lang: str = "en",
    speaker: str = "",
    output_name: str = "",
) -> dict:
    """Create cloned speech audio for an avatar render."""
    return get_avatar_service().synthesize_audio(
        text,
        voice_profile_id=voice_profile_id,
        reference_audio=reference_audio,
        reference_text=reference_text,
        lang=lang or "en",
        speaker=speaker,
        output_name=output_name,
    )


def avatar_render_video(
    text: str,
    avatar_video_path: str,
    voice_profile_id: str = "",
    reference_audio: str = "",
    reference_text: str = "",
    lang: str = "en",
    code: str = "",
) -> dict:
    """Run the local avatar pipeline: cloned audio then talking-head video submit."""
    return get_avatar_service().render_from_text(
        text,
        avatar_video_path,
        voice_profile_id=voice_profile_id,
        reference_audio=reference_audio,
        reference_text=reference_text,
        lang=lang or "en",
        code=code,
    )


def avatar_render_status(code: str) -> dict:
    """Query the status of a submitted avatar render."""
    return get_avatar_service().query_video(code)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "avatar_service_status",
            "description": "Report whether the local talking-avatar services are configured and reachable.",
            "parameters": {
                "type": "object",
                "properties": {"probe": {"type": "boolean", "default": False}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "avatar_prepare_voice",
            "description": "Prepare a reusable voice profile from reference audio using a HeyGem-style local voice service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reference_audio": {"type": "string"},
                    "lang": {"type": "string", "default": "en"},
                    "format": {"type": "string", "description": "Audio format extension such as wav or mp3"},
                },
                "required": ["reference_audio"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "avatar_synthesize_audio",
            "description": "Generate cloned speech audio for a talking avatar render.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "voice_profile_id": {"type": "string"},
                    "reference_audio": {"type": "string"},
                    "reference_text": {"type": "string"},
                    "lang": {"type": "string", "default": "en"},
                    "speaker": {"type": "string"},
                    "output_name": {"type": "string"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "avatar_render_video",
            "description": "Submit a local talking-head avatar video render from text plus an avatar video template.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "avatar_video_path": {"type": "string"},
                    "voice_profile_id": {"type": "string"},
                    "reference_audio": {"type": "string"},
                    "reference_text": {"type": "string"},
                    "lang": {"type": "string", "default": "en"},
                    "code": {"type": "string", "description": "Caller-supplied task identifier"},
                },
                "required": ["text", "avatar_video_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "avatar_render_status",
            "description": "Query a submitted local avatar render by task code.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        },
    },
]

TOOL_MAP = {
    "avatar_service_status": avatar_service_status,
    "avatar_prepare_voice": avatar_prepare_voice,
    "avatar_synthesize_audio": avatar_synthesize_audio,
    "avatar_render_video": avatar_render_video,
    "avatar_render_status": avatar_render_status,
}
