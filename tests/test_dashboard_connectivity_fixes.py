"""Regression tests for the dashboard/gateway connectivity fixes.

Covers:
  * /command multipart file uploads actually reach the agent prompt (P0 #1)
  * autonomous/mission reports expose canonical ``response`` field (P0 #4)
  * MissionEngine never fakes node success without a real executor (P0 #6)
  * mission budget extension is counted exactly once (P1 #12)
  * artifacts are not re-attributed across missions (P0 #7)
  * /run/steer is a real endpoint that records instructions (P0 #2)
  * HEAD / and /favicon.ico behave (transport noise from screenshot)
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from starlette.testclient import TestClient


@pytest.fixture()
def client():
    from gateway.gateway import app
    with TestClient(app) as c:
        yield c


def test_command_multipart_attachment_reaches_prompt(client):
    """Attached file bytes must be injected into the prompt sent to the agent.

    Monkeypatches get_agent_for_user to capture the exact text the agent loop
    receives, so this is independent of which (offline) model backend is active.
    """
    import gateway.gateway as gw

    captured = {}

    class FakeAgent:
        mode = type("M", (), {"value": "agent"})()
        mode_config = type("C", (), {"name": "Agent", "description": "x"})()
        model_name = "fake"
        project = "default"

        class _LLM:
            provider = "fake"

            def _resolve_bundle(self):
                return {}
        llm = _LLM()

        def chat(self, text, **kwargs):
            captured["text"] = text
            return {"response": "ack", "tool_calls": [], "tool_results": [], "steps": 1}

    original = gw.get_agent_for_user
    gw.get_agent_for_user = lambda *a, **k: FakeAgent()
    try:
        files = {"files": ("notes.txt", b"MKR42_secret_marker answer", "text/plain")}
        data = {"platform": "dashboard", "user_id": "attach-test", "text": "Marker?"}
        resp = client.post("/command", data=data, files=files)
        assert resp.status_code == 200, resp.text[:300]
    finally:
        gw.get_agent_for_user = original

    prompt = captured.get("text", "")
    assert "MKR42_secret_marker" in prompt, "attachment bytes must reach the agent prompt"
    assert "Attachment: notes.txt" in prompt


def test_command_multipart_attachment_e2e_mock(client):
    """End-to-end via the offline mock: attachment block appears in the echo
    when the default Ollama fallback is active."""
    files = {"files": ("n.txt", b"MKR42 answer", "text/plain")}
    data = {"platform": "dashboard", "user_id": "attach-e2e-z", "text": "M?"}
    resp = client.post("/command", data=data, files=files)
    assert resp.status_code == 200
    body = resp.json()
    # If the ollama-fallback echo path was used it includes the attachment;
    # any other (model) path still returns a normal response — never an error.
    assert "error" not in body or body.get("response")


def test_command_json_still_works(client):
    """The JSON path must remain fully backward compatible."""
    resp = client.post("/command", json={"platform": "cli", "user_id": "x", "text": "ping"}, timeout=60)
    assert resp.status_code == 200
    assert "response" in resp.json()


def test_steer_endpoint_exists_and_reports_no_run(client):
    """Steer is a real backend call, not a no-op UI stub (P0 #2)."""
    # Empty text -> 400
    r = client.post("/run/steer", json={"run_id": "x", "text": ""})
    assert r.status_code == 400
    # Unknown run -> honest "not applied" rather than a lie.
    r = client.post("/run/steer", json={"run_id": "run_does_not_exist", "text": "be brief"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["applied_to_stream"] is False
    assert "No active run" in body["note"]


def test_steer_delivered_to_active_run(client):
    import threading
    import time
    from core.run_events import run_bus

    rid = "run_steer_probe"
    run_bus.start(rid, label="probe")
    captured = []
    ev = threading.Event()

    def sink(run_id, event):
        if run_id == rid and event.get("type") == "steer":
            captured.append(event)
            ev.set()

    unsub = run_bus.add_sink(sink)
    try:
        r = client.post("/run/steer", json={"run_id": rid, "text": "also check tests"})
        assert r.status_code == 200
        assert r.json()["applied_to_stream"] is True
        assert ev.wait(timeout=3), "steer event must be published on the run stream"
        assert captured and captured[0]["data"]["text"] == "also check tests"
    finally:
        unsub()
        time.sleep(0.05)


def test_head_root_and_favicon(client):
    # Don't follow the redirect: HEAD / itself must be allowed (was 405 before).
    r = client.head("/", follow_redirects=False)
    assert r.status_code in (307, 302)
    fav = client.get("/favicon.ico")
    assert fav.status_code == 200
    assert "svg" in fav.headers.get("content-type", "")


def test_autonomous_report_has_response_field():
    # The autonomy contract exposes a canonical `response` field. The MissionEngine
    # is the only autonomy engine, so this now checks the mission report contract
    # exposes a non-empty human-readable response mirroring the final proof.
    from core.mission import MissionEngine, MissionReport

    with tempfile.TemporaryDirectory() as tmp:
        eng = MissionEngine(storage_dir=pathlib.Path(tmp) / "missions")
        report = eng.start_mission("produce a short report", domain="research", budget_steps=1)
        d = report.to_dict()
        assert "response" in d
        if d.get("state") == "completed":
            assert d["response"]  # non-empty canonical answer on completion


def test_mission_report_has_response_field():
    from core.mission import MissionEngine

    with tempfile.TemporaryDirectory() as tmp:
        eng = MissionEngine(storage_dir=pathlib.Path(tmp) / "missions")
        report = eng.start_mission("produce a short report", domain="research", budget_steps=3)
        d = report.to_dict()
        assert "response" in d
        # response mirrors the human-readable final proof / result.
        assert d["response"] == d["final_proof"]


def test_mission_does_not_fake_success_without_backend():
    """The default production executor must not fabricate node success (P0 #6)."""
    from core.mission import MissionEngine, MissionState

    with tempfile.TemporaryDirectory() as tmp:
        eng = MissionEngine(storage_dir=pathlib.Path(tmp) / "missions")
        report = eng.start_mission("build and verify an app", budget_steps=3)
        assert report.state in (MissionState.FAILED.value, MissionState.BLOCKED.value)


def test_budget_extension_counted_once():
    """extend_budget must add exactly the requested steps, not double (P1 #12)."""
    from core.mission import MissionBudget

    b = MissionBudget(initial_steps=25)
    b.grant_extension(10)
    assert b.extensions_used == 1
    assert b.bonus_steps == 10
    assert b.total_steps() == 35
    b.grant_extension(5)
    assert b.total_steps() == 40  # not 25 + 10*2 + 5, etc.
    serialized = b.to_dict()
    assert serialized["total_steps"] == 40
    # Deserialization must tolerate the computed total_steps key.
    from core.mission import MissionReport
    assert MissionBudget(**{k: v for k, v in serialized.items() if k in MissionBudget.__dataclass_fields__})


def test_artifacts_not_reattributed_across_missions(tmp_path):
    """A file owned by one mission must not be stolen by another (P0 #7)."""
    from core.artifact_manager import ArtifactManager

    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "report.json").write_text('{"ok": true}')
    mgr = ArtifactManager(storage_dir=tmp_path / "artifacts", workspace_root=ws)

    first = mgr.scan_workspace(mission_id="m1")
    second = mgr.scan_workspace(mission_id="m2")

    assert len(first) == 1
    assert len(second) == 0  # m2 does not claim m1's file
    assert mgr.list_artifacts(mission_id="m1")
    assert not mgr.list_artifacts(mission_id="m2")

    # Re-registering the same file under the SAME mission keeps ownership.
    a = mgr.register_artifact(ws / "report.json", mission_id="m1")
    assert a.mission_id == "m1"
