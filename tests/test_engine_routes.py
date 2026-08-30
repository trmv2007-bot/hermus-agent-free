"""HTTP surface: engine, model downloads, telemetry feed and the doctor.

Also covers the two dashboard bugs these endpoints exist to fix:

* the Live Telemetry card was dead markup — nothing populated
  ``#overviewActivity`` and the ``.feed`` class had no CSS;
* the System Overview must show the local engine, and the "download the model"
  banner must disappear once the model is installed.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway.gateway import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def sandbox_manager_paths(tmp_path, monkeypatch):
    """Keep tests out of the developer's real ~/models and ~/.hermus."""
    from core.nollama import nollama_manager

    monkeypatch.setattr(nollama_manager, "home", tmp_path / "nollama")
    monkeypatch.setattr(nollama_manager, "models_dir", tmp_path / "models")
    monkeypatch.setattr(nollama_manager, "state_path", tmp_path / "state.json")
    monkeypatch.setattr(nollama_manager, "log_path", tmp_path / "nollama.log")
    yield


def _write_ir(path: Path, declared: int = 512, actual: int = 1024) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "openvino_model.xml").write_text(
        '<net><weights><blob offset="0" size="%d"/></weights></net>' % declared, encoding="utf-8"
    )
    (path / "openvino_model.bin").write_bytes(b"x" * actual)
    return path


# ---------------------------------------------------------------------------
# Engine status
# ---------------------------------------------------------------------------
def test_engine_status_reports_a_definite_state(client, monkeypatch):
    from core import accelerators as acc
    from core.accelerators import Device, HardwareSnapshot

    monkeypatch.setattr(
        acc,
        "cached_hardware",
        lambda refresh=False: HardwareSnapshot(
            npu=[Device("npu", "intel", "Intel AI Boost", "x", source="openvino")],
            gpus=[Device("gpu", "intel", "Intel Arc 140V", "x", source="openvino")],
            cpu_count=8, ram_mb=32000,
        ),
    )
    monkeypatch.setattr(acc, "probe_endpoint",
                        lambda base_url, timeout=2.0: {"reachable": False, "models": [], "detail": "down"})
    acc.reset_cache()
    body = client.get("/engine/status").json()

    assert body["plan"]["mode"] == "pipelined"
    assert body["status"] in ("needs_install", "needs_model", "unavailable", "ready", "not_applicable")
    assert body["status"] not in ("processing", "unknown", "")
    assert body["plan"]["roles"]["background"]["device"] == "NPU"
    assert body["plan"]["roles"]["reasoning"]["device"] == "GPU"
    acc.reset_cache()


def test_engine_status_probe_can_be_skipped(client):
    body = client.get("/engine/status?probe=false").json()
    assert body["engines"]["ollama"]["reachable"] is None
    assert body["engines"]["ollama"]["detail"] == "not probed"


def test_engine_refresh_forces_redetection(client, monkeypatch):
    from core import accelerators as acc

    calls = {"n": 0}

    def fake_state(refresh=False, probe=True):
        calls["n"] += 1
        return {"status": "not_applicable", "refreshed": refresh, "plan": {"mode": "disabled", "roles": {}}}

    monkeypatch.setattr(acc, "state", fake_state)
    assert client.post("/engine/refresh").json()["refreshed"] is True
    assert calls["n"] == 1


def test_api_status_carries_the_engine_summary(client):
    body = client.get("/api/status").json()
    engine = body["local_engine"]
    assert "mode" in engine and "roles" in engine
    assert set(engine["roles"]) >= {"reasoning", "background", "doctor", "vision"}
    assert "local_engine_routing_npu_gpu" in body["features"]
    assert "hermus_doctor_self_repair" in body["features"]


def test_nollama_install_route_reports_failure_cleanly(client, monkeypatch):
    from core.nollama import nollama_manager

    monkeypatch.setattr(nollama_manager, "install",
                        lambda **kw: {"success": False, "stage": "clone", "error": "git missing"})
    response = client.post("/engine/nollama/install")
    assert response.status_code == 500
    assert response.json()["stage"] == "clone"


def test_nollama_start_and_stop_routes(client, monkeypatch):
    from core.nollama import nollama_manager

    seen = {}
    monkeypatch.setattr(nollama_manager, "start",
                        lambda **kw: seen.update(kw) or {"success": True, "pid": 1, "port": 8010})
    monkeypatch.setattr(nollama_manager, "stop", lambda: {"stopped": True, "pid": 1})
    started = client.post("/engine/nollama/start", json={"device": "npu"})
    assert started.status_code == 200
    assert seen["device"] == "npu"
    assert client.post("/engine/nollama/stop").json()["stopped"] is True


# ---------------------------------------------------------------------------
# Models + downloads
# ---------------------------------------------------------------------------
def test_models_catalog_shows_minicpm_and_what_is_installed(client):
    body = client.get("/engine/models").json()
    ids = {row["id"]: row for row in body["catalog"]}
    assert "minicpm" in ids
    assert ids["minicpm"]["installed"] is False
    assert "doctor" in ids["minicpm"]["roles"]
    assert body["recommended"] is not None


def test_download_route_starts_a_job_and_reports_progress(client, monkeypatch):
    import huggingface_hub

    from core.nollama import nollama_manager

    monkeypatch.setattr(huggingface_hub, "snapshot_download",
                        lambda **kw: str(_write_ir(Path(kw["local_dir"]))))
    started = client.post("/engine/models/download", json={"model": "whisper-base"})
    assert started.status_code == 200
    job = started.json()["job"]
    assert job["state"] in ("queued", "downloading", "ready")

    job_id = job["id"]
    final = None
    for _ in range(80):
        final = client.get(f"/engine/downloads/{job_id}").json()
        if final["terminal"]:
            break
        time.sleep(0.1)
    assert final["state"] == "ready"
    assert client.get("/engine/downloads").json()["downloads"], "downloads list must be visible"

    listed = client.get("/engine/models").json()
    assert any(row["model_id"] == "" or row["complete"] for row in listed["installed"])


def test_download_route_rejects_unknown_models(client):
    response = client.post("/engine/models/download", json={"model": "gpt-9000"})
    assert response.status_code == 400
    assert "unknown model" in response.json()["error"]


def test_download_defaults_to_minicpm(client, monkeypatch):
    from core.nollama import nollama_manager

    captured = {}
    monkeypatch.setattr(nollama_manager, "download_model",
                        lambda model_id, force=False: captured.update(model=model_id) or
                        {"success": True, "started": False, "job": {"id": "x", "state": "ready", "terminal": True}})
    assert client.post("/engine/models/download", json={}).status_code == 200
    assert captured["model"] == "minicpm"


def test_unknown_download_job_is_404(client):
    assert client.get("/engine/downloads/nope").status_code == 404
    assert client.post("/engine/downloads/nope/cancel").json()["cancelled"] is False


# ---------------------------------------------------------------------------
# Live telemetry feed
# ---------------------------------------------------------------------------
def test_events_recent_returns_published_events(client):
    from core.dashboard_events import dashboard_event_bus

    dashboard_event_bus.publish("session_started", {"label": "fix the build"})
    dashboard_event_bus.publish("tool_call", {"tool": "shell", "args": "pytest"})
    dashboard_event_bus.publish("session_failed", {"error": "boom"})

    body = client.get("/events/recent?limit=10").json()
    assert body["count"] >= 3
    types = [event["type"] for event in body["events"]]
    assert "session_failed" in types and "session_started" in types
    # Newest first — the dashboard reverses it to render latest-on-top.
    assert types[0] == "session_failed"


def test_events_recent_limit_is_clamped(client):
    body = client.get("/events/recent?limit=5").json()
    assert body["count"] <= 5


def test_dashboard_wires_the_telemetry_feed():
    """The feed must actually be fed: JS writes to #overviewActivity."""
    html = Path("gateway/dashboard.html").read_text(encoding="utf-8")
    assert 'id="overviewActivity"' in html
    assert "function pushTelemetryEvent" in html
    assert "function loadTelemetry" in html
    assert "/events/recent?limit=" in html
    assert "/dashboard/events" in html, "WebSocket is the primary telemetry path"
    assert ".feed-item" in html, "the feed needs real CSS"
    # The permanent placeholder text is gone from the served markup.
    assert "Loading activity feed…" not in html


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------
def test_doctor_status_route(client):
    body = client.get("/doctor/status").json()
    for key in ("enabled", "model", "engine_status", "worst_severity", "counts",
                "finding_count", "stuck", "reports"):
        assert key in body


def test_doctor_run_returns_a_report(client, monkeypatch):
    from core.doctor import doctor

    monkeypatch.setattr(doctor, "_doctor_llm", lambda model=None: (None, ""))
    body = client.post("/doctor/run", json={"use_llm": False, "ask_internet": False}).json()
    assert body["status"] in ("ok", "attention", "critical")
    assert "findings" in body and "triage" in body
    assert body["triage"]["management_plan"] is not None
    assert body["research"]["performed"] is False


def test_doctor_reap_route_is_dry_run_by_default(client):
    body = client.post("/doctor/reap", json={}).json()
    assert body["dry_run"] is True
    assert "candidates" in body


def test_doctor_report_round_trip(client, monkeypatch):
    from core.doctor import doctor

    monkeypatch.setattr(doctor, "_doctor_llm", lambda model=None: (None, ""))
    report = client.post("/doctor/run", json={"use_llm": False, "ask_internet": False}).json()
    report_id = report["id"]

    fetched = client.get(f"/doctor/reports/{report_id}").json()
    assert fetched["id"] == report_id
    markdown = client.get(f"/doctor/reports/{report_id}?fmt=md")
    assert "# Hermus Doctor Report" in markdown.text
    assert client.get("/doctor/reports/does-not-exist").status_code == 404
    assert client.get("/doctor/reports").json()["count"] >= 1


def test_dashboard_has_the_engine_card_and_doctor_pane():
    html = Path("gateway/dashboard.html").read_text(encoding="utf-8")
    assert 'id="engineCard"' in html
    assert 'id="engineActionBanner"' in html
    assert 'id="engineRoles"' in html
    assert "downloadEngineModel(" in html
    assert 'id="pane-doctor"' in html
    assert "switchPane('doctor')" in html
    # setup.sh must stay runtime-only: models are the dashboard's job.
    assert "setup.sh installs the runtime only" in html
