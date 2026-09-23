"""Tests for fleet control-plane routes (SPEC §9) - Part 3: Broadcast/Orchestrate/Keys/Screen/WS."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.fleet.bus import FleetBus
from core.fleet.registry import FleetRegistry, chat_via_freellm
from gateway.gateway import app


# ---------------------------------------------------------------------------
# Test fixtures (redefined for isolation)
# ---------------------------------------------------------------------------


def _stub_chat_fn(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Simple stub chat function for tests - matches expected signature."""
    return {"content": "Test response", "tokens": 10}


@pytest.fixture()
def fleet_bus(tmp_path: Path) -> FleetBus:
    bus = FleetBus(base_dir=tmp_path / "fleet", fsync=False, snapshot_every_events=0, snapshot_interval_s=0)
    yield bus
    bus.close()


@pytest.fixture()
def fleet_registry(fleet_bus: FleetBus) -> FleetRegistry:
    reg = FleetRegistry(fleet_bus, chat_fn=_stub_chat_fn)
    yield reg
    for agent in reg.list():
        if agent.state != "DESTROYED":
            try:
                reg.dismiss(agent.agent_id, confirm=True)
            except Exception:
                pass


@pytest.fixture()
def client(fleet_registry: FleetRegistry) -> TestClient:
    # Configure app for testing (similar to test_gateway_realtime.py)
    import gateway.gateway as g
    g.config.gateway_queue_enabled = True
    g.config.gateway_queue_workers = 1
    g.config.background_agents_enabled = False
    g.config.auto_start_channels = False
    
    app.state.fleet_registry = fleet_registry
    with TestClient(app) as c:
        yield c
    if hasattr(app.state, "fleet_registry"):
        del app.state.fleet_registry


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------


def test_broadcast_message(client: TestClient):
    """POST /api/fleet/broadcast sends to all agents."""
    client.post("/api/fleet/agents", json={"name": "Listener1", "provider": "groq"})
    client.post("/api/fleet/agents", json={"name": "Listener2", "provider": "groq"})

    resp = client.post("/api/fleet/broadcast", json={"content": "Hello all", "priority": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "event_id" in data
    assert "seq" in data


def test_broadcast_requires_content(client: TestClient):
    """POST /api/fleet/broadcast rejects missing content."""
    resp = client.post("/api/fleet/broadcast", json={})
    assert resp.status_code == 400
    assert resp.json()["code"] == "bad_request"


# ---------------------------------------------------------------------------
# Orchestrate
# ---------------------------------------------------------------------------


def test_orchestrate_mission(client: TestClient):
    """POST /api/fleet/orchestrate decomposes the goal and reports success only
    for work that really landed.

    This test used to assert a response shape the route no longer returns
    (``subtasks``, ``state == "claiming"``) and a ``success: true`` that stayed
    true even when zero subtasks completed — the orchestrator hardcoded it.
    """
    client.post("/api/fleet/agents", json={"name": "OrchAgent", "provider": "groq"})

    resp = client.post(
        "/api/fleet/orchestrate",
        json={"goal": "Build a thing", "budget": 20000, "hitl_gates": []},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mission_id"]
    assert data["state"] in ("done", "failed")
    synthesis = data["synthesis"]
    assert synthesis is not None, f"orchestration never reached synthesis: {data['error']}"
    # The default decomposer really ran.
    assert synthesis["subtasks_total"] > 0
    completed = synthesis["subtasks_completed"]
    # success tracks real completions — a mock fallback reply never counts.
    assert data["success"] is (completed == synthesis["subtasks_total"])
    if not completed:
        assert data["error"], "a mission that completed nothing must say why"

    # An after_decompose gate parks the mission for a human. The orchestrator
    # used to auto-approve a duplicate gate on top of MissionManager's own
    # suspension, which raised "illegal transition suspended → suspended" and
    # defeated HITL entirely.
    resp2 = client.post(
        "/api/fleet/orchestrate",
        json={"goal": "Build another thing", "budget": 20000, "hitl_gates": ["after_decompose"]},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["mission_id"]
    assert data2["state"] == "suspended"
    assert data2["success"] is False
    assert "after_decompose" in data2["error"]


def test_orchestrate_requires_goal(client: TestClient):
    """POST /api/fleet/orchestrate rejects empty goal."""
    resp = client.post("/api/fleet/orchestrate", json={})
    assert resp.status_code == 400
    assert resp.json()["code"] == "bad_request"


# ---------------------------------------------------------------------------
# Keys (Vault)
# ---------------------------------------------------------------------------


def test_list_keys(client: TestClient):
    """GET /api/fleet/keys returns configured key names."""
    resp = client.get("/api/fleet/keys")
    assert resp.status_code == 200
    data = resp.json()
    assert "keys" in data
    assert isinstance(data["keys"], list)


def test_add_key(client: TestClient):
    """POST /api/fleet/keys adds a new key to the vault."""
    resp = client.post(
        "/api/fleet/keys",
        json={"provider": "groq", "name": "test-key", "key": "gsk_test123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "key_id" in data
    assert data["name"] == "test-key"


def test_add_key_requires_fields(client: TestClient):
    """POST /api/fleet/keys rejects missing required fields."""
    for missing in ["provider", "name", "key"]:
        body = {"provider": "groq", "name": "k", "key": "x"}
        del body[missing]
        resp = client.post("/api/fleet/keys", json=body)
        assert resp.status_code == 400
        assert resp.json()["code"] == "bad_request"


# ---------------------------------------------------------------------------
# Screen frame (stub)
# ---------------------------------------------------------------------------


def test_screen_frame(client: TestClient):
    """GET /api/fleet/screen/frame returns stub response."""
    resp = client.get("/api/fleet/screen/frame")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["frame_base64"] is None


# ---------------------------------------------------------------------------
# WebSocket (smoke test - connection only)
# ---------------------------------------------------------------------------


def test_fleet_ws_connect(client: TestClient):
    """WS /ws/fleet accepts connection, sends hello + initial snapshot.

    The fleet WS now sends an initial snapshot (mirrors GET /api/fleet/agents)
    after the hello so the dashboard has full state before bus deltas arrive.
    """
    with client.websocket_connect("/api/fleet/ws/fleet") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["protocol"] == "hermus.fleet.v1"

        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert "data" in snapshot
        assert "agents" in snapshot["data"]
        assert "count" in snapshot["data"]


def test_fleet_ws_rejects_token_when_configured(client: TestClient):
    """WS /ws/fleet closes with 1008 when a wrong token is presented.

    When no gateway token is configured the endpoint is open; the test client
    runs with no token configured so the connection succeeds — this test
    verifies the auth path by presenting a wrong token and confirming the
    connection is refused.
    """
    from gateway import gateway as g

    saved = getattr(g.config, "gateway_api_token", None)
    try:
        g.config.gateway_api_token = "expected-token"

        def attempt(query: str) -> int | None:
            try:
                with client.websocket_connect(query) as ws:
                    ws.receive_json()
                return None  # connected — should not happen with wrong token
            except BaseException as e:
                return getattr(e, "code", None) or getattr(e.__context__, "code", None)

        code = attempt("/api/fleet/ws/fleet?token=wrong-token")
        assert code == 1008, f"expected close code 1008, got {code!r}"
    finally:
        g.config.gateway_api_token = saved  # None restores "no token configured"


def test_fleet_screen_ws_connect(client: TestClient):
    """WS /ws/fleet/screen accepts connection and sends hello + frame."""
    with client.websocket_connect("/api/fleet/ws/fleet/screen") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["protocol"] == "hermus.fleet.screen.v1"

        frame = ws.receive_json()
        assert frame["type"] == "frame"
        # New implementation sends base64 JPEG data; old sent placeholder with message
        assert "data" in frame  # data may be base64 string or None
        if frame["data"] is None:
            # Legacy placeholder format
            assert "message" in frame
        else:
            # New format: base64 JPEG data with metadata
            assert "timestamp" in frame or "captured_at" in frame or "sequence" in frame


def test_fleet_screen_ws_rejects_token_when_configured(client: TestClient):
    """WS /ws/fleet/screen closes with 1008 when a wrong token is presented."""
    from gateway import gateway as g

    saved = getattr(g.config, "gateway_api_token", None)
    try:
        g.config.gateway_api_token = "expected-token"

        def attempt(query: str) -> int | None:
            try:
                with client.websocket_connect(query) as ws:
                    ws.receive_json()
                return None
            except BaseException as e:
                return getattr(e, "code", None) or getattr(e.__context__, "code", None)

        code = attempt("/api/fleet/ws/fleet/screen?token=wrong-token")
        assert code == 1008, f"expected close code 1008, got {code!r}"
    finally:
        g.config.gateway_api_token = saved  # None restores "no token configured"