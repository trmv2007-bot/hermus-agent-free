from __future__ import annotations

from core.control_room_state import snapshot


def test_control_room_snapshot_is_read_only_and_explainable():
    state = snapshot()
    assert set(("executive", "world", "memory", "perception", "specialists")) <= set(state)
    assert isinstance(state["executive"]["active_goals"], list)
    assert isinstance(state["executive"]["recent_events"], list)
    assert isinstance(state["perception"]["statuses"], list)
    assert state["memory"]["available"] is True
    assert state["specialists"]["max_agents"] >= 1
