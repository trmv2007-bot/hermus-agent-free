"""Queueing a proactive check-in is NOT delivery.

The gateway used to call mark_checkin() the moment a job was submitted. If that
job then failed - model error, no client connected, queue rejection - the goal
was still marked as checked in and never raised again. The assistant would
permanently forget to follow up on the user's goal, with no error anywhere.

These tests pin the honest behaviour: queueing marks the check-in IN FLIGHT and
leaves it due, so a real delivery (or a retry) can still happen.
"""

from __future__ import annotations

import pytest

from core.presence import PresenceManager as PresenceStore


@pytest.fixture()
def store(tmp_path):
    return PresenceStore(state_path=tmp_path / "presence.json")


def _aged_goal(store: PresenceStore, minutes_old: int = 600) -> str:
    res = store.add_goal("finish the remodel", priority=1)
    gid = res["goal"]["id"]
    with store._lock:  # noqa: SLF001
        for g in store._data["goals"]:  # noqa: SLF001
            if g.get("id") == gid:
                g["last_touched_at"] = "2000-01-01T00:00:00+00:00"
                g["created_at"] = "2000-01-01T00:00:00+00:00"
        store._save_locked()  # noqa: SLF001
    return gid


def test_marking_pending_does_not_count_as_delivered(store) -> None:
    gid = _aged_goal(store)
    out = store.mark_checkin_pending(gid)
    assert out["success"] is True
    assert out["delivered"] is False
    goal = next(g for g in store.list_goals(status="active") if g["id"] == gid)
    # The whole point: not acknowledged.
    assert goal.get("last_checkin_at") is None
    assert goal.get("checkin_count", 0) == 0
    # But it is recorded as in flight, so it can be deduplicated.
    assert goal.get("checkin_pending_at")


def test_goal_stays_due_after_being_queued(store) -> None:
    """The regression: a queued-then-failed turn must not silence the goal."""
    gid = _aged_goal(store)
    store.mark_checkin_pending(gid)
    due_ids = [d.get("id") for d in store.check_ins_due()]
    assert gid in due_ids, "goal went silent after queueing - this is the bug"


def test_pending_then_real_delivery_acknowledges(store) -> None:
    gid = _aged_goal(store)
    store.mark_checkin_pending(gid)
    out = store.deliver_check_ins(sink=lambda m: None)
    assert out["delivered"] == [gid]
    assert [d.get("id") for d in store.check_ins_due()] != [gid] or not [
        d for d in store.check_ins_due() if d.get("id") == gid
    ]


def test_real_delivery_after_failed_queue_still_counts(store) -> None:
    gid = _aged_goal(store)
    store.mark_checkin_pending(gid)
    # A sink that fails must leave it pending...
    failed = store.deliver_check_ins(sink=lambda m: (_ for _ in ()).throw(RuntimeError("offline")))
    assert failed["delivered"] == []
    assert [d for d in store.check_ins_due() if d.get("id") == gid]
    # ...and a later working sink can still deliver it.
    ok = store.deliver_check_ins(sink=lambda m: None)
    assert gid in ok["delivered"]


def test_mark_pending_unknown_goal_reports_error(store) -> None:
    out = store.mark_checkin_pending("goal_does_not_exist")
    assert out["success"] is False
    assert "not found" in out["error"]
