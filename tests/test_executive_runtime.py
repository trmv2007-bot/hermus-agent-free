"""Tests for the Executive Brain -> canonical runtime integration seam."""

from __future__ import annotations

from core.executive import ExecutiveBrain
from core.executive_runtime import execute_with_executive


def test_executive_bridge_creates_goal_and_passes_generated_requirements(monkeypatch, tmp_path):
    brain = ExecutiveBrain(tmp_path / "executive.sqlite3")
    captured = {}

    def fake_execute(text, **kwargs):
        captured["text"] = text
        captured.update(kwargs)
        return {
            "response": "completed",
            "run_kind": "mission",
            "state": "completed",
            "mission_id": "msn_test",
            "verified": True,
        }

    monkeypatch.setattr("core.runtime.execute", fake_execute)

    result = execute_with_executive(
        "Build a small test app",
        brain=brain,
        prefer="mission",
        platform="test",
        user_id="tester",
    )

    assert captured["text"] == "Build a small test app"
    assert captured["requirements"] == ["The requested objective is completed and independently verified."]
    assert captured["subgoals"]
    assert result["executive"]["planned"] is True
    assert result["executive"]["goal_id"].startswith("goal_")
    assert brain.active_goals() == []

    events = brain.recent_events(limit=20)
    event_types = {event["event_type"] for event in events}
    assert "executive_handoff" in event_types
    assert "mission_completed" in event_types


def test_explicit_requirements_are_preserved(monkeypatch, tmp_path):
    brain = ExecutiveBrain(tmp_path / "executive.sqlite3")
    captured = {}

    def fake_execute(text, **kwargs):
        captured.update(kwargs)
        return {"response": "ok", "run_kind": "mission", "state": "completed"}

    monkeypatch.setattr("core.runtime.execute", fake_execute)

    execute_with_executive(
        "Create the service",
        brain=brain,
        prefer="mission",
        requirements=["All tests pass", "No unapproved network writes"],
    )

    assert captured["requirements"] == ["All tests pass", "No unapproved network writes"]


def test_chat_does_not_create_an_executive_goal(monkeypatch, tmp_path):
    brain = ExecutiveBrain(tmp_path / "executive.sqlite3")

    def fake_execute(text, **kwargs):
        return {"response": "hello", "run_kind": "chat"}

    monkeypatch.setattr("core.runtime.execute", fake_execute)

    result = execute_with_executive("Hello HERMUS", brain=brain, prefer="chat")

    assert result["run_kind"] == "chat"
    assert brain.active_goals() == []
