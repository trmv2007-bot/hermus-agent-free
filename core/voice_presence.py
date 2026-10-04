"""Presence bridge for conversational HERMUS voice sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any


@dataclass
class VoiceSession:
    session_id: str
    active: bool = True
    muted: bool = False
    last_input_at: float | None = None
    last_output_at: float | None = None
    turns: int = 0
    output_generation: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class VoicePresence:
    """Small state machine above the existing speech routes/tools."""

    def __init__(self) -> None:
        self._sessions: dict[str, VoiceSession] = {}

    def start(self, session_id: str, **metadata: Any) -> VoiceSession:
        session = VoiceSession(session_id=session_id, metadata=dict(metadata))
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> VoiceSession | None:
        return self._sessions.get(session_id)

    def record_input(self, session_id: str) -> VoiceSession:
        session = self._sessions.setdefault(session_id, VoiceSession(session_id=session_id))
        session.last_input_at = time()
        session.turns += 1
        return session

    def record_output(self, session_id: str) -> VoiceSession:
        session = self._sessions.setdefault(session_id, VoiceSession(session_id=session_id))
        session.last_output_at = time()
        return session

    def set_muted(self, session_id: str, muted: bool) -> VoiceSession:
        session = self._sessions.setdefault(session_id, VoiceSession(session_id=session_id))
        session.muted = bool(muted)
        return session

    def interrupt_output(self, session_id: str) -> VoiceSession:
        session = self._sessions.setdefault(session_id, VoiceSession(session_id=session_id))
        session.output_generation += 1
        session.last_output_at = None
        return session

    def output_allowed(self, session_id: str, generation: int) -> bool:
        session = self._sessions.get(session_id)
        return bool(session and session.active and not session.muted and session.output_generation == generation)

    def begin_output(self, session_id: str) -> int:
        session = self._sessions.setdefault(session_id, VoiceSession(session_id=session_id))
        return session.output_generation

    def stop(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.active = False
        return True

    def snapshot(self, session_id: str | None = None) -> dict[str, Any]:
        rows = (
            self._sessions.values()
            if session_id is None
            else [self._sessions[session_id]]
            if session_id in self._sessions
            else []
        )
        return {
            "sessions": [
                {
                    "session_id": item.session_id,
                    "active": item.active,
                    "muted": item.muted,
                    "last_input_at": item.last_input_at,
                    "last_output_at": item.last_output_at,
                    "turns": item.turns,
                    "output_generation": item.output_generation,
                    "metadata": item.metadata,
                }
                for item in rows
            ]
        }


voice_presence = VoicePresence()

__all__ = ["VoicePresence", "VoiceSession", "voice_presence"]
