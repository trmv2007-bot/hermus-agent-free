"""Routes over the in-memory AgentPool: what the endpoints actually change.

``gateway/routes_agents.py`` had no coverage of its own. These tests pin the
outcomes a caller depends on — configuration that took effect, an agent that
appears in the roster, a run that reports failure as failure, and a stored key
that never comes back out in the clear.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from gateway.gateway import app

    return TestClient(app)


@pytest.fixture()
def pool():
    from core.agents.pool import get_pool

    instance = get_pool()
    saved = {provider: list(keys) for provider, keys in instance._provider_keys.items()}
    yield instance
    instance._provider_keys.clear()
    instance._provider_keys.update(saved)


def _destroy(pool, agent_id: str) -> None:
    if pool.get_agent(agent_id) is not None:
        pool.destroy_agent(agent_id)


# ---------------------------------------------------------------------- pool


def test_pool_config_round_trips_through_the_live_object(client, pool):
    before = client.get("/api/v1/agents/pool/config").json()["config"]
    assert before["max_agents"] == pool.config.max_agents

    response = client.post("/api/v1/agents/pool/config", json={"max_agents": 7, "idle_timeout": 42})
    assert response.status_code == 200

    after = client.get("/api/v1/agents/pool/config").json()["config"]
    assert after["max_agents"] == 7
    assert after["idle_timeout"] == 42
    assert pool.config.max_agents == 7, "the endpoint must move the real config, not a copy"
    pool.config.max_agents = before["max_agents"]
    pool.config.idle_timeout = before["idle_timeout"]


def test_pool_status_reports_the_pool_it_owns(client, pool):
    body = client.get("/api/v1/agents/pool/status").json()

    assert body["status"] == "ok"
    assert body["pool"]["total_agents"] == len(pool.get_all_agents())
    assert body["pool"]["max_agents"] == pool.config.max_agents


# -------------------------------------------------------------------- agents


def test_created_agent_appears_in_the_roster_with_what_was_asked_for(client, pool):
    created = client.post(
        "/api/v1/agents/create",
        json={"name": "route-test-agent", "provider": "ollama", "model": "qwen2.5:3b", "role": "researcher"},
    ).json()
    agent_id = created["agent"]["agent_id"]
    try:
        assert created["status"] == "ok"
        listed = client.get("/api/v1/agents/list").json()
        match = [a for a in listed["agents"] if a["agent_id"] == agent_id]
        assert match, "a created agent must be listed, not just acknowledged"
        assert match[0]["config"]["name"] == "route-test-agent"
        assert match[0]["config"]["model"] == "qwen2.5:3b"
        assert match[0]["role"] == "researcher"
        assert listed["count"] == len(listed["agents"])
        assert pool.get_agent(agent_id) is not None, "the route and the pool must see one roster"
    finally:
        _destroy(pool, agent_id)


def test_a_credential_never_comes_back_out_of_the_roster(client, pool):
    """A list endpoint that echoes api_key is a way to read every stored secret."""
    secret = "sk-should-never-be-echoed-9876543210"
    created = client.post(
        "/api/v1/agents/create",
        json={"name": "keyed-agent", "provider": "groq", "model": "llama-3.3-70b", "api_key": secret},
    ).json()
    agent_id = created["agent"]["agent_id"]
    try:
        assert created["agent"]["config"]["api_key"] != secret
        assert created["agent"]["config"]["has_api_key"] is True
        assert secret not in client.get("/api/v1/agents/list").text
        assert secret not in client.post("/api/v1/agents/ping", json={"agent_id": agent_id}).text
        assert pool.get_agent(agent_id).config.api_key == secret, "the agent still needs the real key to work"
    finally:
        _destroy(pool, agent_id)


def test_an_unknown_role_falls_back_instead_of_failing_silently_wrong(client, pool):
    created = client.post(
        "/api/v1/agents/create",
        json={"name": "role-fallback", "role": "chief_vibes_officer"},
    ).json()
    agent_id = created["agent"]["agent_id"]
    try:
        assert created["agent"]["role"] == "general"
    finally:
        _destroy(pool, agent_id)


def test_running_a_task_on_an_unknown_agent_is_a_404_not_a_200(client):
    response = client.post("/api/v1/agents/definitely-not-here/run", json={"task": "do a thing"})
    assert response.status_code == 404


def test_a_failing_run_is_reported_as_failing(client, pool, monkeypatch):
    """The route returns whatever the agent reports; a 200 wrapper must not turn a
    failed task into an apparent success."""
    created = client.post("/api/v1/agents/create", json={"name": "failing-agent"}).json()
    agent_id = created["agent"]["agent_id"]

    async def always_fails(task, task_id=None):
        return {"status": "failed", "error": "model backend unreachable", "output": ""}

    monkeypatch.setattr(pool.get_agent(agent_id), "run_task", always_fails)
    try:
        body = client.post(f"/api/v1/agents/{agent_id}/run", json={"task": "anything"}).json()
        assert body["status"] == "ok", "the HTTP call itself succeeded"
        assert body["result"]["status"] == "failed"
        assert "unreachable" in body["result"]["error"]
    finally:
        _destroy(pool, agent_id)


def test_stopping_an_agent_removes_it_from_the_roster(client, pool):
    created = client.post("/api/v1/agents/create", json={"name": "stoppable"}).json()
    agent_id = created["agent"]["agent_id"]
    assert client.post(f"/api/v1/agents/{agent_id}/stop").status_code == 200

    assert pool.get_agent(agent_id) is None
    assert agent_id not in [a["agent_id"] for a in client.get("/api/v1/agents/list").json()["agents"]]


# ------------------------------------------------------------------- api keys


def test_a_stored_key_is_never_returned_in_the_clear(client, pool):
    secret = "sk-test-super-secret-value-1234567890"
    added = client.post("/api/v1/agents/api-keys/add", json={"provider": "groq", "key": secret})
    assert added.status_code == 200, added.text

    try:
        listed = client.get("/api/v1/agents/api-keys/list")
        assert listed.status_code == 200
        body = listed.json()
        assert secret not in listed.text, "the list endpoint must redact key material"
        entries = body["keys"].get("groq", [])
        assert any(entry for entry in entries)
        assert all(secret not in str(entry) for entry in entries)
    finally:
        client.post("/api/v1/agents/api-keys/remove", json={"provider": "groq", "key": secret})


def test_key_add_and_remove_need_both_fields(client):
    assert client.post("/api/v1/agents/api-keys/add", json={"key": "x"}).status_code == 400
    assert client.post("/api/v1/agents/api-keys/add", json={"provider": "groq"}).status_code == 400
    assert client.post("/api/v1/agents/api-keys/remove", json={"provider": "groq"}).status_code == 400


def test_removing_a_key_that_was_never_added_is_a_404(client, pool):
    response = client.post("/api/v1/agents/api-keys/remove", json={"provider": "groq", "key": "sk-never-added"})
    assert response.status_code == 404


# ------------------------------------------------------------------ messaging


def test_a_message_reaches_the_recipient_inbox_the_reader_endpoint_uses(client, pool):
    ids = {}
    for name in ("sender-one", "recipient-one"):
        created = client.post("/api/v1/agents/create", json={"name": name}).json()
        ids[name] = created["agent"]["agent_id"]
    try:
        sent = client.post(
            "/api/v1/agents/message",
            json={"sender_id": ids["sender-one"], "target_id": ids["recipient-one"], "content": "status please"},
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["to"] == ids["recipient-one"]

        fetched = client.get(f"/api/v1/agents/messages/{ids['recipient-one']}")
        assert fetched.status_code == 200
        body = fetched.json()
        assert body["count"] >= 1, "a delivered message must be readable from the same store it was written to"
        assert any("status please" in str(entry) for entry in body["messages"])
    finally:
        for agent_id in ids.values():
            _destroy(pool, agent_id)


def test_messaging_requires_both_ends_to_exist(client, pool):
    created = client.post("/api/v1/agents/create", json={"name": "half-a-conversation"}).json()
    agent_id = created["agent"]["agent_id"]
    try:
        unknown_sender = client.post(
            "/api/v1/agents/message", json={"sender_id": "no-such-agent", "target_id": agent_id, "content": "hi"}
        )
        unknown_target = client.post(
            "/api/v1/agents/message", json={"sender_id": agent_id, "target_id": "no-such-agent", "content": "hi"}
        )
        assert unknown_sender.status_code == 404
        assert unknown_target.status_code == 404
        assert client.get("/api/v1/agents/messages/no-such-agent").status_code in (404, 200)
    finally:
        _destroy(pool, agent_id)


def test_broadcast_records_one_message_without_inventing_recipients(client, pool):
    ids = []
    for name in ("bc-one", "bc-two"):
        created = client.post("/api/v1/agents/create", json={"name": name}).json()
        ids.append(created["agent"]["agent_id"])
    try:
        response = client.post("/api/v1/agents/broadcast", json={"content": "stand down"})
        assert response.status_code == 200
        body = response.json()
        assert body["message_id"], body
        assert isinstance(body["count"], int)
        # The response names no recipients, so it cannot claim delivery to agents
        # that were never addressed.
        assert "delivered_to" not in body and "recipients" not in body
    finally:
        for agent_id in ids:
            _destroy(pool, agent_id)
