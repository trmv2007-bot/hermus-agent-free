"""Bridge voice sessions into HERMUS's executive request lifecycle."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable

from core.voice_presence import VoicePresence

@dataclass
class VoiceTurnResult:
    session_id: str
    transcript: str
    result: Any = None
    spoken: bool = False

class VoiceExecutiveBridge:
    def __init__(self, presence: VoicePresence, executive_handler: Callable[[str], Any], speaker: Callable[[str], Any] | None = None):
        self.presence = presence
        self.executive_handler = executive_handler
        self.speaker = speaker

    def handle_transcript(self, session_id: str, transcript: str) -> VoiceTurnResult:
        session = self.presence.get(session_id) or self.presence.start(session_id)
        if not session.active or session.muted:
            return VoiceTurnResult(session_id, transcript, result={"ok": False, "error": "voice_session_inactive_or_muted"})
        self.presence.record_input(session_id)
        result = self.executive_handler(transcript)
        spoken = False
        if self.speaker is not None:
            text = self._response_text(result)
            if text:
                self.speaker(text)
                self.presence.record_output(session_id)
                spoken = True
        return VoiceTurnResult(session_id, transcript, result=result, spoken=spoken)

    @staticmethod
    def _response_text(result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            for key in ("answer", "response", "message", "text"):
                if result.get(key):
                    return str(result[key])
        return ""

__all__ = ["VoiceExecutiveBridge", "VoiceTurnResult"]
