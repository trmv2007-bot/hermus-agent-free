"""Voice-first (Jarvis) mode: speak first, work in the background.

Covers the contract that makes the mode feel instant:
  * the acknowledgment never waits on a model call in canned mode,
  * /voice/command returns 202 with a job handle plus a ready-to-play ack,
  * the voice.reply handler synthesizes the answer and emits voice_answer,
  * a TTS or STT failure degrades instead of erroring the turn.
"""
from __future__ import annotations

import pytest

from core.config import config
from gateway import routes_voice


# --------------------------------------------------------------------------- #
# acknowledgment
# --------------------------------------------------------------------------- #
def test_pick_ack_comes_from_the_configured_pool():
    original = config.voice_ack_phrases
    config.voice_ack_phrases = "Alpha.|Beta.|Gamma."
    try:
        for _ in range(20):
            assert routes_voice.pick_ack("do the thing") in ("Alpha.", "Beta.", "Gamma.")
    finally:
        config.voice_ack_phrases = original


def test_pick_ack_falls_back_when_the_pool_is_empty():
    original = config.voice_ack_phrases
    config.voice_ack_phrases = "  |  | "
    try:
        assert routes_voice.pick_ack() == "On it."
    finally:
        config.voice_ack_phrases = original


def test_canned_ack_never_calls_the_model(monkeypatch):
    """The whole point of canned mode: no model, no 20K-token prefill."""
    import asyncio

    def boom(*a, **kw):
        raise AssertionError("canned ack mode must not call the model")

    monkeypatch.setattr(routes_voice, "_llm_ack", boom)
    monkeypatch.setattr(routes_voice, "synthesize_speech",
                        lambda text: {"spoken": False, "text": text, "audio_url": None})
    original = config.voice_ack_mode
    config.voice_ack_mode = "canned"
    try:
        result = asyncio.run(routes_voice._make_ack("what time is it"))
    finally:
        config.voice_ack_mode = original
    assert result["mode"] == "canned"
    assert result["text"]


def test_llm_ack_falls_back_to_a_phrase_when_the_model_is_down(monkeypatch):
    monkeypatch.setattr(routes_voice, "_llm_ack", lambda text: "")
    monkeypatch.setattr(routes_voice, "synthesize_speech",
                        lambda text: {"spoken": False, "text": text, "audio_url": None})
    original = config.voice_ack_mode
    config.voice_ack_mode = "llm"
    try:
        import asyncio
        result = asyncio.run(routes_voice._make_ack("hello"))
    finally:
        config.voice_ack_mode = original
    assert result["mode"] == "llm"
    assert result["text"], "must still say something audible when the model fails"


def test_synthesize_speech_degrades_instead_of_raising(monkeypatch):
    import core.speech as speech_mod

    class BrokenEngine:
        def synthesize(self, *a, **kw):
            raise RuntimeError("no audio backend")

    monkeypatch.setattr(speech_mod, "speech_engine", BrokenEngine())
    result = routes_voice.synthesize_speech("hello")
    assert result["spoken"] is False
    assert result["audio_url"] is None
    assert "no audio backend" in result["error"]

    assert routes_voice.synthesize_speech("")["spoken"] is False


def test_synthesize_speech_returns_a_gateway_url(monkeypatch):
    import core.speech as speech_mod

    class OkEngine:
        def synthesize(self, *a, **kw):
            return {"success": True, "audio_id": "abc123", "backend": "piper",
                    "estimated_duration": 1.4, "path": "/should/not/leak"}

    monkeypatch.setattr(speech_mod, "speech_engine", OkEngine())
    result = routes_voice.synthesize_speech("hello")
    assert result["spoken"] is True
    assert result["audio_url"] == "/speech/audio/abc123"
    assert "path" not in result


# --------------------------------------------------------------------------- #
# job handler
# --------------------------------------------------------------------------- #
def test_voice_reply_handler_is_registered():
    from gateway.handlers import register_handlers

    class FakeQueue:
        def __init__(self):
            self.kinds = {}

        def register(self, kind, fn, *, overwrite=True):
            self.kinds[kind] = fn

    queue = FakeQueue()
    kinds = register_handlers(queue, agent_getter=lambda *a, **kw: None)
    assert "voice.reply" in kinds
    assert "voice" in kinds["voice.reply"]
    assert callable(queue.kinds["voice.reply"])


def test_voice_reply_handler_synthesizes_and_emits(monkeypatch):
    from gateway.handlers import make_voice_reply_handler

    monkeypatch.setattr(
        routes_voice, "synthesize_speech",
        lambda text: {"spoken": True, "text": text, "audio_url": "/speech/audio/xyz",
                      "backend": "piper"},
    )
    monkeypatch.setattr(
        "gateway.handlers._runtime_execute",
        lambda ctx, *, prefer, agent_getter: {"response": "The answer is 42."},
    )

    emitted = []

    class Ctx:
        id = "job-1"
        run_id = "run-1"
        payload = {"text": "what is 6x7", "prefer": "auto"}

        def emit(self, event_type, data=None):
            emitted.append((event_type, data))

    result = make_voice_reply_handler(lambda *a, **kw: None)(Ctx())

    assert result["answer"] == "The answer is 42."
    assert result["spoken"] is True
    assert result["speech"]["audio_url"] == "/speech/audio/xyz"
    kinds = [e[0] for e in emitted]
    assert "voice_answer" in kinds
    payload = emitted[kinds.index("voice_answer")][1]
    assert payload["text"] == "The answer is 42."
    assert payload["audio_url"] == "/speech/audio/xyz"


def test_voice_reply_handler_survives_a_tts_failure(monkeypatch):
    from gateway.handlers import make_voice_reply_handler

    def boom(text):
        raise RuntimeError("tts exploded")

    monkeypatch.setattr(routes_voice, "synthesize_speech", boom)
    monkeypatch.setattr(
        "gateway.handlers._runtime_execute",
        lambda ctx, *, prefer, agent_getter: {"response": "still fine"},
    )

    class Ctx:
        id = "job-2"
        run_id = "run-2"
        payload = {"text": "hi"}

        def emit(self, *a, **kw):
            pass

    result = make_voice_reply_handler(lambda *a, **kw: None)(Ctx())
    # The work still succeeded; only the speech degraded.
    assert result["answer"] == "still fine"
    assert result["spoken"] is False
    assert "tts exploded" in result["speech"]["error"]


def test_voice_answer_is_truncated_for_speech_only(monkeypatch):
    from gateway.handlers import make_voice_reply_handler

    spoken = {}
    monkeypatch.setattr(
        routes_voice, "synthesize_speech",
        lambda text: spoken.update(text=text) or {"spoken": True, "text": text,
                                                  "audio_url": "/speech/audio/t"},
    )
    monkeypatch.setattr(
        "gateway.handlers._runtime_execute",
        lambda ctx, *, prefer, agent_getter: {"response": "x" * 5000},
    )
    original = config.voice_answer_max_chars
    config.voice_answer_max_chars = 300
    try:
        class Ctx:
            id = "job-3"
            run_id = "run-3"
            payload = {"text": "long"}

            def emit(self, *a, **kw):
                pass

        result = make_voice_reply_handler(lambda *a, **kw: None)(Ctx())
    finally:
        config.voice_answer_max_chars = original

    assert len(spoken["text"]) == 300, "spoken clip must be shortened"
    assert len(result["answer"]) == 4000, "the transcript/result keeps the full answer"


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #
@pytest.fixture
def voice_client(monkeypatch):
    """Gateway TestClient with STT, TTS and the queue stubbed out."""
    import core.speech as speech_mod
    import tools.voice as voice_mod

    class OkEngine:
        def synthesize(self, text, *a, **kw):
            return {"success": True, "audio_id": f"id{abs(hash(text)) % 9999}",
                    "backend": "piper", "estimated_duration": 1.1}

        def status(self):
            return {"backend": "piper", "selected": "piper"}

    monkeypatch.setattr(speech_mod, "speech_engine", OkEngine())
    monkeypatch.setattr(voice_mod, "transcribe_audio",
                        lambda path, model=None, language=None, **kw:
                        {"success": True, "text": "what time is it in hyderabad"})
    monkeypatch.setattr(voice_mod, "voice_available_models",
                        lambda: {"available": True, "models": ["base"]})

    from fastapi.testclient import TestClient
    import gateway.gateway as gw

    with TestClient(gw.app) as client:
        yield client


def test_voice_status_reports_the_configuration(voice_client):
    r = voice_client.get("/voice/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["ack_mode"] == "canned"
    assert body["ack_phrases"]
    assert body["speak_answer"] is True
    assert body["tts"]["backend"] == "piper"


def test_voice_command_speaks_first_then_queues(voice_client, monkeypatch):
    submitted = {}

    class FakeJob:
        id = "job-abc"
        run_id = "run-abc"

    class FakeQueue:
        enabled = True
        _started = True

        def submit(self, kind, payload, **kw):
            submitted["kind"] = kind
            submitted["payload"] = payload
            submitted["kw"] = kw
            return FakeJob()

    monkeypatch.setattr(routes_voice, "_job_queue", FakeQueue())

    r = voice_client.post("/voice/command", content=b"fake-webm-audio",
                          headers={"Content-Type": "audio/webm"})

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["success"] is True
    assert body["queued"] is True
    assert body["transcript"] == "what time is it in hyderabad"
    # The acknowledgment is ready to play in the same response.
    assert body["ack"]["spoken"] is True
    assert body["ack"]["audio_url"].startswith("/speech/audio/")
    assert body["ack"]["mode"] == "canned"
    assert body["ack_ms"] >= 0
    # And the real work is queued behind it.
    assert submitted["kind"] == "voice.reply"
    assert submitted["payload"]["text"] == "what time is it in hyderabad"
    assert body["job_id"] == "job-abc"
    assert body["events_url"] == "/jobs/job-abc/events"
    assert body["stream_url"] == "/stream/run/run-abc"


def test_voice_command_rejects_empty_audio(voice_client):
    r = voice_client.post("/voice/command", content=b"",
                          headers={"Content-Type": "audio/webm"})
    assert r.status_code == 400
    assert "audio body required" in r.json()["error"]


def test_voice_command_rejects_oversized_audio(voice_client):
    r = voice_client.post("/voice/command", content=b"x" * (26 * 1024 * 1024),
                          headers={"Content-Type": "audio/webm"})
    assert r.status_code == 413


def test_voice_command_reports_a_transcription_failure(voice_client, monkeypatch):
    import tools.voice as voice_mod

    monkeypatch.setattr(voice_mod, "transcribe_audio",
                        lambda *a, **kw: {"success": False, "error": "no whisper model"})
    r = voice_client.post("/voice/command", content=b"audio",
                          headers={"Content-Type": "audio/webm"})
    assert r.status_code == 503
    body = r.json()
    assert body["stage"] == "transcribe"
    assert "no whisper model" in body["error"]


def test_voice_command_handles_silence(voice_client, monkeypatch):
    import tools.voice as voice_mod

    monkeypatch.setattr(voice_mod, "transcribe_audio",
                        lambda *a, **kw: {"success": True, "text": "   "})
    r = voice_client.post("/voice/command", content=b"audio",
                          headers={"Content-Type": "audio/webm"})
    assert r.status_code == 422
    assert "no speech detected" in r.json()["error"]


def test_voice_say_queues_typed_text_and_still_speaks(voice_client, monkeypatch):
    class FakeJob:
        id = "job-say"
        run_id = "run-say"

    class FakeQueue:
        enabled = True
        _started = True

        def submit(self, kind, payload, **kw):
            assert kind == "voice.reply"
            assert payload["text"] == "summarise the repo"
            return FakeJob()

    monkeypatch.setattr(routes_voice, "_job_queue", FakeQueue())
    r = voice_client.post("/voice/say", json={"text": "summarise the repo"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["queued"] is True
    assert body["transcript"] == "summarise the repo"
    assert body["ack"]["spoken"] is True
    assert body["job_id"] == "job-say"


def test_voice_say_requires_text(voice_client):
    r = voice_client.post("/voice/say", json={"text": "   "})
    assert r.status_code == 400


def test_voice_endpoints_refuse_when_disabled(voice_client, monkeypatch):
    monkeypatch.setattr(config, "voice_enabled", False)
    assert voice_client.get("/voice/status").json()["enabled"] is False
    assert voice_client.post("/voice/say", json={"text": "hi"}).status_code == 503
    r = voice_client.post("/voice/command", content=b"audio",
                          headers={"Content-Type": "audio/webm"})
    assert r.status_code == 503
    assert "HERMUS_VOICE_ENABLED" in r.json()["error"]


def test_voice_ack_endpoint_speaks_one_phrase(voice_client):
    r = voice_client.post("/voice/ack", json={"text": "One moment."})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["text"] == "One moment."
    assert body["audio_url"].startswith("/speech/audio/")


def test_voice_command_runs_inline_when_there_is_no_queue(voice_client, monkeypatch):
    class DeadQueue:
        enabled = False
        _started = False

        def submit(self, *a, **kw):
            raise RuntimeError("queue not started")

    monkeypatch.setattr(routes_voice, "_job_queue", DeadQueue())
    monkeypatch.setattr(
        routes_voice, "_run_inline",
        lambda payload: {"answer": "inline answer", "inline": True,
                         "speech": {"spoken": False, "audio_url": None}},
    )
    r = voice_client.post("/voice/say", json={"text": "hi"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] is False
    assert body["answer"] == "inline answer"
    # The acknowledgment still happened — degrading the transport must not
    # degrade the responsiveness.
    assert body["ack"]["spoken"] is True


# --------------------------------------------------------------------------- #
# client script
# --------------------------------------------------------------------------- #
def test_client_script_is_served(voice_client):
    r = voice_client.get("/static/control-client.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    body = r.text
    for symbol in ("openStream", "postVoiceBlob", "voiceCommand", "sendCommand",
                   "startRecording", "voice_answer", "STREAM_EVENT_TYPES"):
        assert symbol in body, f"client is missing {symbol}"


def test_voice_routes_are_mounted(voice_client):
    import gateway.gateway as gw

    def collect(routes, out):
        """Walk included routers (_IncludedRouter wrappers) recursively."""
        for r in routes:
            inner = getattr(r, "original_router", None)
            if inner is not None:
                collect(inner.routes, out)
                continue
            path = getattr(r, "path", None)
            if path is not None:
                out.append(path)

    found: list[str] = []
    collect(gw.app.routes, found)
    paths = set(found)
    for expected in ("/voice/status", "/voice/command", "/voice/say", "/voice/ack",
                     "/static/control-client.js"):
        assert expected in paths, f"{expected} not mounted"


# --------------------------------------------------------------------------- #
# hands-free loop configuration
# --------------------------------------------------------------------------- #
def test_handsfree_is_off_by_default():
    """An always-hot microphone must be an explicit choice, never a default."""
    assert config.voice_handsfree is False


def test_handsfree_knobs_are_all_present_and_sane():
    assert isinstance(config.voice_wake_word, str)
    assert isinstance(config.voice_wake_aliases, list)
    assert config.voice_silence_ms > config.voice_speech_ms > 0
    assert config.voice_max_utterance_ms > config.voice_min_utterance_ms > 0


def test_wake_aliases_parse_from_a_comma_separated_string():
    """A blank entry must not become an alias of "" — that would match everything."""
    from core.config import csv_list

    assert csv_list(" jervis , ,jarviss ,") == ["jervis", "jarviss"]
    assert csv_list("") == []
    assert csv_list(None) == []
    assert csv_list("jarvis") == ["jarvis"]


def test_wake_aliases_are_read_from_the_environment_at_import_time():
    """Field defaults are evaluated when the class is defined, so this has to be
    checked in a fresh interpreter rather than by constructing Config() here."""
    import os
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, HERMUS_VOICE_WAKE_ALIASES=" jervis , ,jarviss ,")
    out = subprocess.run(
        [sys.executable, "-c",
         "from core.config import config; print(config.voice_wake_aliases)"],
        cwd=root, env=env, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "['jervis', 'jarviss']"


def test_status_reports_the_handsfree_contract(voice_client):
    """The client arms itself from this payload, so it must be complete."""
    res = voice_client.get("/voice/status")
    assert res.status_code == 200
    hf = res.json()["handsfree"]
    for key in ("enabled", "wake_word", "wake_required", "wake_aliases", "silence_ms",
                "speech_ms", "min_utterance_ms", "max_utterance_ms", "barge_in"):
        assert key in hf, f"handsfree status is missing {key}"
    assert hf["silence_ms"] > hf["speech_ms"]
    assert hf["max_utterance_ms"] > hf["min_utterance_ms"]
