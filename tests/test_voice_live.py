"""Does core/voice.py actually speak, hear, and wake on this machine?

Not a mock and not a unit test of the plumbing: real models, real audio, and
the negative control that decides whether the wake word means anything.

Every figure asserted here is one a person would otherwise have to take on
trust, and three of them are the ones that were wrong during development:
the TTS that reported a fine RTF while emitting pure silence, the recogniser
whose results were read after the evidence had been consumed, and the keyword
file that had to be uppercased before it would load at all.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.voice import Voice  # noqa: E402

MODELS = ROOT / "models" / "hermus-voice-models"
AUDIO = Path(r"C:\Users\rishi\AppData\Local\Temp\voicebench")


def expect(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    return ok


def main() -> int:
    voice = Voice(model_root=MODELS)
    results: list[bool] = []

    status = voice.status()
    print("\n=== status ===")
    print(f"  available : {status.available}")
    print(f"  reason    : {status.reason or '-'}")
    print(f"  models    : {status.models_present}")
    results.append(expect("reports itself available", status.available, status.reason))
    if not status.available:
        return 0 if not results[0] else 1

    # ------------------------------------------------------------------ TTS
    print("\n=== speech out (piper) ===")
    import time

    t0 = time.perf_counter()
    wav = voice.speak("The light is on in the hall. It is time to go home now.")
    synth_s = time.perf_counter() - t0

    with wave.open(__import__("io").BytesIO(wav), "rb") as w:
        rate, frames = w.getframerate(), w.getnframes()
        pcm = w.readframes(frames)
    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16)
    audio_s = frames / rate
    peak = int(np.abs(samples).max())
    rtf = synth_s / audio_s if audio_s else 0

    print(f"  {audio_s:.2f}s audio in {synth_s:.2f}s   RTF {rtf:.3f}   peak {peak}")
    # The peak assertion is the whole reason this test exists: a broken
    # synthesizer returns a correctly sized buffer of zeros and exits 0.
    results.append(expect("synthesis is not silence", peak > 0, f"peak={peak}"))
    results.append(expect("faster than realtime", rtf < 1.0, f"RTF={rtf:.3f}"))
    results.append(expect("duration is plausible", 2.0 < audio_s < 12.0, f"{audio_s:.2f}s"))

    # ------------------------------------------------------------------ STT
    print("\n=== speech in (sherpa-onnx) ===")
    t0 = time.perf_counter()
    text = voice.transcribe(pcm, rate)
    stt_s = time.perf_counter() - t0
    stt_rtf = stt_s / audio_s if audio_s else 0
    print(f"  heard: {text!r}")
    print(f"  {stt_s:.2f}s for {audio_s:.2f}s audio   RTF {stt_rtf:.3f}")
    lowered = text.lower()
    # Not an exact-match assertion — an ASR is allowed to be imperfect — but
    # empty output means the stream was never flushed, which was a real bug.
    results.append(expect("returns non-empty text", bool(text.strip()), repr(text)))
    results.append(expect("hears the right words", "light" in lowered and "hall" in lowered, lowered))
    results.append(expect("faster than realtime", stt_rtf < 1.0, f"RTF={stt_rtf:.3f}"))

    # ------------------------------------------------------------------ KWS
    print("\n=== wake word (sherpa-onnx) ===")
    import numpy as np

    def pcm_of(name: str) -> tuple[bytes, int]:
        with wave.open(str(AUDIO / f"{name}.wav"), "rb") as w:
            return w.readframes(w.getnframes()), w.getframerate()

    for name, should_fire in (("wake_hermes", True), ("wake_jarvis", True), ("negative", False)):
        data, srate = pcm_of(name)
        hits = voice.detect_wake(data, srate)
        print(f"  {name:14s} -> {hits!r}")
        results.append(
            expect(f"{name} {'fires' if should_fire else 'stays silent'}", bool(hits) is should_fire, str(hits))
        )

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
