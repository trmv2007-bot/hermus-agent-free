"""Gateway gate tests for the 2026-09 audit, Batch 2.

Each test pins one way the control plane used to be reachable that it must not
be again: an un-gated fleet router, a custom-API listing that returned live
credentials, a caller-switchable sandbox screen, and two request paths that
reached anywhere on the disk.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from gateway.gateway import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# ------------------------------------------------------------------ fleet gate
def test_fleet_http_surface_requires_the_gateway_token(client, monkeypatch):
    """/api/fleet was the only state-bearing router mounted without the token
    dependency, so spawning, key adds and orchestrate ignored the token."""
    from core.config import config

    monkeypatch.setattr(config, "gateway_api_token", "s3cret-gateway-token", raising=False)
    assert client.get("/api/fleet/agents").status_code == 401
    assert client.get("/api/fleet/agents", headers={"X-Hermus-Token": "s3cret-gateway-token"}).status_code == 200


# ---------------------------------------------------------------- redaction
def test_custom_apis_list_never_returns_a_live_credential(client, monkeypatch):
    sentinel = "sk-REAL-SECRET-DO-NOT-LEAK"
    from core.custom_api import custom_api_manager

    apis = [
        {
            "name": "leaky",
            "description": "",
            "url": "https://example.invalid",
            "method": "GET",
            "auth": {"token": sentinel},
            "id": "api-1",
            "created": "",
        }
    ]
    monkeypatch.setattr(custom_api_manager, "list_apis", lambda *a, **k: apis)

    resp = client.get("/custom-apis/list")
    assert resp.status_code == 200
    assert sentinel not in resp.text, "custom-apis/list returned the stored credential"
    row = resp.json()["custom_apis"][0]
    assert row["preview"].startswith("sk-REA...") and sentinel not in row["preview"]


# ---------------------------------------------------------------- sandbox
def test_sandbox_run_ignores_a_client_supplied_dangerous_flag(client, monkeypatch):
    import core.sandbox as sandbox_mod

    seen: dict = {}

    def fake_run(command, **kwargs):
        seen.update(kwargs)
        return {"returncode": 0, "stdout": "", "error": ""}

    monkeypatch.setattr(sandbox_mod.sandbox, "run", fake_run)
    resp = client.post("/sandbox/run", json={"command": "echo hi", "allow_dangerous": True})
    assert resp.status_code == 200
    assert seen.get("allow_dangerous") is False, "the caller switched off the dangerous-pattern screen"


# ---------------------------------------------------------------- paths
def test_embeddings_ingest_rejects_paths_outside_the_workspace(client):
    outside = "C:/Windows/win.ini" if os.name == "nt" else "/etc/passwd"
    resp = client.post("/embeddings/ingest", json={"path": outside})
    assert resp.status_code == 400
    assert resp.json()["code"] == "path_outside_workspace"


def test_skill_forge_validate_rejects_traversal(client):
    resp = client.post("/skills/forge/validate", json={"path": "../../../etc/passwd"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "path_outside_skills_dir"


# ------------------------------------------------------- one auth owner
def test_websocket_token_policy_has_exactly_one_owner():
    """The gateway had four implementations of the token check; the fleet one
    bypassed the constant-time comparison. Divergence in a security gate is not
    cosmetic, so the duplicates must not come back."""
    import gateway.realtime as realtime
    import gateway.routes_fleet as routes_fleet
    from gateway.context import ws_token_ok

    assert not hasattr(realtime, "_auth_ok")
    assert not hasattr(routes_fleet, "_fleet_ws_auth")
    assert routes_fleet._ws_auth_ok is not None
    assert callable(ws_token_ok)
