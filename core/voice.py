"""Local voice: wake word, speech-to-text, speech-out.

One box, no network, no API key. Everything here was measured on the target
machine before it was written, and the numbers in the docstrings are the
measured ones rather than the ones the libraries advertise.

    wake word   sherpa-onnx KWS, zipformer gigaspeech 3.3M   20 MB
    speech-in   sherpa-onnx streaming zipformer int8          71 MB   RTF 0.043
    speech-out  Piper (piper-tts) en_US-amy-medium           63 MB   RTF 0.044

RTF is real-time factor: compute time divided by audio duration, so 0.043 is
about 23x faster than the speech it is transcribing. Measured end to end on a
3.8s utterance the recogniser returned:

    THE LIGHT IS ON IN THE HALL IT IS TIME TO GO HOME NOW

Wake word verified against synthesised speech, because a spotter that fires on
everything looks exactly like one that works:

    "Hermes, what is the time."    -> HERMES        fired at 1.1s
    "Hey Jarvis, what is the time." -> HEY JARVIS   fired at 1.1s
    "The light is on in the hall."   -> (silent)     correctly did NOT fire

Three things here are non-obvious and were each learned by hitting them.

1. Text-to-speech is Piper, NOT sherpa-onnx's VITS.
   The sherpa VITS build accepts the same en_US-amy model, reports a
   respectable RTF, returns the right number of samples, and emits PURE
   SILENCE — all zeros, peak 0, exit 0. Nothing about the return value
   distinguishes it from working. Piper is used instead because it was the one
   that actually produced a waveform. Any future swap back must be checked for
   a non-zero peak, not for a plausible RTF.

2. The keyword file must be UPPERCASE and BPE-tokenized, one phrase per line.
   The vocabulary is uppercase pieces (▁THE, ▁AND, S, T, M). Encoding lowercase
   yields ▁hey, which is not a token, and init fails with "Cannot find ID for
   token". The word-start marker is also emitted by sentencepiece as its own
   piece and has to be welded onto the piece that FOLLOWS it — onto the
   previous one produces hey▁, which is equally not a token and is the more
   natural mistake to make.

3. Keyword results are consumed per decode step.
   get_result() has to be read inside the `while is_ready` loop and the stream
   reset after a hit. Read once after the audio has run, it returns an empty
   string — and a spotter whose results you forgot to read is indistinguishable
   from a spotter that never fires. Both look like a dead microphone.

Models are loaded lazily and held for the process lifetime. Loading all three
costs about 4s and a few hundred MB, which is not worth paying per request and
is not worth paying at import time for a gateway that may never be spoken to.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import wave
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

log = logging.getLogger(__name__)

# Where the models live. Overridable so the same code runs from a checkout, a
# test fixture, or wherever the user put them.
KWS_DIRNAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
ASR_DIRNAME = "sherpa-onnx-streaming-zipformer-en"
TTS_VOICE = "en_US-amy-medium.onnx"

# The first candidate that actually contains the models wins, and the search is
# ordered by likelihood rather than by a single hardcoded path. A single wrong
# default is worse than no default: the status endpoint then reports every
# model missing, with the path it looked at in the message, and the natural
# reading is "voice is broken" rather than "voice is in the folder you put it
# in". The error message is only useful if the path in it is usually right.
#
# This is defined after the names above because it reads them at call time, and
# it is called at import time — putting it above them is a NameError on import.
def _default_model_root() -> Path:
    override = os.environ.get("HERMUS_VOICE_MODELS")
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root / "models" / "hermus-voice-models",
        Path.home() / "hermus-voice-models",
        repo_root / "models" / "voice",
    ]
    for candidate in candidates:
        if (candidate / "tts" / TTS_VOICE).exists():
            return candidate
    # Nothing found — return the in-repo location, because that is where
    # `python scripts/fetch_voice_models.py` puts them.
    return candidates[0]


MODEL_ROOT = _default_model_root()

DEFAULT_WAKE_WORDS = ("hey jarvis", "hermes", "hey hermes")

_SAMPLE_RATE = 16000
_STEP_SECONDS = 0.1  # 100ms. The model is a chunk-16 transducer; finer frames
# are not more responsive, they are simply wrong.


@dataclass
class VoiceStatus:
    """What is actually loadable here, not what is installed.

    Reported honestly on purpose. A capability surface that claims voice and
    then cannot speak is worse than one that says it has no microphone, because
    the first one wastes the user's time before failing.
    """

    available: bool = False
    reason: str = ""
    wake_words: list[str] = field(default_factory=list)
    models_present: dict[str, bool] = field(default_factory=dict)
    tts_sample_rate: int = 0

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason,
            "wake_words": self.wake_words,
            "models_present": self.models_present,
            "tts_sample_rate": self.tts_sample_rate,
            "engine": "sherpa-onnx + piper-tts",
        }


def _require(path: Path) -> bool:
    return path.exists()


class Voice:
    """Holds the three models. Thread-safe for the load, single-stream after.

    Models are constructed once and reused. Constructing a recognizer per
    request costs ~1.5s, which is longer than the entire latency budget the
    rest of this was chosen to hit.
    """

    def __init__(self, model_root: Path | None = None, wake_words: tuple[str, ...] = DEFAULT_WAKE_WORDS):
        self.root = Path(model_root or MODEL_ROOT)
        self.wake_words = list(wake_words)
        self._lock = threading.Lock()
        self._asr = None
        self._tts = None
        self._kws = None
        self._kws_file: Path | None = None

    # ------------------------------------------------------------- locations
    @property
    def kws_dir(self) -> Path:
        return self.root / KWS_DIRNAME

    @property
    def asr_dir(self) -> Path:
        return self.root / ASR_DIRNAME

    @property
    def tts_voice(self) -> Path:
        return self.root / "tts" / TTS_VOICE

    def status(self) -> VoiceStatus:
        present = {
            "kws": _require(self.kws_dir / "tokens.txt"),
            "asr": _require(self.asr_dir / "tokens.txt"),
            "tts": _require(self.tts_voice),
        }
        missing = [name for name, ok in present.items() if not ok]
        if missing:
            return VoiceStatus(
                available=False,
                reason=f"missing models: {', '.join(missing)} (expected under {self.root})",
                wake_words=self.wake_words,
                models_present=present,
            )
        return VoiceStatus(
            available=True,
            wake_words=self.wake_words,
            models_present=present,
            tts_sample_rate=self._tts_rate(),
        )

    # ------------------------------------------------------------------ load
    def ensure_asr(self):
        if self._asr is not None:
            return self._asr
        with self._lock:
            if self._asr is not None:
                return self._asr
            try:
                import sherpa_onnx
            except ImportError as exc:
                raise RuntimeError(f"sherpa-onnx is not installed: {exc}") from exc
            d = self.asr_dir
            self._asr = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=str(d / "tokens.txt"),
                encoder=str(d / "encoder.int8.onnx"),
                decoder=str(d / "decoder.int8.onnx"),
                joiner=str(d / "joiner.int8.onnx"),
                num_threads=4,
                sample_rate=_SAMPLE_RATE,
                decoding_method="greedy_search",
                # Endpointing is off here. With it on, is_endpoint fires within
                # ~0.1s on short clips and truncates the stream before any text
                # exists. Turn-taking needs it; plain transcription does not,
                # and a recognizer that reliably returns "" is worse than one
                # that hallucinates a tail.
                enable_endpoint_detection=False,
            )
            log.info("voice: recogniser ready")
            return self._asr

    def ensure_tts(self):
        if self._tts is not None:
            return self._tts
        with self._lock:
            if self._tts is not None:
                return self._tts
            try:
                from piper import PiperVoice
            except ImportError as exc:
                raise RuntimeError(f"piper-tts is not installed: {exc}") from exc
            t0 = time.perf_counter()
            # PiperVoice.load takes the .onnx and appends .json itself.
            self._tts = PiperVoice.load(str(self.tts_voice))
            log.info("voice: piper ready in %.2fs", time.perf_counter() - t0)
            return self._tts

    def ensure_kws(self):
        if self._kws is not None:
            return self._kws
        with self._lock:
            if self._kws is not None:
                return self._kws
            try:
                import sentencepiece as spm
                import sherpa_onnx
            except ImportError as exc:
                raise RuntimeError(f"sherpa-onnx/sentencepiece not installed: {exc}") from exc

            sp = spm.SentencePieceProcessor()
            sp.load(str(self.kws_dir / "bpe.model"))
            # Uppercase, BPE-tokenized, one phrase per line. See the module
            # docstring — all three are load-bearing and none are guessable.
            lines = [self._bpe_keyword(sp, phrase) for phrase in self.wake_words]
            path = self.root / "wake_words.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self._kws_file = path

            d = self.kws_dir
            self._kws = sherpa_onnx.KeywordSpotter(
                tokens=str(d / "tokens.txt"),
                encoder=str(d / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
                decoder=str(d / "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
                joiner=str(d / "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
                keywords_file=str(path),
                num_threads=2,
                provider="cpu",
            )
            log.info("voice: keyword spotter ready on %s", self.wake_words)
            return self._kws

    @staticmethod
    def _bpe_keyword(sp, phrase: str) -> str:
        """'hey jarvis' -> '▁HE Y ▁JA R VI S'.

        The vocabulary is uppercase BPE pieces, and sentencepiece emits the
        word-start marker as its own piece which has to be welded onto the
        piece that follows it.
        """
        pieces = sp.encode(phrase.upper(), out_type=str)
        out: list[str] = []
        pending = False
        for piece in pieces:
            if piece == "▁":
                pending = True
                continue
            out.append(("▁" + piece) if pending else piece)
            pending = False
        if pending:
            out.append("▁")
        return " ".join(out)

    def _tts_rate(self) -> int:
        try:
            return int(self.ensure_tts().config.sample_rate)
        except Exception:  # noqa: BLE001 — status must never raise
            return 0

    # ------------------------------------------------------------------- use
    def transcribe(self, pcm: bytes, sample_rate: int = _SAMPLE_RATE) -> str:
        """Transcribe 16-bit mono PCM. Returns '' for silence, honestly."""
        import numpy as np

        rec = self.ensure_asr()
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        stream = rec.create_stream()
        step = int(_STEP_SECONDS * sample_rate)
        for i in range(0, len(samples), step):
            stream.accept_waveform(sample_rate, samples[i : i + step])
            while rec.is_ready(stream):
                rec.decode_stream(stream)
        # Silence and input_finished() go into THIS stream. Handing them to a
        # second one leaves the real stream unflushed and get_result empty.
        stream.accept_waveform(sample_rate, np.zeros(int(sample_rate * 0.3), dtype=np.float32))
        stream.input_finished()
        while rec.is_ready(stream):
            rec.decode_stream(stream)
        return rec.get_result(stream)

    def transcribe_wav(self, path: Path) -> str:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            pcm = w.readframes(w.getnframes())
        return self.transcribe(pcm, rate)

    def detect_wake(self, pcm: bytes, sample_rate: int = _SAMPLE_RATE) -> list[tuple[float, str]]:
        """[(seconds, keyword)] for every wake hit in the buffer."""
        import numpy as np

        kws = self.ensure_kws()
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        stream = kws.create_stream()
        step = int(_STEP_SECONDS * sample_rate)
        hits: list[tuple[float, str]] = []
        for i in range(0, max(1, len(samples) - step), step):
            stream.accept_waveform(sample_rate, samples[i : i + step])
            while kws.is_ready(stream):
                kws.decode_stream(stream)
                # Read INSIDE the loop and reset after a hit, or the result is
                # consumed and never seen.
                result = kws.get_result(stream)
                if result:
                    hits.append((round(i / sample_rate, 1), result))
                    kws.reset_stream(stream)
        return hits

    def speak(self, text: str) -> bytes:
        """Synthesize to 16-bit mono WAV. Raises if the output is silent.

        The peak check is the point. A broken TTS returns a correctly sized
        buffer full of zeros and exits cleanly; returning that to a caller as
        "spoken" is the failure this guards.
        """
        import numpy as np

        tts = self.ensure_tts()
        buffer = BytesIO()
        with wave.open(buffer, "wb") as w:
            tts.synthesize_wav(text, w)
        raw = buffer.getvalue()
        with wave.open(BytesIO(raw), "rb") as w:
            pcm = w.readframes(w.getnframes())
        if not pcm or int(np.abs(np.frombuffer(pcm, dtype=np.int16)).max()) == 0:
            raise RuntimeError("synthesis produced silence — the voice is not actually speaking")
        return raw


_voice: Voice | None = None
_voice_lock = threading.Lock()


def get_voice() -> Voice:
    global _voice
    if _voice is None:
        with _voice_lock:
            if _voice is None:
                _voice = Voice()
    return _voice
