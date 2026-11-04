"""Harness API + execution-path contracts.

Two things are proven here:

* the read-only ``/api/v1/harness/*`` projections return real shapes, 404 for
  an unknown session, and never leak message bodies in the overview;
* ``swarm.run_workers`` executes through the canonical delegation queue instead
  of constructing an agent — the split-brain gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from gateway.gateway import app

    return TestClient(app)


def test_harness_overview_is_real_and_body_free(client):
    r = client.get("/api/v1/harness/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert {"sessions", "bus", "files", "budget"} <= body.keys()
    assert isinstance(body["sessions"]["total"], int)
    assert isinstance(body["sessions"]["by_status"], dict)
    assert body["budget"]["tokens"] > 0
    # The overview is a count projection; it must not carry message bodies.
    assert "preview" not in str(body)
    assert "read_by" not in str(body)


def test_harness_sessions_list_and_unknown_session_404(client):
    listing = client.get("/api/v1/harness/sessions?limit=5").json()
    assert listing["ok"] is True
    assert isinstance(listing["sessions"], list)
    assert listing["limit"] == 5

    missing = client.get("/api/v1/harness/sessions/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["success"] is False


def test_harness_events_shape(client):
    body = client.get("/api/v1/harness/events?limit=5").json()
    assert body["ok"] is True
    assert isinstance(body["events"], list)
    for row in body["events"]:
        assert row["kind"] in ("file_shift", "mail")


def test_swarm_runs_through_the_canonical_queue(monkeypatch, tmp_path):
    from core.harness import sessions, swarm

    monkeypatch.setattr("core.harness.sessions._dir", lambda: tmp_path / "sessions")
    (tmp_path / "sessions").mkdir(exist_ok=True)
    monkeypatch.setattr("core.harness.bus._path", lambda: tmp_path / "bus.json")

    calls = {}

    def fake_delegate(goal, tasks=None, **kwargs):
        calls["goal"] = goal
        calls["tasks"] = tasks
        return {
            "ok": True,
            "nodes": [
                {"task": t, "answer": f"answer for {t[:8]}", "success": True} for t in (tasks or [])
            ],
        }

    monkeypatch.setattr("subagents.subagent.delegate", fake_delegate)
    out = swarm.run_workers("split the work", "coord-q", count=3)

    assert calls["goal"] == "split the work"
    assert len(calls["tasks"]) == 3, "one delegated task per worker"
    assert out["ok"] is True
    assert len(out["results"]) == 3
    assert all(r["success"] for r in out["results"])
    # every worker session landed in a terminal state
    for worker in out["workers"]:
        assert sessions.get(worker["id"])["status"] == "done"


def test_swarm_never_constructs_an_agent():
    """Gate: a second execution path would bypass the JobQueue lifecycle."""
    source = Path("core/harness/swarm.py").read_text(encoding="utf-8")
    assert "HermusAgent" not in source
    assert "subagents.subagent" in source


def test_swarm_reports_failure_honestly(monkeypatch, tmp_path):
    from core.harness import swarm

    monkeypatch.setattr("core.harness.sessions._dir", lambda: tmp_path / "sessions")
    (tmp_path / "sessions").mkdir(exist_ok=True)
    monkeypatch.setattr("core.harness.bus._path", lambda: tmp_path / "bus.json")
    monkeypatch.setattr(
        "subagents.subagent.delegate",
        lambda *a, **k: {"ok": False, "error": "queue unavailable", "nodes": []},
    )
    out = swarm.run_workers("do work", "coord-f", count=2)
    assert out["ok"] is False
    assert out["error"] == "queue unavailable"
    assert all(r["success"] is False for r in out["results"])
