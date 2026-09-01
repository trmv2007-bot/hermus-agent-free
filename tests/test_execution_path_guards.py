from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_subsystem_routes_have_permission_guard_for_risky_screen_and_research_actions():
    src = (ROOT / "gateway/routes_subsystems.py").read_text(encoding="utf-8")
    assert "def _permission_guard" in src
    for token in (
        '_permission_guard("screen_record_start"',
        '_permission_guard("screen_record_save"',
        '_permission_guard("screen_watch"',
        '_permission_guard("screen_action_before"',
        '_permission_guard("screen_action_after"',
        '_permission_guard("web_search"',
    ):
        assert token in src, f"missing route-level permission guard: {token}"


def test_computer_routes_have_permission_guard_for_task_delete_and_delegation_actions():
    src = (ROOT / "gateway/routes_computer.py").read_text(encoding="utf-8")
    assert "def _permission_guard" in src
    for token in (
        '_permission_guard("computer_task"',
        '_permission_guard("delete_file"',
    ):
        assert token in src, f"missing computer route-level permission guard: {token}"
    assert src.count('_permission_guard("computer_task"') >= 4


def test_computer_and_remote_emergency_routes_mirror_global_red_line_brake():
    src = (ROOT / "gateway/routes_computer.py").read_text(encoding="utf-8")
    assert "get_emergency_stop().activate" in src
    assert "get_emergency_stop().clear" in src
    assert "set_by=\"computer-route\"" in src
    assert "set_by=\"remote-route\"" in src


def test_control_room_computer_task_payload_matches_route_contract():
    src = (ROOT / "gateway/control.html").read_text(encoding="utf-8")
    assert 'body: JSON.stringify({ task: task })' in src
    assert 'body: JSON.stringify({ objective: task })' not in src
