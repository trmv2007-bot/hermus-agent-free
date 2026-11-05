from __future__ import annotations

from core.executive import ExecutiveBrain


def test_executive_goal_plan_and_handoff(tmp_path):
    brain = ExecutiveBrain(tmp_path / "executive.sqlite3")

    goal_id = brain.create_goal("Build and test a small web app", priority=80)
    plan = brain.plan_goal(
        "Build and test a small web app",
        goal_id=goal_id,
        priority=80,
        success_criteria=["Application exists", "Tests pass"],
    )

    assert plan.goal_id == goal_id
    assert [step.id for step in plan.steps] == ["understand", "plan", "execute", "verify", "repair"]

    handoff = brain.handoff(plan)
    assert handoff["goal_id"] == goal_id
    assert handoff["requirements"] == ["Application exists", "Tests pass"]
    assert "Build and test a small web app" in handoff["subgoals"]


def test_executive_persists_state_and_observations(tmp_path):
    db = tmp_path / "executive.sqlite3"
    brain = ExecutiveBrain(db)

    brain.set_state("presence", {"mode": "focused"})
    event_id = brain.observe("heartbeat", {"healthy": True})

    reopened = ExecutiveBrain(db)
    assert reopened.get_state("presence") == {"mode": "focused"}
    events = reopened.recent_events(limit=5)
    assert any(event["event_id"] == event_id and event["event_type"] == "heartbeat" for event in events)


def test_executive_goal_status_is_bounded(tmp_path):
    brain = ExecutiveBrain(tmp_path / "executive.sqlite3")
    goal_id = brain.create_goal("Do something", priority=101)

    assert brain.update_goal(goal_id, status="paused", priority=-10)
    assert brain.active_goals() == []

    try:
        brain.update_goal(goal_id, status="wild")
    except ValueError as exc:
        assert "invalid executive goal status" in str(exc)
    else:
        raise AssertionError("invalid status should be rejected")
