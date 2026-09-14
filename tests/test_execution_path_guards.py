from __future__ import annotations

import re
from pathlib import Path

from _control_room_source import control_room_source

ROOT = Path(__file__).resolve().parents[1]


def _guard_call(src: str, tool: str) -> int:
    """Count ``_permission_guard("<tool>", ...)`` calls, tolerating the
    formatter splitting the call across lines."""
    return len(re.findall(r'_permission_guard\(\s*"' + re.escape(tool) + r'"', src))


def test_subsystem_routes_have_permission_guard_for_risky_screen_and_research_actions():
    src = (ROOT / "gateway/routes_subsystems.py").read_text(encoding="utf-8")
    assert "def _permission_guard" in src
    for tool in (
        "screen_record_start",
        "screen_record_save",
        "screen_watch",
        "screen_action_before",
        "screen_action_after",
        "web_search",
    ):
        assert _guard_call(src, tool) >= 1, f"missing route-level permission guard: {tool}"


def test_computer_routes_have_permission_guard_for_task_delete_and_delegation_actions():
    src = (ROOT / "gateway/routes_computer.py").read_text(encoding="utf-8")
    assert "def _permission_guard" in src
    for tool in (
        "computer_task",
        "delete_file",
    ):
        assert _guard_call(src, tool) >= 1, f"missing computer route-level permission guard: {tool}"
    assert _guard_call(src, "computer_task") >= 4


def test_computer_and_remote_emergency_routes_mirror_global_red_line_brake():
    src = (ROOT / "gateway/routes_computer.py").read_text(encoding="utf-8")
    assert "get_emergency_stop().activate" in src
    assert "get_emergency_stop().clear" in src
    assert 'set_by="computer-route"' in src
    assert 'set_by="remote-route"' in src


def test_control_room_computer_task_payload_matches_route_contract():
    src = control_room_source()
    assert "body: JSON.stringify({ task: task })" in src
    assert "body: JSON.stringify({ objective: task })" not in src
