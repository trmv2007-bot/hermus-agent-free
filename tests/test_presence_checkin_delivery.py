"""Proactive check-ins must be DELIVERED, not merely computed.

`check_ins_due()` only ever computed the due list. These tests pin the half that
makes the assistant proactive in reality: a sink gets called, and - critically -
state is only acknowledged as delivered AFTER the sink confirms. A check-in
marked done without reaching the user is the exact lie this closes.
"""

from __future__ import annotations

import pytest

from core.presence import PresenceManager as PresenceStore


@pytest.fixture()
def store(tmp_path):
    return PresenceStore(state_path=tmp_path / "presence.json")


def _aged_goal(store: PresenceStore, *, minutes: int = 600) -> str:
    """Create an active goal whose last activity is old enough to be due."""
    res = store.add_goal("finish the remodel", priority=1)
    goal_id = res["goal"]["id"]
    # Backdate last_touched_at so the rolling threshold is exceeded.
    with store._lock:  # noqa: SLF001 - test reaches into state to simulate elapsed time
        for g in store._data["goals"]:  # noqa: SLF001
            if g.get("id") == goal_id:
                g["last_touched_at"] = "2000-01-01T00:00:00+00:00"
                g["created_at"] = "2000-01-01T00:00:00+00:00"
        store._save_locked()  # noqa: SLF001
    return goal_id


def test_no_sink_reports_no_sink_instead_of_faking_delivery(store) -> None:
    """Without a sink we must say so, not pretend the user was told."""
    _aged_goal(store)
    assert store.check_ins_due(), "precondition: a goal is due"
    # Force the no-sink path even if a global sink happens to be configured.
    store._sink = None  # noqa: SLF001
    out = store.deliver_check_ins()
    assert out["reason"] == "no_sink"
    assert out["success"] is False
    assert out["delivered"] == []
    assert out["skipped"]


def test_sink_receives_the_message_and_goal_is_acknowledged(store) -> None:
    goal_id = _aged_goal(store)
    got: list[str] = []

    def sink(message: str) -> None:
        got.append(message)

    out = store.deliver_check_ins(sink=sink)
    assert out["delivered"] == [goal_id]
    assert len(got) == 1
    assert "finish the remodel" in got[0]
    # Now acknowledged - it should no longer be due.
    assert not [d for d in store.check_ins_due() if d.get("id") == goal_id]


def test_failed_sink_does_not_mark_delivered(store) -> None:
    """The honesty rule: a check-in the user never received stays pending."""
    goal_id = _aged_goal(store)

    def sink(message: str) -> None:
        raise RuntimeError("telegram unreachable")

    out = store.deliver_check_ins(sink=sink)
    assert out["delivered"] == []
    assert goal_id in out["skipped"]
    assert "telegram unreachable" in out["reason"]
    # Still due, so a retry is possible.
    assert [d for d in store.check_ins_due() if d.get("id") == goal_id]


def test_sink_declining_is_treated_as_failure(store) -> None:
    goal_id = _aged_goal(store)

    def sink(message: str):
        return False

    out = store.deliver_check_ins(sink=sink)
    assert out["delivered"] == []
    assert goal_id in out["skipped"]


def test_two_arg_sink_receives_the_goal_item(store) -> None:
    goal_id = _aged_goal(store)
    seen: list[dict] = []

    def sink(message: str, item: dict) -> None:
        seen.append(item)

    out = store.deliver_check_ins(sink=sink)
    assert out["delivered"] == [goal_id]
    assert seen and seen[0].get("id") == goal_id


def test_limit_is_respected(store) -> None:
    for i in range(4):
        res = store.add_goal(f"task {i}", priority=1)
        gid = res["goal"]["id"]
        with store._lock:  # noqa: SLF001
            for g in store._data["goals"]:  # noqa: SLF001
                if g.get("id") == gid:
                    g["last_touched_at"] = "2000-01-01T00:00:00+00:00"
            store._save_locked()  # noqa: SLF001

    got: list[str] = []
    out = store.deliver_check_ins(sink=lambda m: got.append(m), limit=2)
    assert len(got) == 2
    assert len(out["delivered"]) == 2


def test_nothing_due_is_a_clean_noop(store) -> None:
    out = store.deliver_check_ins(sink=lambda m: None)
    assert out["success"] is True
    assert out["delivered"] == []


def test_render_is_human_readable(store) -> None:
    msg = store._render_checkin({"title": "ship the engine", "age_minutes": 125})  # noqa: SLF001
    assert "ship the engine" in msg
    assert "2h ago" in msg
