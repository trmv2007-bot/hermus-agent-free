"""Tests for the durable identity/presence continuity layer."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from core.config import config
from core.presence import PresenceManager


def test_presence_has_identity_and_survives_restart(tmp_path):
    path = tmp_path / "presence.json"
    first = PresenceManager(path)
    first.update_identity(
        name="Hermus Prime",
        role="A calm local engineering partner",
        tone="warm and direct",
        values=["honesty", "continuity"],
        greeting="Good to see you.",
    )
    first.begin_turn("Build the release checklist", session_id="s1", run_id="r1")
    first.finish_turn(
        goal="Build the release checklist",
        response="The checklist is ready.",
        session_id="s1",
        run_id="r1",
    )

    second = PresenceManager(path)
    snapshot = second.snapshot()
    assert snapshot["identity"]["name"] == "Hermus Prime"
    assert snapshot["presence"]["state"] == "idle"
    assert snapshot["presence"]["detail"] == "ready"
    assert any(m["kind"] == "turn_completed" for m in snapshot["moments"])
    assert "Hermus Prime" in second.prompt_block(session_id="s1")
    assert "The checklist is ready" in second.prompt_block(session_id="s1")


def test_presence_redacts_obvious_secrets_in_continuity(tmp_path):
    manager = PresenceManager(tmp_path / "presence.json")
    manager.record_moment("note", "api_key=super-secret-value and token sk-12345678901234567890")
    summary = manager.snapshot()["moments"][-1]["summary"]
    assert "super-secret-value" not in summary
    assert "sk-12345678901234567890" not in summary
    assert "redacted" in summary


def test_goals_surface_due_checkins_and_can_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "presence_checkin_after_minutes", 30)
    manager = PresenceManager(tmp_path / "presence.json")
    created = manager.add_goal("Review the Android companion", priority=5)
    goal_id = created["goal"]["id"]

    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with manager._lock:
        manager._data["goals"][0]["last_touched_at"] = old
        manager._save_locked()

    due = manager.check_ins_due()
    assert due and due[0]["id"] == goal_id
    marked = manager.mark_checkin(goal_id)
    assert marked["success"] is True
    assert manager.check_ins_due() == []

    completed = manager.complete_goal(goal_id, note="Reviewed and documented")
    assert completed["success"] is True
    assert completed["goal"]["status"] == "completed"
    assert manager.list_goals(status="active") == []


def test_heartbeat_is_persisted_and_never_executes_work(tmp_path):
    manager = PresenceManager(tmp_path / "presence.json")
    before = manager.snapshot()["presence"]["heartbeat_count"]
    after = manager.heartbeat(force_event=True)
    assert after["presence"]["heartbeat_count"] == before + 1
    assert after["presence"]["state"] == "idle"
    stored = json.loads((tmp_path / "presence.json").read_text())
    assert stored["presence"]["heartbeat_count"] == before + 1


def test_scoped_continuity_does_not_cross_users(tmp_path):
    manager = PresenceManager(tmp_path / "presence.json")
    manager.add_goal("Alice's private goal", user_id="alice")
    manager.add_goal("Bob's private goal", user_id="bob")
    manager.record_moment("note", "Alice's private continuity", user_id="alice")
    manager.record_moment("note", "Bob's private continuity", user_id="bob")

    alice = manager.snapshot(user_id="alice")
    bob_prompt = manager.prompt_block(user_id="bob")
    assert any(g["title"] == "Alice's private goal" for g in alice["goals"])
    assert all(g["title"] != "Bob's private goal" for g in alice["goals"])
    assert "Bob's private continuity" in bob_prompt
    assert "Alice's private continuity" not in bob_prompt


def test_nested_moment_metadata_is_redacted_before_persistence(tmp_path):
    path = tmp_path / "presence.json"
    manager = PresenceManager(path)
    manager.record_moment(
        "note", "safe summary",
        metadata={"nested": {"password": "super-secret", "items": ["sk-12345678901234567890"]}},
    )
    stored = json.loads(path.read_text())
    metadata = stored["moments"][-1]["metadata"]
    assert metadata["nested"]["password"] == "[redacted]"
    assert metadata["nested"]["items"][0] == "[redacted-token]"


def test_read_only_runtime_temporarily_removes_all_registered_tools():
    from core.runtime import execute

    class FakeAgent:
        def __init__(self):
            self.tools = [{"function": {"name": "dangerous_tool"}}]
            self.seen_tools = None

        def chat(self, text, **kwargs):
            self.seen_tools = list(self.tools)
            return {"response": "status only"}

    agent = FakeAgent()
    original = agent.tools
    result = execute("check the status", agent=agent, prefer="chat", read_only=True)
    assert result["response"] == "status only"
    assert agent.seen_tools == []
    assert agent.tools is original
