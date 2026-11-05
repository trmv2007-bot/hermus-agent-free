"""Contracts for the shared dashboard state and agent-facing layout tools."""

from __future__ import annotations

from pathlib import Path

from core.dashboard_state import DashboardState


def test_dashboard_layout_persists_and_tracks_tabs(tmp_path: Path):
    path = tmp_path / "dashboard.json"
    state = DashboardState(str(path))
    panel = state.add_panel("Evidence", "verified", kind="text", width=8)
    state.move_panel(panel["id"], order=0)
    state.update_tab("agents", label="Crew", visible=False, order=0)

    restored = DashboardState(str(path)).snapshot()
    assert restored["panels"][0]["title"] == "Evidence"
    assert restored["panels"][0]["width"] == 8
    agents = next(tab for tab in restored["tabs"] if tab["id"] == "agents")
    assert agents["label"] == "Crew"
    assert agents["visible"] is False


def test_dashboard_panel_mutations_are_bounded(tmp_path: Path):
    state = DashboardState(str(tmp_path / "dashboard.json"))
    panel = state.add_panel("Safe", "plain text", kind="text")
    updated = state.update_panel(panel["id"], title="Changed", content="new", visible=False)
    assert updated["title"] == "Changed"
    assert updated["visible"] is False

    try:
        state.add_panel("Bad", "<script>alert(1)</script>", kind="html")
    except ValueError as exc:
        assert "kind" in str(exc)
    else:
        raise AssertionError("unsupported panel kind must be rejected")
