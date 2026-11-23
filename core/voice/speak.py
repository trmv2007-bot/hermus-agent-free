"""Spoken output for the room: a sentence at a time, redacted, interruptible.

Three properties matter here and each has a reason:

* The first sentence is spoken while the rest of the answer is still arriving,
  so speech starts in a few hundred milliseconds instead of after a whole reply.
  Sentences are synthesized one at a time for that reason.
* Speech is cut off the moment the person talks or presses stop. The generation
  counter lives in ``core.voice_presence``, which every voice surface shares, so
  a clip synthesized for a superseded turn is never played.
* Nothing secret-shaped is ever spoken out loud. A password read aloud into a
  room is a password that has left the machine, so the text is redacted before
  any audio exists.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from core.awareness.redact import SECRET_PATTERNS

# Spoken text is more dangerous than displayed text: it can be heard by anyone
# in the room, cannot be scrolled away, and leaves no record to redact later.
# These patterns cover a credential the answer might name in a sentence, not
# just the ``key=value`` shape the awareness layer handles.
_SPEECH_SECRETS = (
    (
        re.compile(
            r"(?i)\b(password|passphrase|passcode|secret|api[ _-]?key|private[ _-]?key|"
            r"access[ _-]?token|auth[ _-]?token|recovery code)\b\s*(?:is|are|was|were|=|:)\s*\S+"
        ),
        r"\1 [hidden]",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "bearer [hidden]"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "[hidden private key]",
    ),
    (re.compile(r"\b(?:\d{4}[ -]){3}\d{4}\b"), "[hidden number]"),
)

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_BREAK = re.compile(r"(?<=[,;:])\s+")

MAX_CLIP_CHARS = 240


def redact_for_speech(text: str) -> tuple[str, bool]:
    """Remove anything that would be a credential if it were said out loud."""
    value = str(text or "")
    redacted = False
    for pattern, replacement in _SPEECH_SECRETS + tuple(SECRET_PATTERNS):
        value, hits = pattern.subn(replacement, value)
        redacted = redacted or bool(hits)
    return value.strip(), redacted


def split_for_speech(text: str) -> list[str]:
    """Break an answer into clips short enough to speak while the rest streams."""
    chunks: list[str] = []
    for sentence in _SENTENCE_END.split(str(text or "").strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= MAX_CLIP_CHARS:
            chunks.append(sentence)
            continue
        piece = ""
        for clause in _CLAUSE_BREAK.split(sentence):
            candidate = f"{piece} {clause}".strip()
            if len(candidate) > MAX_CLIP_CHARS and piece:
                chunks.append(piece)
                piece = clause.strip()
            else:
                piece = candidate
        if piece:
            chunks.append(piece)
    return chunks


class SpeechDirector:
    """Turns text into audio the room can play, and knows when to stop."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._playing: dict[str, dict[str, Any]] = {}

    def _presence(self):
        from core.voice_presence import voice_presence

        return voice_presence

    def generation(self, session_id: str) -> int:
        session = self._presence().get(str(session_id or ""))
        return int(getattr(session, "output_generation", 0) or 0)

    def speak(self, text: str, *, session_id: str = "", voice: str | None = None, rate: int = 178) -> dict[str, Any]:
        """Synthesize every clip for this text, or say honestly why it cannot."""
        from core.speech import prepare_speech_text, speech_engine

        started = time.monotonic()
        prepared = prepare_speech_text(text)
        spoken_text, redacted = redact_for_speech(prepared)
        if not spoken_text:
            return {"spoken": False, "error": "nothing speakable in that text", "chars": 0}

        generation = 0
        if session_id:
            generation = self._presence().begin_output(session_id)

        clips: list[dict[str, Any]] = []
        backend = None
        first_ms = None
        for chunk in split_for_speech(spoken_text):
            result = speech_engine.synthesize(chunk, voice, rate)
            if first_ms is None:
                first_ms = round((time.monotonic() - started) * 1000)
            if not isinstance(result, dict) or not result.get("success"):
                error = str((result or {}).get("error") or "speech synthesis failed")
                return {
                    "spoken": False,
                    "error": error,
                    "chars": len(spoken_text),
                    "generation": generation,
                    "clips": clips,
                }
            backend = result.get("backend") or backend
            clips.append(
                {
                    "audio_url": f"/speech/audio/{result['audio_id']}",
                    "text": chunk,
                    "duration": result.get("estimated_duration"),
                }
            )

        stale = bool(session_id) and not self._presence().output_allowed(session_id, generation)
        return {
            "spoken": True,
            "generation": generation,
            "stale": stale,
            "clips": clips,
            "chars": len(spoken_text),
            "redacted": redacted,
            "backend": backend,
            "first_clip_ms": first_ms,
            "generation_ms": round((time.monotonic() - started) * 1000),
        }

    def interrupt(self, session_id: str) -> int:
        """Stop speaking now: every clip already handed out becomes stale."""
        session = self._presence().interrupt_output(str(session_id or ""))
        with self._lock:
            self._playing.pop(str(session_id or ""), None)
        return int(getattr(session, "output_generation", 0) or 0)

    def note_playing(self, session_id: str, playing: bool, duration_ms: int = 0) -> dict[str, Any]:
        """Record whether audio is *actually* coming out of the speakers.

        The room reports this from the media element's own events, so the
        character's speaking state follows real sound rather than a guess made
        when the clip was requested.
        """
        key = str(session_id or "")
        with self._lock:
            if playing:
                self._playing[key] = {"playing": True, "since": time.time(), "duration_ms": int(duration_ms or 0)}
            else:
                self._playing.pop(key, None)
            return {"session_id": key, "playing": playing, "speaking": sorted(self._playing)}

    def speaking(self) -> list[str]:
        with self._lock:
            return sorted(self._playing)

    def is_speaking(self, session_id: str) -> bool:
        with self._lock:
            return str(session_id or "") in self._playing


speech_director = SpeechDirector()

__all__ = ["SpeechDirector", "redact_for_speech", "speech_director", "split_for_speech"]
