"""Voice commands belong to the NPU; the GPU stays free for the agent.

The routing invariant these tests pin:

* when the hardware plan gives the ``background`` role to NoLlama and a Whisper
  IR is on disk, ``transcribe_audio`` sends the clip to the local engine;
* every other case — CPU-only box, engine down, no Whisper download, engine
  without an audio route — falls back to faster-whisper and *says why*, so the
  user is never left with a dead microphone button.
"""
from __future__ import annotations

import json

import pytest

import core.accelerators as accel
import core.nollama as nl
import tools.voice as voice
from core.nollama import NollamaManager


@pytest.fixture
def mgr(tmp_path):
    return NollamaManager(home=tmp_path / "nollama", models_dir=tmp_path / "models")


def _write_ir(path, declared=1024, actual=4096):
    path.mkdir(parents=True, exist_ok=True)
    (path / "openvino_model.xml").write_text(
        '<net><weights><blob offset="0" size="%d"/></weights></net>' % declared, encoding="utf-8"
    )
    (path / "openvino_model.bin").write_bytes(b"x" * actual)
    return path


def _whisper_dir(mgr):
    spec = nl.CATALOG_BY_ID[nl.WHISPER_MODEL_ID]
    return _write_ir(mgr.models_dir / spec.repo.split("/")[-1])


class _Resp:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def audio(tmp_path):
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"RIFF....WAVE")
    return str(clip)


# ---------------------------------------------------------------------------
# The manager: what it advertises, and what it refuses
# ---------------------------------------------------------------------------
def test_whisper_model_is_absent_until_downloaded(mgr):
    assert mgr.whisper_model() is None


def test_whisper_model_is_found_once_the_ir_is_complete(mgr):
    _whisper_dir(mgr)
    found = mgr.whisper_model()
    assert found is not None
    assert found["name"] == "whisper-base-int8-ov"
    assert found["complete"] is True


def test_incomplete_whisper_download_is_not_offered(mgr):
    spec = nl.CATALOG_BY_ID[nl.WHISPER_MODEL_ID]
    _write_ir(mgr.models_dir / spec.repo.split("/")[-1], declared=10_000, actual=16)
    assert mgr.whisper_model() is None


def test_transcribe_refuses_when_the_engine_is_down(mgr, audio):
    result = mgr.transcribe(audio)
    assert result["success"] is False
    assert result["action"] == "start"


def test_transcribe_names_the_whisper_download_when_missing(mgr, audio, monkeypatch):
    monkeypatch.setattr(mgr, "running", lambda: True)
    result = mgr.transcribe(audio)
    assert result["success"] is False
    assert result["action"] == "download_model"
    assert result["model_id"] == nl.WHISPER_MODEL_ID


def test_transcribe_needs_a_real_file(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "running", lambda: True)
    result = mgr.transcribe("/nonexistent/clip.wav")
    assert result["success"] is False
    assert "not found" in result["error"]


def test_transcribe_posts_to_the_openai_audio_route(mgr, audio, monkeypatch):
    _whisper_dir(mgr)
    monkeypatch.setattr(mgr, "running", lambda: True)
    seen = {}

    def fake_post(url, files=None, data=None, timeout=None):
        seen["url"] = url
        seen["model"] = (data or {}).get("model")
        seen["language"] = (data or {}).get("language")
        seen["filename"] = list((files or {}).values())[0][0]
        return _Resp(200, {"text": " open the report "}, headers={"X-Device": "NPU"})

    import requests

    monkeypatch.setattr(requests, "post", fake_post)
    result = mgr.transcribe(audio, language="en")

    assert result["success"] is True
    assert result["text"] == "open the report"
    assert result["engine"] == "nollama"
    assert result["device"] == "NPU"
    assert seen["url"].endswith(nl.TRANSCRIPTION_PATH)
    assert seen["model"] == "whisper-base-int8-ov"
    assert seen["language"] == "en"
    assert seen["filename"] == "clip.wav"


def test_transcribe_falls_back_when_the_build_has_no_audio_route(mgr, audio, monkeypatch):
    _whisper_dir(mgr)
    monkeypatch.setattr(mgr, "running", lambda: True)
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(404, text="Not Found"))
    result = mgr.transcribe(audio)
    assert result["success"] is False
    assert "no transcription route" in result["error"]


def test_transcribe_reports_a_server_error_instead_of_raising(mgr, audio, monkeypatch):
    _whisper_dir(mgr)
    monkeypatch.setattr(mgr, "running", lambda: True)
    import requests

    def boom(*a, **k):
        raise ConnectionError("engine died mid-request")

    monkeypatch.setattr(requests, "post", boom)
    result = mgr.transcribe(audio)
    assert result["success"] is False
    assert "ConnectionError" in result["error"]


# ---------------------------------------------------------------------------
# The voice tool: pick the NPU when the plan says so, else the CPU
# ---------------------------------------------------------------------------
def _plan_with_background(engine):
    return {
        "mode": "pipelined" if engine == "nollama" else "cpu_only",
        "roles": {"background": {"role": "background", "engine": engine, "device": "NPU", "model": "x"}},
    }


def test_voice_skips_the_engine_when_background_runs_on_ollama(monkeypatch):
    monkeypatch.setattr(accel, "cached_plan", lambda refresh=False: _plan_with_background("ollama"))
    result = voice.local_engine_transcribe("/tmp/whatever.wav")
    assert result["success"] is False
    assert "background role" in result["error"]


class _FakeManager:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    def running(self):
        return True

    def whisper_model(self):
        return {"name": "whisper-base-int8-ov"}

    def transcribe(self, path, language=None):
        self.calls += 1
        return dict(self._result)


def test_voice_uses_the_local_engine_when_it_owns_the_role(monkeypatch, audio):
    monkeypatch.setattr(accel, "cached_plan", lambda refresh=False: _plan_with_background("nollama"))
    fake = _FakeManager({"success": True, "text": "run the nightly report", "engine": "nollama"})
    monkeypatch.setattr(nl, "nollama_manager", fake)

    result = voice.transcribe_audio(audio)
    assert fake.calls == 1
    assert result["success"] is True
    assert result["backend"] == "nollama"
    assert result["text"] == "run the nightly report"
    assert result["audio_path"] == audio


def test_voice_falls_back_to_the_cpu_and_says_why(monkeypatch, audio):
    monkeypatch.setattr(accel, "cached_plan", lambda refresh=False: _plan_with_background("nollama"))
    monkeypatch.setattr(
        nl, "nollama_manager", _FakeManager({"success": False, "error": "NoLlama is not running"})
    )
    monkeypatch.setattr(voice, "FASTER_WHISPER_AVAILABLE", False)
    monkeypatch.delenv("HERMUS_STT_BACKEND", raising=False)

    result = voice.transcribe_audio(audio)
    assert result["success"] is False
    assert result["backend"] == "faster-whisper"
    assert result["local_engine_error"] == "NoLlama is not running"


def test_pinning_the_engine_never_touches_the_cpu_path(monkeypatch, audio):
    monkeypatch.setattr(accel, "cached_plan", lambda refresh=False: _plan_with_background("nollama"))
    monkeypatch.setattr(
        nl, "nollama_manager", _FakeManager({"success": False, "error": "engine has no transcription route"})
    )
    monkeypatch.setenv("HERMUS_STT_BACKEND", "nollama")

    def no_cpu(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("faster-whisper must not be used when the engine is pinned")

    monkeypatch.setattr(voice, "_get_model", no_cpu)
    result = voice.transcribe_audio(audio)
    assert result["success"] is False
    assert result["backend"] == "nollama"


def test_missing_file_is_rejected_before_any_backend_is_consulted(monkeypatch):
    monkeypatch.setattr(accel, "cached_plan", lambda refresh=False: _plan_with_background("nollama"))
    fake = _FakeManager({"success": True, "text": "x"})
    monkeypatch.setattr(nl, "nollama_manager", fake)
    result = voice.transcribe_audio("/nonexistent/clip.wav")
    assert result["success"] is False
    assert fake.calls == 0


def test_available_models_reports_the_preferred_backend(monkeypatch):
    monkeypatch.setattr(accel, "cached_plan", lambda refresh=False: _plan_with_background("ollama"))
    info = voice.voice_available_models()
    assert info["preferred_backend"] == "faster-whisper"
    assert info["local_engine"]["assigned_to_background_role"] is False


# ---------------------------------------------------------------------------
# The gateway route the Voice panel reads
# ---------------------------------------------------------------------------
def test_transcription_status_route_reports_the_active_backend(monkeypatch):
    from fastapi.testclient import TestClient

    from gateway.gateway import app

    monkeypatch.setattr(accel, "cached_plan", lambda refresh=False: _plan_with_background("ollama"))
    body = TestClient(app).get("/speech/transcription/status").json()
    assert body["backend"] == "faster-whisper"
    assert body["local_engine"]["ready"] is False
    assert "Whisper model" in body["note"]
    assert "discovered_count" in body and "search_dirs" in body
