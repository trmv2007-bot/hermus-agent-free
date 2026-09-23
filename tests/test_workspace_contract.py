"""The workspace front end may only call endpoints that exist.

The panels were written against assumed response shapes twice before this test
existed — the key-health path was wrong and the model catalogue is keyed
``catalog``, not ``models``. Rather than trust the client, read its paths out of
the TypeScript and check them against the live route table.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
CLIENT_TS = ROOT / "frontend" / "src" / "api" / "client.ts"

_PATH_LITERAL = re.compile(r'(?:get|post)[<(][^)]*?\(\s*"(/[a-z0-9\-_/]+)"', re.IGNORECASE)


def _client() -> TestClient:
    from gateway.gateway import app

    return TestClient(app)


def test_the_client_file_declares_the_endpoints_we_expect():
    """Guards the regex: if the client's shape changes, this test must be updated,
    not quietly vacuous."""
    found = set(_PATH_LITERAL.findall(CLIENT_TS.read_text(encoding="utf-8")))
    assert {"/keys/health", "/engine/models", "/api/fleet/agents", "/api/v1/commands"} <= found, found


def test_every_endpoint_the_workspace_calls_is_routable():
    """Checked against the live OpenAPI schema rather than ``app.routes``: the
    routers are attached in ways the top-level route list does not reflect, but
    the schema is built from what actually resolves."""
    paths = set(_PATH_LITERAL.findall(CLIENT_TS.read_text(encoding="utf-8")))
    assert paths, "the client declares no endpoints — check the regex"

    schema = _client().get("/openapi.json").json()
    routes = set(schema.get("paths", {}))
    missing = sorted(path for path in paths if path not in routes)
    assert not missing, f"called by the workspace but not routable: {missing}"


def test_the_mission_document_carries_what_the_mission_panel_reads(tmp_path, monkeypatch):
    import core.mission as mission_module
    from core.mission import MissionEngine

    engine = MissionEngine(executor=lambda node, ctx: {"success": True, "output": "done", "evidence": [{"check": "ok", "status": "passed"}]}, storage_dir=tmp_path / "missions")
    report = engine.start_mission("contract check", requirements=["a thing"], budget_steps=6)
    monkeypatch.setattr(mission_module, "mission_engine", engine)

    body = _client().get(f"/missions/{report.mission_id}").json()
    for field in ("mission_id", "goal", "state", "outcome_state", "requirements", "agent_claim", "verified_result", "disagreements", "evidence_refs"):
        assert field in body, field

    requirement = body["requirements"][0]
    for field in ("id", "description", "satisfied", "status", "oracle", "evidence"):
        assert field in requirement, field


def test_the_evidence_endpoints_return_what_the_panel_reads(tmp_path, monkeypatch):
    import core.mission as mission_module
    from core.mission import MissionEngine

    engine = MissionEngine(executor=lambda node, ctx: {"success": True, "output": "done", "evidence": [{"check": "ok", "status": "passed"}]}, storage_dir=tmp_path / "missions")
    report = engine.start_mission("evidence contract", requirements=["a thing"], budget_steps=6)
    monkeypatch.setattr(mission_module, "mission_engine", engine)
    client = _client()

    listed = client.get(f"/missions/{report.mission_id}/evidence").json()
    assert {"mission_id", "outcome_state", "digest", "evidence", "referenced_by_report"} <= set(listed)
    if listed["evidence"]:
        entry = listed["evidence"][0]
        assert {"id", "check", "source", "summary", "kind"} <= set(entry)
        one = client.get(f"/missions/{report.mission_id}/evidence/{entry['id']}").json()
        assert "recheck" in one and one["recheck"]["found"] is True


def test_the_key_health_panel_will_not_render_fields_that_do_not_exist():
    """The client says what it reads; the endpoint has to still provide it."""
    body = _client().get("/keys/health").json()
    assert "results" in body
    for probe in body["results"][:3]:
        assert {"provider", "healthy", "success"} <= set(probe)


def test_the_roster_card_has_every_field_the_worker_centre_reads():
    """The Worker Center panel was written against ``agent_id``; the card names it
    ``id``, which would have rendered an empty roster with no error anywhere.

    Built from the projection itself rather than the live roster, because an empty
    fleet would make this test vacuously pass.
    """
    from datetime import datetime, timezone

    from gateway.routes_fleet import _agent_card

    class StubStats:
        def to_dict(self):
            return {"tasks_done": 3, "tasks_failed": 1, "tokens": 4200}

    class StubAgent:
        agent_id = "agt_1"
        name = "Friday"
        state = "IDLE"
        provider = "groq"
        model = "llama-3.3-70b"
        key_name = "groq-primary"
        skills = ["triage"]
        last_activity = datetime.now(timezone.utc).isoformat()
        created_at = last_activity
        stats = StubStats()
        summary = "handled 3 tasks"
        current_task = None

    card = _agent_card(StubAgent())
    panel_fields = {"id", "name", "state", "provider", "model", "key_name", "stats", "last_activity", "current_task"}
    missing = panel_fields - set(card)
    assert not missing, f"the fleet card no longer carries {missing}"
    assert card["id"] == "agt_1", "the panel keys React rows off `id`"
    assert {"tasks_done", "tasks_failed", "tokens"} <= set(card["stats"])


def test_the_model_panel_reads_the_catalogue_key_the_gateway_sends():
    body = _client().get("/engine/models").json()
    assert "catalog" in body, "the workspace client maps `catalog`; a rename here breaks the panel"
    if body["catalog"]:
        assert {"id", "name"} <= set(body["catalog"][0])
