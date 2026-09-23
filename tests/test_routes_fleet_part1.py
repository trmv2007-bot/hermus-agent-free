"""Tests for fleet control-plane routes (SPEC §9) - Part 1: Spawn/Roster/Dismiss."""

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
from gateway.routes_fleet import _get_registry


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _stub_chat_fn(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Simple stub chat function for tests - matches expected signature."""
    return {"content": "Test response", "tokens": 10}


@pytest.fixture()
def fleet_bus(tmp_path: Path) -> FleetBus:
    """An isolated FleetBus with snapshots disabled for fast tests."""
    bus = FleetBus(base_dir=tmp_path / "fleet", fsync=False, snapshot_every_events=0, snapshot_interval_s=0)
    yield bus
    bus.close()


@pytest.fixture()
def fleet_registry(fleet_bus: FleetBus) -> FleetRegistry:
    """A FleetRegistry backed by the test bus with a stub chat function."""
    reg = FleetRegistry(fleet_bus, chat_fn=_stub_chat_fn)
    yield reg
    # Cleanup: dismiss all agents
    for agent in reg.list():
        if agent.state != "DESTROYED":
            try:
                reg.dismiss(agent.agent_id, confirm=True)
            except Exception:
                pass


@pytest.fixture()
def client(fleet_registry: FleetRegistry) -> TestClient:
    """TestClient with the fleet registry mounted on app.state."""
    app.state.fleet_registry = fleet_registry
    with TestClient(app) as c:
        yield c
    # Clean up
    if hasattr(app.state, "fleet_registry"):
        del app.state.fleet_registry


# ---------------------------------------------------------------------------
# Spawn / Roster / Dismiss
# ---------------------------------------------------------------------------


def test_spawn_agent_success(client: TestClient):
    """POST /api/fleet/agents creates a new agent."""
    resp = client.post(
        "/api/fleet/agents",
        json={"name": "TestAgent", "persona": "helpful", "provider": "groq", "model": "llama-3.3-70b"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "agent_id" in data
    assert data["state"] == "IDLE"


def test_spawn_agent_requires_name(client: TestClient):
    """POST /api/fleet/agents rejects missing name with 400."""
    resp = client.post("/api/fleet/agents", json={"persona": "test"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "bad_request"


def test_spawn_agent_name_collision_409(client: TestClient, fleet_registry: FleetRegistry):
    """POST /api/fleet/agents rejects duplicate name (case-insensitive) with 409."""
    client.post("/api/fleet/agents", json={"name": "Duplicate", "provider": "groq"})
    resp = client.post("/api/fleet/agents", json={"name": "duplicate", "provider": "groq"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"


def test_list_agents(client: TestClient, fleet_registry: FleetRegistry):
    """GET /api/fleet/agents returns the roster with expected fields."""
    # Spawn a couple agents
    r1 = client.post("/api/fleet/agents", json={"name": "AgentOne", "provider": "groq"})
    r2 = client.post("/api/fleet/agents", json={"name": "AgentTwo", "provider": "openai", "model": "gpt-4o-mini"})
    id1 = r1.json()["agent_id"]
    id2 = r2.json()["agent_id"]

    resp = client.get("/api/fleet/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    agents = data["agents"]
    assert len(agents) == 2
    # Check fields per SPEC §9
    for a in agents:
        assert "id" in a
        assert "name" in a
        assert "provider" in a
        assert "model" in a
        assert "state" in a
        assert "stats" in a
        assert "key_name" in a  # FULL keys visible in open mode
        assert "skills" in a
        assert "last_activity" in a
        assert "created_at" in a
        assert "memory_summary" in a
        assert "current_task" in a


def test_dismiss_agent_requires_confirm(client: TestClient):
    """DELETE /api/fleet/agents/{id} requires confirm=true query param."""
    r = client.post("/api/fleet/agents", json={"name": "ToDismiss", "provider": "groq"})
    agent_id = r.json()["agent_id"]

    resp = client.delete(f"/api/fleet/agents/{agent_id}")  # no confirm
    assert resp.status_code == 400
    assert resp.json()["code"] == "bad_request"


def test_dismiss_agent_success(client: TestClient):
    """DELETE /api/fleet/agents/{id}?confirm=true destroys the agent."""
    r = client.post("/api/fleet/agents", json={"name": "ToDismiss", "provider": "groq"})
    agent_id = r.json()["agent_id"]

    resp = client.delete(f"/api/fleet/agents/{agent_id}?confirm=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["destroyed"] is True

    # Agent should no longer appear in roster
    list_resp = client.get("/api/fleet/agents")
    agents = list_resp.json()["agents"]
    assert not any(a["id"] == agent_id for a in agents)


def test_dismiss_unknown_agent_404(client: TestClient):
    """DELETE /api/fleet/agents/unknown returns 404."""
    resp = client.delete("/api/fleet/agents/unknown-id?confirm=true")
    assert resp.status_code == 404