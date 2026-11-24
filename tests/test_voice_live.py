"""Push-to-talk and spoken replies: the pipeline, the degradation chain, the cut-off.

Everything here runs against checked-in fixture WAVs. Nothing opens a
microphone, and nothing reaches the network: the tests that need a real model
skip when the weights are not already on disk, which is the same honesty the
product applies to a person's first press.
"""

from __future__ import annotations

import re
import threading
import time
import wave
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "voice"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def fixture_pcm(name: str) -> bytes:
    with wave.open(str(FIXTURES / f"{name}.wav")) as handle:
        assert handle.getframerate() == 16000
        assert handle.getnchannels() == 1
        return handle.readframes(handle.getnframes())


def frames(pcm: bytes, ms: int = 100) -> list[bytes]:
    step = 16000 * 2 * ms // 1000
    return [pcm[i : i + step] for i in range(0, len(pcm), step)]


def whisper_ready(model: str | None = None) -> bool:
    pytest.importorskip("faster_whisper", reason="faster-whisper is an optional local install")
    from core.voice.listen import _model_cached, partial_model_name

    return _model_cached(model or partial_model_name())


@pytest.fixture
def gateway(monkeypatch):
    """A gateway client whose speech backend is stubbed, like tests/test_voice_mode."""
    import core.speech as speech_mod

    class CaptureEngine:
        def __init__(self):
            self.spoken: list[str] = []

        def synthesize(self, text, *a, **kw):
            self.spoken.append(text)
            return {"success": True, "audio_id": "a" * 32, "backend": "piper", "estimated_duration": 0.8}

        def status(self):
            return {"available": True, "backend": "piper", "setup": None}

    engine = CaptureEngine()
    monkeypatch.setattr(speech_mod, "speech_engine", engine)

    from fastapi.testclient import TestClient

    import gateway.gateway as gw

    with TestClient(gw.app) as client:
        client.engine = engine  # type: ignore[attr-defined]
        yield client


# --------------------------------------------------------------------------- #
# the microphone: fixture audio in, text out
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not whisper_ready(), reason="no local speech model on this machine")
def test_partial_transcript_appears_while_the_person_is_still_speaking():
    """The live partial is the whole point of a push-to-talk control."""
    from core.voice.listen import listen_manager

    listen = listen_manager.start("test-partials")
    pcm = fixture_pcm("fixture_long")
    partials = []
    listened = 0
    for index, block in enumerate(frames(pcm)):
        listen_manager.append(listen["id"], block)
        listened += len(block)
        if (index + 1) % 7 == 0 or index == len(frames(pcm)) - 1:
            result = listen_manager.partial(listen["id"])
            assert listened < len(pcm) or index == len(frames(pcm)) - 1
            if result["text"]:
                partials.append(result["text"])
    listen_manager.cancel(listen["id"])
    assert partials, "no partial text was produced before the utterance ended"
    assert "gigabytes" in partials[-1].lower() or "gigabyte" in partials[-1].lower()


@pytest.mark.skipif(not whisper_ready(), reason="no local speech model on this machine")
def test_final_transcript_comes_from_fixture_audio():
    from core.voice.listen import listen_manager

    listen = listen_manager.start("test-final")
    for block in frames(fixture_pcm("fixture_point")):
        listen_manager.append(listen["id"], block)
    final = listen_manager.finish(listen["id"])
    assert "audio device" in final["text"].lower()
    assert final["ms"] >= 0


@pytest.mark.skipif(not whisper_ready(), reason="no local speech model on this machine")
def test_silence_produces_no_transcript():
    """A press with nothing said must not invent a turn."""
    from core.voice.listen import listen_manager

    listen = listen_manager.start("test-silence")
    for block in frames(fixture_pcm("fixture_silence")):
        listen_manager.append(listen["id"], block)
    final = listen_manager.finish(listen["id"])
    assert final["text"] == ""
    assert final["silent"] is True


def test_trim_silence_keeps_the_words_and_drops_the_quiet():
    from core.voice.listen import duration_ms, trim_silence

    speech = fixture_pcm("fixture_ask")
    padded = b"\x00\x00" * 16000 + speech + b"\x00\x00" * 16000
    trimmed = trim_silence(padded)
    assert 0 < duration_ms(trimmed) < duration_ms(padded) - 500
    assert trim_silence(b"\x00\x00" * 16000) == b""


def test_a_chunk_for_an_unknown_listen_is_refused(gateway):
    response = gateway.post("/voice/live/chunk", content=b"\x00\x00" * 100, headers={"X-Hermus-Listen": "nope"})
    assert response.status_code == 404


def test_chunks_need_the_listen_header(gateway):
    response = gateway.post("/voice/live/chunk", content=b"\x00\x00" * 10)
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# the language the partial is decoded in
# --------------------------------------------------------------------------- #
def test_the_rooms_language_reaches_the_decoder_without_detecting(monkeypatch):
    """The room says "en-US", so the partial never pays for detection.

    Detection is a second pass over the audio — measured at ~190 ms of a ~380 ms
    partial on this PC — and on two English fragments it guessed Turkish and
    Hindi instead. A hint makes both problems disappear.
    """
    import core.voice.listen as listen_mod

    monkeypatch.setattr(listen_mod, "_whisper_installed", lambda: True)
    monkeypatch.setattr(listen_mod, "warm_models", lambda names=None: None)
    seen: list = []

    def fake_transcribe(pcm, model_name, language):
        seen.append(language)
        return "the transcript"

    monkeypatch.setattr(listen_mod, "_transcribe", fake_transcribe)
    manager = listen_mod.ListenManager()
    opened = manager.start("s1", "en-US")
    assert opened["language"] == "en"
    manager.append(opened["id"], b"" * 16000)  # 1 s, loud enough to count
    manager.partial(opened["id"])
    seen.clear()
    manager.finish(opened["id"])
    assert seen == ["en"], "the committed transcript keeps the room's language too"


def test_without_a_hint_every_pass_lets_the_model_decide(monkeypatch):
    """A fragment's guess is not reused: it was wrong on real English.

    Pinning what a 700 ms partial detected turned the next one into Turkish text
    on this machine, so with no hint each pass detects for itself and only the
    room's own locale can turn that off.
    """
    import core.voice.listen as listen_mod

    monkeypatch.setattr(listen_mod, "_whisper_installed", lambda: True)
    monkeypatch.setattr(listen_mod, "warm_models", lambda names=None: None)
    seen: list = []

    def fake_transcribe(pcm, model_name, language):
        seen.append(language)
        return "hello there friend"

    monkeypatch.setattr(listen_mod, "_transcribe", fake_transcribe)
    manager = listen_mod.ListenManager()
    opened = manager.start("s1", "")
    assert opened["language"] is None
    for _ in range(3):
        manager.append(opened["id"], b"" * 16000)
        manager.partial(opened["id"], force=True)
    assert seen == [""] * 3, "no hint means every pass asks the model, and nothing is pinned"


def test_a_language_hint_is_reduced_to_the_code_whisper_expects():
    from core.voice.listen import _language_hint

    assert _language_hint("en-US") == "en"
    assert _language_hint("pt_BR") == "pt"
    assert _language_hint(" HI ") == "hi"
    assert _language_hint("") == ""
    assert _language_hint(None) == ""


def test_the_committed_transcript_uses_the_faster_model_by_default():
    """The partial is what has to feel instant; base is the commit default.

    `small` is still the most accurate on CPU and is one variable away, so this
    pins the trade rather than hiding it.
    """
    from core.voice.listen import final_model_name

    assert final_model_name() == "base"


# --------------------------------------------------------------------------- #
# the degradation chain tells the truth
# --------------------------------------------------------------------------- #
def test_missing_speech_to_text_disables_the_mic_with_the_fix(monkeypatch):
    import core.voice.listen as listen_mod

    monkeypatch.setattr(listen_mod, "_whisper_installed", lambda: False)
    from core.voice.listen import stt_status

    status = stt_status()
    assert status["available"] is False
    assert "pip install faster-whisper" in status["fix"]
    assert status["reason"]


def test_the_gateway_refuses_a_listen_with_the_command_that_would_work(gateway, monkeypatch):
    import core.voice.listen as listen_mod

    monkeypatch.setattr(listen_mod, "_whisper_installed", lambda: False)
    response = gateway.post("/voice/live/start", json={"session_id": "s1"})
    assert response.status_code == 503
    body = response.json()
    assert "pip install faster-whisper" in body["fix"]
    assert body["error"]


def test_status_is_honest_about_both_directions(gateway, monkeypatch):
    import core.voice.listen as listen_mod
    import gateway.routes_voice_live as live_mod

    monkeypatch.setattr(listen_mod, "_whisper_installed", lambda: False)
    monkeypatch.setattr(
        live_mod,
        "_tts_status",
        lambda: {
            "available": False,
            "backend": None,
            "reason": "No local voice is installed, so answers stay on screen",
            "fix": "python -m piper.download_voices en_US-lessac-medium --download-dir data/speech/voices",
        },
    )
    body = gateway.get("/voice/live/status").json()
    assert body["stt"]["available"] is False
    assert body["tts"]["available"] is False
    assert "piper.download_voices" in body["tts"]["fix"]
    assert body["truth"]["mic"] and body["truth"]["speech"]


def test_missing_voice_reports_the_one_command_that_installs_one(monkeypatch, tmp_path):
    """No voice on disk: the status names the command, not an error code."""
    import core.speech as speech_mod

    engine = speech_mod.SpeechEngine(root=tmp_path)
    monkeypatch.setenv("HERMUS_TTS_BACKEND", "piper")
    monkeypatch.setenv("HERMUS_PIPER_MODEL", "")
    monkeypatch.setenv("HERMUS_PIPER_VOICE_DIR", str(tmp_path / "voices"))
    monkeypatch.setattr(speech_mod.shutil, "which", lambda name: "/usr/bin/piper" if name == "piper" else None)
    monkeypatch.setattr(speech_mod, "_module_available", lambda name: name == "piper")
    monkeypatch.setattr(speech_mod, "find_library", lambda name: None)
    monkeypatch.setattr(engine, "_omnivoice_status", lambda: {"available": False})

    status = engine.status()
    assert status["available"] is False
    assert "piper.download_voices" in status["setup"]


def test_a_voice_on_disk_is_found_without_a_setting(monkeypatch, tmp_path):
    import core.speech as speech_mod

    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "en_US-lessac-medium.onnx").write_bytes(b"stub")
    monkeypatch.setenv("HERMUS_PIPER_MODEL", "")
    monkeypatch.setenv("HERMUS_PIPER_VOICE_DIR", str(voices))
    engine = speech_mod.SpeechEngine(root=tmp_path)
    assert engine.piper_model() == voices / "en_US-lessac-medium.onnx"


def test_the_room_disables_the_mic_in_markup_before_any_click():
    """The control is disabled until the status says otherwise, never a dead button."""
    root = Path(__file__).resolve().parent.parent
    html = (root / "gateway/static/companion.html").read_text(encoding="utf-8")
    mic = next(tag for tag in re.findall(r"<button[^>]*>", html) if 'id="mic"' in tag)
    assert 'aria-disabled="true"' in mic
    script = (root / "gateway/static/companion.js").read_text(encoding="utf-8")
    assert "/voice/live/status" in script
    assert "voice.stt.available" in script
    assert 'under("voice needs ' in script


# --------------------------------------------------------------------------- #
# speaking, and stopping mid-sentence
# --------------------------------------------------------------------------- #
def test_speak_returns_a_clip_per_sentence(gateway):
    response = gateway.post("/voice/speak", json={"text": "First thing. Second thing. Third thing.", "session_id": "s1"})
    assert response.status_code == 200
    body = response.json()
    assert body["spoken"] is True
    assert len(body["clips"]) == 3
    assert body["clips"][0]["audio_url"].startswith("/speech/audio/")


def test_interrupt_makes_an_in_flight_clip_stale(gateway, monkeypatch):
    """Pressing stop while a sentence is being synthesized must win."""
    import core.speech as speech_mod
    from core.voice.speak import speech_director

    slow_started = threading.Event()

    class SlowEngine:
        def synthesize(self, text, *a, **kw):
            slow_started.set()
            time.sleep(0.35)
            return {"success": True, "audio_id": "b" * 32, "backend": "piper"}

    monkeypatch.setattr(speech_mod, "speech_engine", SlowEngine())
    result: dict = {}

    def run():
        result.update(speech_director.speak("This sentence is still being rendered.", session_id="intr"))

    worker = threading.Thread(target=run)
    worker.start()
    slow_started.wait(2)
    speech_director.interrupt("intr")
    worker.join(5)

    assert result["spoken"] is True
    assert result["stale"] is True


def test_stop_endpoint_raises_the_generation(gateway):
    first = gateway.post("/voice/speak/stop", json={"session_id": "s2"}).json()
    second = gateway.post("/voice/speak/stop", json={"session_id": "s2"}).json()
    assert second["output_generation"] == first["output_generation"] + 1


def test_speaking_state_follows_real_playback_not_a_timer(gateway):
    from core.voice.speak import speech_director

    started = gateway.post("/voice/live/playing", json={"session_id": "s3", "playing": True}).json()
    assert started["playing"] is True
    assert speech_director.is_speaking("s3") is True
    ended = gateway.post("/voice/live/playing", json={"session_id": "s3", "playing": False}).json()
    assert ended["playing"] is False
    assert speech_director.is_speaking("s3") is False


def test_a_missing_voice_degrades_speak_to_an_honest_refusal(gateway, monkeypatch):
    import core.speech as speech_mod

    class NoEngine:
        def synthesize(self, text, *a, **kw):
            return {"success": False, "error": "No local TTS backend is configured"}

        def status(self):
            return {"available": False, "backend": None, "setup": "pip install piper-tts"}

    monkeypatch.setattr(speech_mod, "speech_engine", NoEngine())
    response = gateway.post("/voice/speak", json={"text": "Hello there.", "session_id": "s4"})
    assert response.status_code == 503
    assert "No local TTS backend" in response.json()["error"]


# --------------------------------------------------------------------------- #
# secrets never leave the machine as sound
# --------------------------------------------------------------------------- #
def test_secrets_are_redacted_before_any_audio_exists(gateway):
    text = "Your password is hunter2 and the key is sk-abcdefghijklmnop123 plus 4111 1111 1111 1111."
    body = gateway.post("/voice/speak", json={"text": text, "session_id": "s5"}).json()
    assert body["spoken"] is True
    assert body["redacted"] is True
    spoken = " ".join(clip["text"] for clip in body["clips"])
    for secret in ("hunter2", "sk-abcdefghijklmnop123", "4111 1111 1111 1111"):
        assert secret not in spoken
    assert "[hidden]" in spoken


def test_clean_text_is_not_marked_redacted():
    from core.voice.speak import redact_for_speech

    text, redacted = redact_for_speech("You have nineteen gigabytes free on the system drive.")
    assert redacted is False
    assert text.startswith("You have nineteen")


def test_a_secret_shaped_sentence_still_says_something():
    """Redaction replaces the secret, it does not silence the whole answer."""
    from core.voice.speak import redact_for_speech

    text, redacted = redact_for_speech("The api key is abcdef123456, so rotate it.")
    assert redacted is True
    assert "abcdef123456" not in text
    assert "rotate it" in text


# --------------------------------------------------------------------------- #
# real local speech, when this machine has the voice
# --------------------------------------------------------------------------- #
def test_local_voice_synthesizes_a_playable_wav(tmp_path, monkeypatch):
    """Speech out, measured: a real WAV with real audio in it."""
    import core.speech as speech_mod

    engine = speech_mod.SpeechEngine(root=tmp_path)
    status = engine.status()
    if not status["available"]:
        pytest.skip(f"no local TTS backend on this machine: {status.get('setup')}")
    result = engine.synthesize("Speech out works on this machine.")
    assert result["success"] is True
    path = Path(result["path"])
    with wave.open(str(path)) as handle:
        assert handle.getnframes() > 8000
        assert handle.getframerate() >= 8000
    assert result["generation_ms"] >= 0
