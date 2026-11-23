#!/usr/bin/env python3
"""Regenerate the checked-in voice fixtures from text, without a microphone.

Run:  python scripts/voice_fixtures.py [--voice-dir data/speech/voices]

The voice tests transcribe and re-synthesize real speech, and a real speech
sample is the only thing that proves the pipeline works end to end. Recording
the person at their own microphone is not acceptable, so the fixtures are
generated once from text with the same local Piper voice the product speaks
with, then committed under ``tests/fixtures/voice``.

Requires ``piper-tts`` and an installed voice (see the speech status line for
the one command that fetches it). Exits non-zero with that line if either is
missing.
"""

from __future__ import annotations

import argparse
import io
import wave
from pathlib import Path

LINES = {
    "fixture_ask": "what can you do",
    "fixture_point": "where do I change the audio device",
    "fixture_act": "rename the screenshots on my desktop by date",
    "fixture_long": (
        "I checked the machine and it has nineteen gigabytes free on the system drive and a model download still queued"
    ),
}

TARGET_RATE = 16000


def _resample(samples: bytes, source_rate: int) -> bytes:
    import array

    data = array.array("h")
    data.frombytes(samples[: len(samples) - (len(samples) % 2)])
    if source_rate == TARGET_RATE:
        return data.tobytes()
    count = int(round(len(data) * TARGET_RATE / source_rate))
    out = array.array("h")
    for i in range(count):
        pos = i * source_rate / TARGET_RATE
        low = int(pos)
        frac = pos - low
        nxt = data[low + 1] if low + 1 < len(data) else data[low]
        out.append(int(data[low] * (1 - frac) + nxt * frac))
    return out.tobytes()


def _write(path: Path, frames: bytes) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(TARGET_RATE)
        handle.writeframes(frames)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice-dir", default="data/speech/voices")
    parser.add_argument("--out", default="tests/fixtures/voice")
    args = parser.parse_args()

    voices = sorted(Path(args.voice_dir).glob("*.onnx"))
    if not voices:
        print(
            f"No Piper voice in {args.voice_dir}. Fetch one with: python -m piper.download_voices "
            f"en_US-lessac-medium --download-dir {args.voice_dir}"
        )
        return 2
    try:
        from piper import PiperVoice
    except ImportError:
        print("piper-tts is not installed. Install it with: python -m pip install piper-tts")
        return 2

    voice = PiperVoice.load(str(voices[0]))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in LINES.items():
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            voice.synthesize_wav(text, handle)
        buffer.seek(0)
        with wave.open(buffer) as handle:
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
        _write(out / f"{name}.wav", _resample(frames, rate))
        print(f"{name}.wav  {text!r}")
    _write(out / "fixture_silence.wav", b"\x00\x00" * TARGET_RATE)
    print("fixture_silence.wav  1.0 s of silence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
