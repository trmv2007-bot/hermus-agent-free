"""Living Control Room dashboard + local Talking Mode integration tests."""
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.append(str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

from core.dashboard_events import DashboardEventBus, dashboard_event_bus
from core.speech import SpeechEngine, prepare_speech_text
from gateway.gateway import app


def test_single_control_room_is_served_projection():
    """The one production UI is /control; legacy dashboard surfaces are gone."""
    client = TestClient(app)
    page = client.get("/control")
    assert page.status_code == 200
    assert "HERMUS" in page.text and "Snapshot" in page.text and "Replay" in page.text
    # No legacy dashboard/static surface remains reachable.
    assert client.get("/dashboard").status_code == 404
    assert client.get("/dashboard/legacy").status_code == 404
    assert client.get("/dashboard-assets/living-deck.js").status_code == 404
    assert client.get("/dashboard-assets/living-deck.css").status_code == 404


def test_dashboard_status_and_event_websocket():
    client = TestClient(app)
    status = client.get("/dashboard/status")
    assert status.status_code == 200
    body = status.json()
    assert body["gateway"] == "online"
    assert body["local"] is True
    assert "speech" in body and "tasks" in body

    with client.websocket_connect("/dashboard/events") as websocket:
        first = websocket.receive_json()
        assert first["kind"] == "snapshot"
        assert isinstance(first["events"], list)
        sent = dashboard_event_bus.publish("test_neural_event", {"ok": True})
        received = websocket.receive_json()
        assert received["id"] == sent["id"]
        assert received["data"]["ok"] is True


def test_dashboard_event_bus_isolated_instance():
    bus = DashboardEventBus(max_events=20)
    delivered = []
    unsubscribe = bus.subscribe(delivered.append)
    event = bus.publish("session_started", {"run_id": "r1"})
    assert delivered == [event]
    assert bus.recent(1)[0]["data"]["run_id"] == "r1"
    unsubscribe()
    bus.publish("session_finished", {"run_id": "r1"})
    assert len(delivered) == 1


def test_speech_text_cleanup_and_safe_audio_lookup(tmp_path):
    engine = SpeechEngine(root=tmp_path)
    cleaned = prepare_speech_text("# Result\nSee [docs](https://example.com). `done`\n```py\nprint('secret')\n```")
    assert "Result" in cleaned and "docs" in cleaned and "Code block omitted" in cleaned
    assert "https://" not in cleaned and "print" not in cleaned
    assert engine.audio_path("../../etc/passwd") is None
    assert engine.audio_path("not-an-id") is None

    audio_id = "a" * 32
    wav = tmp_path / f"{audio_id}.wav"
    wav.write_bytes(b"RIFF" + b"0" * 48)
    assert engine.audio_path(audio_id) == wav


def test_talking_command_returns_backend_audio(monkeypatch):
    class FakeAgent:
        mode = SimpleNamespace(value="agent")
        mode_config = SimpleNamespace(name="Agent Mode", description="Agent mode for tests")
        model_name = "mock/talking"
        profile = ""
        llm = SimpleNamespace(provider="mock", _resolve_bundle=lambda: {"provider": "mock"})

        def chat(self, text):
            return {"response": f"Spoken response to {text}", "tool_calls": [], "steps": 1}

    monkeypatch.setattr("gateway.gateway.get_agent_for_user", lambda *args, **kwargs: FakeAgent())
    monkeypatch.setattr(
        "core.speech.speech_engine.synthesize",
        lambda text, voice=None, rate=165: {
            "success": True,
            "audio_id": "b" * 32,
            "path": "/private/path/never-exposed.wav",
            "backend": "test-tts",
            "estimated_duration": 1.2,
        },
    )

    client = TestClient(app)
    response = client.post("/command", json={
        "platform": "dashboard",
        "user_id": "talk-test",
        "text": "hello",
        "mode": "agent",
        "talking": True,
        "run_id": "run_talk_test",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["talking"] is True
    assert body["run_id"] == "run_talk_test"
    assert body["speech"]["audio_url"] == "/speech/audio/" + "b" * 32
    assert "path" not in body["speech"]
    recent = dashboard_event_bus.recent(20)
    types = [event["type"] for event in recent if event["data"].get("run_id") == "run_talk_test"]
    assert "session_started" in types
    assert "agent_response" in types
    assert "speech_ready" in types
    assert "session_finished" in types


def test_invalid_speech_audio_id_is_404():
    response = TestClient(app).get("/speech/audio/not-safe")
    assert response.status_code == 404
