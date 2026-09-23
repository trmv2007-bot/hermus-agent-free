"""Tests for fleet control-plane routes (SPEC §9) - Part 2: Task assign/cancel/pause/resume."""

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
    app.state.fleet_registry = fleet_registry
    with TestClient(app) as c:
        yield c
    if hasattr(app.state, "fleet_registry"):
        del app.state.fleet_registry


# ---------------------------------------------------------------------------
# Task assignment / cancel / pause / resume
# ---------------------------------------------------------------------------


def test_assign_task_success(client: TestClient):
    """POST /api/fleet/agents/{id}/task assigns a task idempotently."""
    r = client.post("/api/fleet/agents", json={"name": "Worker", "provider": "groq"})
    agent_id = r.json()["agent_id"]

    resp = client.post(
        f"/api/fleet/agents/{agent_id}/task",
        json={"task": "Say hello", "idempotency_key": "key-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["agent_id"] == agent_id
    assert "task_id" in data
    assert data["executed"] is True
    assert "content" in data


def test_assign_task_idempotency(client: TestClient):
    """Same idempotency_key returns the same result without re-executing."""
    r = client.post("/api/fleet/agents", json={"name": "Worker2", "provider": "groq"})
    agent_id = r.json()["agent_id"]

    r1 = client.post(
        f"/api/fleet/agents/{agent_id}/task",
        json={"task": "Say hello", "idempotency_key": "idem-1"},
    )
    r2 = client.post(
        f"/api/fleet/agents/{agent_id}/task",
        json={"task": "Say hello", "idempotency_key": "idem-1"},
    )
    assert r1.json()["task_id"] == r2.json()["task_id"]


def test_assign_task_requires_task(client: TestClient):
    """POST /api/fleet/agents/{id}/task rejects empty task with 400."""
    r = client.post("/api/fleet/agents", json={"name": "Worker3", "provider": "groq"})
    agent_id = r.json()["agent_id"]

    resp = client.post(f"/api/fleet/agents/{agent_id}/task", json={})
    assert resp.status_code == 400
    assert resp.json()["code"] == "bad_request"


def test_assign_task_unknown_agent_404(client: TestClient):
    """POST /api/fleet/agents/unknown/task returns 404."""
    resp = client.post("/api/fleet/agents/unknown/task", json={"task": "x"})
    assert resp.status_code == 404


def test_cancel_task(client: TestClient):
    """DELETE /api/fleet/agents/{id}/task cancels current task.

    Note: with the synchronous stub chat_fn, tasks complete immediately,
    so by the time cancel is called the agent is already IDLE.
    The endpoint returns 400 when there's no in-flight task.
    """
    r = client.post("/api/fleet/agents", json={"name": "Worker4", "provider": "groq"})
    agent_id = r.json()["agent_id"]

    client.post(f"/api/fleet/agents/{agent_id}/task", json={"task": "Long task"})

    # Task completes synchronously with stub, so cancel returns 400 (no in-flight task)
    resp = client.delete(f"/api/fleet/agents/{agent_id}/task")
    assert resp.status_code == 400
    data = resp.json()
    assert data["success"] is False
    assert data["code"] == "bad_request"
    assert "no in-flight task" in data["message"]


def test_pause_agent(client: TestClient):
    """POST /api/fleet/agents/{id}/pause transitions agent to PAUSED."""
    r = client.post("/api/fleet/agents", json={"name": "Pausable", "provider": "groq"})
    agent_id = r.json()["agent_id"]

    resp = client.post(f"/api/fleet/agents/{agent_id}/pause")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["state"] == "PAUSED"


def test_resume_agent(client: TestClient):
    """POST /api/fleet/agents/{id}/resume transitions PAUSED -> IDLE."""
    r = client.post("/api/fleet/agents", json={"name": "Resumable", "provider": "groq"})
    agent_id = r.json()["agent_id"]

    client.post(f"/api/fleet/agents/{agent_id}/pause")
    resp = client.post(f"/api/fleet/agents/{agent_id}/resume")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["state"] == "IDLE"


def test_pause_illegal_transition_400(client: TestClient):
    """Pausing an already PAUSED agent returns 400 (illegal transition)."""
    r = client.post("/api/fleet/agents", json={"name": "Pausable2", "provider": "groq"})
    agent_id = r.json()["agent_id"]

    client.post(f"/api/fleet/agents/{agent_id}/pause")
    resp = client.post(f"/api/fleet/agents/{agent_id}/pause")
    assert resp.status_code == 400
    assert resp.json()["code"] == "illegal_transition"