"""Push-to-talk listening: raw PCM in, live partials out, one final transcript.

The room captures the microphone as 16 kHz mono PCM16 in the browser and posts
the bytes while the person is still speaking. This module owns one utterance at
a time: it keeps the samples, transcribes the prefix on demand for the live
partial, and transcribes once more when the person stops.

Transcription is faster-whisper on the CPU — free, local, no account. The
partial pass uses a small model because it runs every few hundred milliseconds
while the person talks; the final pass uses a larger one because accuracy is
what the turn is built from. Both download once, on first use, from a public
model hub with no credentials.

Nothing here raises for an absent backend: a missing package comes back as an
unavailable status carrying the one command that fixes it, so the room can
disable its control and say why instead of failing after the click.
"""

from __future__ import annotations

import io
import os
import threading
import time
import uuid
import wave
from array import array
from dataclasses import dataclass, field
from typing import Any

PCM_RATE = 16000
SAMPLE_WIDTH = 2
_CHANNELS = 1
MAX_UTTERANCE_SECONDS = 45
SESSION_IDLE_SECONDS = 300
# A partial costs a full decode of the utterance so far. Requests that arrive
# sooner than this after the previous one get the previous text back instead, so
# a client posting faster than this machine decodes can never build a backlog of
# stale decodes behind the live one.
MIN_PARTIAL_GAP_MS = 220
# Whisper invents a word ("Clear.", "Thank you.") when handed a fragment barely
# long enough to contain a phoneme. Below this much voiced audio there is nothing
# to transcribe yet, so the previous partial stands.
MIN_PARTIAL_VOICED_MS = 300
# One or two words out of under half a second of audio is a coin flip between
# the person's first syllable and the model's own filler. Holding it back costs
# a fraction of a second and keeps invented text out of the input line.
MIN_PARTIAL_ALONE_MS = 450
_SILENCE_RMS = 90
_TRIM_WINDOW_MS = 30

_partial_env = "HERMUS_VOICE_PARTIAL_MODEL"
_default_partial_model = "tiny"
_default_final_model = "base"


# --------------------------------------------------------------------------- #
# availability
# --------------------------------------------------------------------------- #
def _whisper_installed() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:
        return False


def partial_model_name() -> str:
    return str(os.getenv(_partial_env, "") or "").strip() or _default_partial_model


def final_model_name() -> str:
    try:
        from core.config import config

        configured = str(getattr(config, "voice_stt_model", "") or "").strip()
    except Exception:
        configured = ""
    return configured or _default_final_model


def _model_cached(name: str) -> bool:
    """Whether the weights are already on disk, so the first use is offline.

    Reported in the status so the room can say "first use downloads ~75 MB"
    rather than leaving a person staring at a slow first partial.
    """
    try:
        from pathlib import Path

        cache = Path(os.getenv("HF_HOME") or Path.home() / ".cache" / "huggingface")
        return any((cache / "hub").glob(f"models--Systran--faster-whisper-{name}"))
    except Exception:
        return False


def stt_status() -> dict[str, Any]:
    """What push-to-talk will actually do on this install, and what would fix it."""
    installed = _whisper_installed()
    partial = partial_model_name()
    final = final_model_name()
    return {
        "available": bool(installed),
        "engine": "faster-whisper",
        "partial_model": partial,
        "final_model": final,
        "models_cached": {"partial": _model_cached(partial), "final": _model_cached(final)},
        "first_use_download": None
        if (_model_cached(partial) and _model_cached(final))
        else "the first press downloads the speech model once (free, no account)",
        "sample_rate": PCM_RATE,
        "max_utterance_seconds": MAX_UTTERANCE_SECONDS,
        "reason": None if installed else "faster-whisper is not installed, so the microphone has nowhere to send audio",
        "fix": None if installed else "python -m pip install faster-whisper",
        "ready": installed,
    }


# --------------------------------------------------------------------------- #
# model cache
# --------------------------------------------------------------------------- #
_models: dict[str, Any] = {}
_model_locks: dict[str, threading.Lock] = {}
_model_locks_guard = threading.Lock()


def _lock_for(name: str) -> threading.Lock:
    """One lock per model, so loading the big final model cannot hold up a partial."""
    with _model_locks_guard:
        return _model_locks.setdefault(name, threading.Lock())


class SpeechToTextUnavailableError(RuntimeError):
    """Raised when there is no local engine to transcribe with."""


def _load_model(name: str):
    if not _whisper_installed():
        raise SpeechToTextUnavailableError("python -m pip install faster-whisper")
    cached = _models.get(name)
    if cached is not None:
        return cached
    with _lock_for(name):
        if name in _models:
            return _models[name]
        from faster_whisper import WhisperModel

        threads = min(8, max(1, (os.cpu_count() or 4) // 2))
        model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=threads)
        _models[name] = model
        return model


def warm_models(names: tuple[str, ...] | None = None) -> None:
    """Fetch and load models off the request path.

    The first press of the microphone pays for the download once (~75 MB for the
    partial model, several hundred for the final one). Only the partial model is
    warmed while the person is speaking: loading a big model takes CPU away from
    the live transcript, which is the one thing that has to keep up. The final
    model is warmed right after the first utterance closes instead.
    """
    for name in names or (partial_model_name(),):
        try:
            _load_model(name)
        except Exception:  # noqa: BLE001 — warming is best-effort by design
            return


_partial_model: dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# audio helpers
# --------------------------------------------------------------------------- #
def _frames(pcm: bytes) -> array:
    usable = len(pcm) - (len(pcm) % SAMPLE_WIDTH)
    samples = array("h")
    samples.frombytes(pcm[:usable])
    return samples


def _window_rms(samples: array, start: int, count: int) -> float:
    chunk = samples[start : start + count]
    if not chunk:
        return 0.0
    total = 0
    for value in chunk:
        total += value * value
    return (total / len(chunk)) ** 0.5


def trim_silence(pcm: bytes, *, threshold: float = _SILENCE_RMS, pad_ms: int = 160) -> bytes:
    """Drop leading and trailing silence, keeping a short pad for context."""
    samples = _frames(pcm)
    window = max(1, int(PCM_RATE * _TRIM_WINDOW_MS / 1000))
    voiced = [i for i in range(0, len(samples) - window + 1, window) if _window_rms(samples, i, window) >= threshold]
    if not voiced:
        return b""
    pad = int(PCM_RATE * pad_ms / 1000)
    start = max(0, voiced[0] - pad)
    end = min(len(samples), voiced[-1] + window + pad)
    return samples[start:end].tobytes()


def pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap PCM16 mono in a RIFF header — faster-whisper reads files, not streams."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(_CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(PCM_RATE)
        handle.writeframes(pcm)
    return buffer.getvalue()


def duration_ms(pcm: bytes) -> int:
    return int(len(pcm) / (PCM_RATE * SAMPLE_WIDTH) * 1000)


def _as_audio(pcm: bytes):
    """Hand faster-whisper samples when numpy is there, a WAV stream otherwise.

    The array path skips a container decode per partial and was measured at
    roughly half the wall time of the WAV path on the same audio.
    """
    try:
        import numpy as np
    except Exception:
        buffer = io.BytesIO(pcm_to_wav(pcm))
        buffer.name = "utterance.wav"
        return buffer
    samples = np.frombuffer(pcm[: len(pcm) - (len(pcm) % SAMPLE_WIDTH)], dtype=np.int16)
    return samples.astype(np.float32) / 32768.0


def _transcribe(pcm: bytes, model_name: str, language: str | None) -> str:
    if not pcm:
        return ""
    model = _load_model(model_name)
    segments, _info = model.transcribe(
        _as_audio(pcm),
        beam_size=1,
        language=language or None,
        condition_on_previous_text=False,
        without_timestamps=True,
        vad_filter=False,
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #
@dataclass
class ListenSession:
    id: str
    session_id: str = ""
    created_at: float = field(default_factory=time.monotonic)
    touched_at: float = field(default_factory=time.monotonic)
    pcm: bytearray = field(default_factory=bytearray)
    partial_text: str = ""
    final_text: str = ""
    first_partial_ms: int | None = None
    partials: int = 0
    last_partial_audio_ms: int = 0
    decoding: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def budget(self) -> int:
        return PCM_RATE * SAMPLE_WIDTH * MAX_UTTERANCE_SECONDS


class ListenManager:
    """One utterance per listen id, alive for the length of a press."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, ListenSession] = {}
        self._warming = False
        self._final_warm = False

    def _warm(self, names: tuple[str, ...]) -> None:
        try:
            warm_models(names)
        finally:
            self._warming = False

    def start(self, session_id: str = "") -> dict[str, Any]:
        if not _whisper_installed():
            raise SpeechToTextUnavailableError("python -m pip install faster-whisper")
        self._evict()
        if not self._warming:
            self._warming = True
            threading.Thread(target=self._warm, args=((partial_model_name(),),), name="hermus-voice-warm", daemon=True).start()
        session = ListenSession(uuid.uuid4().hex, session_id=session_id)
        with self._lock:
            self._sessions[session.id] = session
        return {
            "id": session.id,
            "sample_rate": PCM_RATE,
            "sample_width": SAMPLE_WIDTH,
            "partial_model": partial_model_name(),
            "final_model": final_model_name(),
        }

    def get(self, listen_id: str) -> ListenSession | None:
        with self._lock:
            return self._sessions.get(str(listen_id or ""))

    def append(self, listen_id: str, pcm: bytes) -> int:
        session = self.get(listen_id)
        if session is None:
            raise KeyError(listen_id)
        with session.lock:
            room = session.budget() - len(session.pcm)
            accepted = min(len(pcm), max(0, room))
            if accepted:
                session.pcm.extend(pcm[:accepted])
            session.touched_at = time.monotonic()
        return accepted

    def snapshot(self, listen_id: str) -> tuple[bytes, int]:
        """Copy the utterance so far plus its length, without holding the lock."""
        session = self.get(listen_id)
        if session is None:
            raise KeyError(listen_id)
        with session.lock:
            return bytes(session.pcm), len(session.pcm)

    def partial(self, listen_id: str, language: str | None = None, force: bool = False) -> dict[str, Any]:
        """Transcribe the utterance so far — the text that appears as you speak."""
        session = self.get(listen_id)
        if session is None:
            raise KeyError(listen_id)
        with session.lock:
            pcm = bytes(session.pcm)
            already = session.partial_text
            audio_ms = duration_ms(pcm)
            due = force or not already or audio_ms - session.last_partial_audio_ms >= MIN_PARTIAL_GAP_MS
            if not due or session.decoding:
                return {
                    "text": already,
                    "ms": 0,
                    "audio_ms": audio_ms,
                    "partials": session.partials,
                    "first_partial_ms": session.first_partial_ms,
                    "skipped": True,
                    "silent": not already,
                }
            session.decoding = True
        try:
            started = time.monotonic()
            trimmed = trim_silence(pcm)
            if duration_ms(trimmed) < MIN_PARTIAL_VOICED_MS:
                return {
                    "text": already,
                    "ms": 0,
                    "audio_ms": audio_ms,
                    "partials": session.partials,
                    "first_partial_ms": session.first_partial_ms,
                    "silent": not already,
                }
            text = _transcribe(trimmed, partial_model_name(), language)
            elapsed = int((time.monotonic() - started) * 1000)
            if len(text.split()) <= 2 and duration_ms(trimmed) < MIN_PARTIAL_ALONE_MS:
                text = ""
        finally:
            with session.lock:
                session.decoding = False
        with session.lock:
            if text:
                session.partial_text = text
            session.partials += 1
            session.last_partial_audio_ms = audio_ms
            if session.first_partial_ms is None:
                session.first_partial_ms = elapsed
            first_at = session.created_at
            session.touched_at = time.monotonic()
        return {
            "text": text or already,
            "ms": elapsed,
            "audio_ms": audio_ms,
            "since_start_ms": int((time.monotonic() - first_at) * 1000),
            "partials": session.partials,
            "first_partial_ms": session.first_partial_ms,
            "silent": False,
        }

    def finish(self, listen_id: str, language: str | None = None) -> dict[str, Any]:
        """The transcript the turn is built from, and how long the waiting cost."""
        session = self.get(listen_id)
        if session is None:
            raise KeyError(listen_id)
        with session.lock:
            pcm = bytes(session.pcm)
        started = time.monotonic()
        trimmed = trim_silence(pcm)
        if not trimmed:
            with self._lock:
                self._sessions.pop(listen_id, None)
            return {"text": "", "ms": 0, "audio_ms": duration_ms(pcm), "silent": True}
        text = _transcribe(trimmed, final_model_name(), language)
        elapsed = int((time.monotonic() - started) * 1000)
        with session.lock:
            session.final_text = text
            session.touched_at = time.monotonic()
        with self._lock:
            self._sessions.pop(listen_id, None)
        if not self._final_warm:
            self._final_warm = True
            threading.Thread(
                target=self._warm, args=((final_model_name(),),), name="hermus-voice-final-warm", daemon=True
            ).start()
        return {
            "text": text,
            "ms": elapsed,
            "audio_ms": duration_ms(pcm),
            "partial": session.partial_text,
            "first_partial_ms": session.first_partial_ms,
            "silent": not bool(text),
        }

    def cancel(self, listen_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(str(listen_id or ""), None) is not None

    def _evict(self) -> None:
        cutoff = time.monotonic() - SESSION_IDLE_SECONDS
        with self._lock:
            for key, session in list(self._sessions.items()):
                if session.touched_at < cutoff:
                    self._sessions.pop(key, None)


listen_manager = ListenManager()

__all__ = [
    "ListenManager",
    "ListenSession",
    "PCM_RATE",
    "SpeechToTextUnavailableError",
    "duration_ms",
    "final_model_name",
    "listen_manager",
    "partial_model_name",
    "pcm_to_wav",
    "stt_status",
    "trim_silence",
]
