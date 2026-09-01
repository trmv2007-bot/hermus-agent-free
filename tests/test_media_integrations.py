from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from core.avatar import AvatarService
from core.config import config
from core.speech import SpeechEngine
import tools.voice as voice


class _FakePrompt:
    def __init__(self, marker: str = "prompt"):
        self.marker = marker

    def save(self, path: str) -> None:
        Path(path).write_bytes(self.marker.encode("utf-8"))

    @classmethod
    def load(cls, path: str) -> "_FakePrompt":
        return cls(Path(path).read_bytes().decode("utf-8") or "prompt")


class _FakeOmniModel:
    sampling_rate = 24000

    def __init__(self):
        self.last_generate = None

    def create_voice_clone_prompt(self, ref_audio: str, ref_text: str | None = None):
        self.last_prompt = {"ref_audio": ref_audio, "ref_text": ref_text}
        return _FakePrompt("cached")

    def generate(self, **kwargs):
        self.last_generate = kwargs
        return [[0] * self.sampling_rate]


class _FakeSF:
    @staticmethod
    def write(path: str, audio, sample_rate: int) -> None:
        Path(path).write_bytes(b"RIFF" + b"0" * 48)


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data=None, content: bytes = b"", text: str = ""):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeAvatarSession:
    def __init__(self):
        self.posts = []
        self.gets = []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json, timeout))
        if url.endswith("/v1/preprocess_and_tran"):
            return _FakeResponse(json_data={
                "code": 0,
                "asr_format_audio_url": "/srv/shared/ref.wav",
                "reference_audio_text": "reference words",
            })
        if url.endswith("/v1/invoke"):
            return _FakeResponse(content=b"RIFF" + b"0" * 48)
        if url.endswith("/submit"):
            return _FakeResponse(json_data={"code": 10000, "msg": "submitted"})
        return _FakeResponse(status_code=404, text="not found")

    def get(self, url, timeout=None, params=None):
        self.gets.append((url, timeout, params))
        if url.endswith("/query"):
            return _FakeResponse(json_data={"code": 10000, "data": {"status": 1, "progress": "1 / 1", "msg": "queued"}})
        return _FakeResponse(status_code=200, json_data={"ok": True})


def test_speech_engine_creates_and_reuses_omnivoice_prompt(tmp_path, monkeypatch):
    prompts = tmp_path / "prompts"
    monkeypatch.setattr(config, "omnivoice_prompt_dir", str(prompts), raising=False)
    engine = SpeechEngine(root=tmp_path / "speech")
    fake_model = _FakeOmniModel()
    monkeypatch.setattr(
        engine,
        "_load_omnivoice_runtime",
        lambda: {"model": fake_model, "soundfile": _FakeSF(), "VoiceClonePrompt": _FakePrompt},
    )

    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF" + b"0" * 48)
    created = engine.create_clone_prompt(str(ref), ref_text="hello there", prompt_id="voice-a", label="demo")
    assert created["success"] is True
    assert (prompts / "voice-a.pt").exists()
    assert (prompts / "voice-a.json").exists()
    listed = engine.list_clone_prompts()
    assert listed and listed[0]["prompt_id"] == "voice-a"

    result = engine.synthesize("hello world", backend="omnivoice", prompt_id="voice-a", language="en")
    assert result["success"] is True
    assert result["backend"] == "omnivoice"
    assert result["prompt_id"] == "voice-a"
    assert fake_model.last_generate["language"] == "en"
    assert "voice_clone_prompt" in fake_model.last_generate


def test_discover_local_stt_models_finds_handy_assets(tmp_path, monkeypatch):
    models_dir = tmp_path / "handy-models"
    models_dir.mkdir()
    (models_dir / "ggml-small.bin").write_bytes(b"x")
    (models_dir / "parakeet-unified-en-0.6b-Q8_0.gguf").write_bytes(b"x")
    (models_dir / "parakeet-tdt-0.6b-v3-int8").mkdir()
    monkeypatch.setattr(config, "handy_model_dirs", str(models_dir), raising=False)

    out = voice.discover_local_stt_models()
    assert out["count"] >= 3
    engines = {row["engine"] for row in out["models"]}
    assert "whisper-ggml" in engines
    assert "parakeet" in engines


def test_transcribe_audio_can_remember_into_memory(tmp_path, monkeypatch):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF" + b"0" * 48)

    from core.memory.store import MemoryFacade
    import core.memory.store as memory_store

    previous = memory_store._facade
    memory_store._facade = MemoryFacade(db_path=str(tmp_path / "memory2.db"))
    try:
        monkeypatch.setattr(
            voice,
            "local_engine_transcribe",
            lambda path, language=None: {"success": True, "backend": "nollama", "text": "  um hello   world  ", "language": "en"},
        )
        result = voice.transcribe_audio(
            str(audio),
            remember=True,
            session_id="voice-session",
            project="demo",
            normalize=True,
            strip_fillers=True,
        )
        assert result["success"] is True
        assert result["text"] == "hello world"
        assert result["memory"]["remembered"] is True
        rows = memory_store._facade.search_sessions("hello world", limit=5, project="demo")
        assert rows, "remembered transcript should be searchable through MemoryFacade"
    finally:
        try:
            memory_store._facade.close()
        except Exception:
            pass
        memory_store._facade = previous


def test_avatar_service_render_pipeline(tmp_path):
    session = _FakeAvatarSession()
    service = AvatarService(
        tts_base_url="http://127.0.0.1:18180",
        face2face_base_url="http://127.0.0.1:8383/easy",
        root=tmp_path,
        session=session,
    )
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF" + b"0" * 48)
    avatar = tmp_path / "avatar.mp4"
    avatar.write_bytes(b"00")

    prepared = service.prepare_voice(str(ref), lang="en")
    assert prepared["success"] is True
    profile_id = prepared["voice_profile"]["voice_profile_id"]
    audio = service.synthesize_audio("hello avatar", voice_profile_id=profile_id)
    assert audio["success"] is True and Path(audio["path"]).exists()
    submitted = service.submit_video(audio["path"], str(avatar), code="job-1")
    assert submitted["success"] is True and submitted["code"] == "job-1"
    status = service.query_video("job-1")
    assert status["code"] == "job-1"
    pipeline = service.render_from_text("hello again", str(avatar), voice_profile_id=profile_id, code="job-2")
    assert pipeline["success"] is True
    assert pipeline["submission"]["code"] == "job-2"


def test_capabilities_endpoint_reports_speech_transcription_and_avatar(monkeypatch):
    from gateway.gateway import app

    monkeypatch.setattr(
        "gateway.routes_speech.get_channel_status",
        lambda: {"telegram": "offline"},
        raising=False,
    )
    client = TestClient(app)
    body = client.get("/api/v1/system/capabilities").json()
    assert "speech" in body
    assert "transcription" in body
    assert "avatar" in body


def test_speech_routes_expose_transcription_models_and_avatar_status(monkeypatch):
    from gateway.gateway import app

    monkeypatch.setattr(
        "tools.voice.voice_available_models",
        lambda: {"available_models": ["base"], "discovered_models": [{"name": "GGML Small"}], "discovered_count": 1, "search_dirs": []},
    )
    monkeypatch.setattr(
        "core.avatar.get_avatar_service",
        lambda: SimpleNamespace(status=lambda probe=False: {"available": False, "backend": "heygem-compatible"}),
    )
    client = TestClient(app)
    models = client.get("/speech/transcription/models").json()
    assert models["discovered_count"] == 1
    status = client.get("/speech/avatar/status").json()
    assert status["backend"] == "heygem-compatible"
