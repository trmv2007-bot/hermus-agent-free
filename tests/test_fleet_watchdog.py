"""Tests for the fleet watchdog -- the guarded autonomy layer (Ultron cage)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.fleet.bus import FleetBus
from core.fleet.orchestrator import Orchestrator
from core.fleet.registry import ERROR, IDLE, FleetRegistry
from core.fleet.watchdog import FleetWatchdog, install_watchdog


def _stub_chat_fn(messages):
    return {"content": "Test response from agent", "tokens": 10}


def _stub_planner(goal):
    return [f"Research: {goal}", f"Plan: {goal}"]


def _stub_verifier(subtask, result):
    return True, "verified"


def _boom(messages):
    raise RuntimeError("model down")


@pytest.fixture()
def fleet_bus(tmp_path):
    bus = FleetBus(base_dir=tmp_path / "fleet", fsync=False, snapshot_every_events=0, snapshot_interval_s=0)
    yield bus
    bus.close()


@pytest.fixture()
def fleet_registry(fleet_bus):
    reg = FleetRegistry(fleet_bus, chat_fn=_stub_chat_fn)
    yield reg
    for a in reg.list():
        if a.state != "DESTROYED":
            try:
                reg.dismiss(a.agent_id, confirm=True)
            except Exception:
                pass


@pytest.fixture()
def orchestrator(fleet_registry):
    orch = Orchestrator(fleet_registry, fleet_registry.bus, planner=_stub_planner, verifier=_stub_verifier)
    yield orch
    orch.close()


@pytest.fixture()
def watchdog(orchestrator):
    return FleetWatchdog(orchestrator, interval=0.01)


def _fail_once(reg, agent_id, text):
    try:
        reg.assign(agent_id, text)
    except Exception:
        pass  # assign re-raises after WORKING -> ERROR; the state is what we want


def _spawn_errored(tmp_path, name):
    """Drive an agent into ERROR through the real path: a failing model call."""
    bus = FleetBus(base_dir=tmp_path / f"fleet-{name}", fsync=False, snapshot_every_events=0, snapshot_interval_s=0)
    reg = FleetRegistry(bus, chat_fn=_boom)
    agent = reg.spawn({"name": name, "provider": "groq"})
    _fail_once(reg, agent.agent_id, "trigger a failure please")
    return bus, reg, agent


def test_heal_recovers_error_agent_and_audits_it(tmp_path):
    bus, reg, agent = _spawn_errored(tmp_path, "HealMe")
    try:
        assert reg.get(agent.agent_id).state == ERROR
        wd = FleetWatchdog(Orchestrator(reg, reg.bus), interval=0.01)

        acts = wd.heal()

        assert len(acts) == 1 and acts[0]["act"] == "agent_recovered"
        assert reg.get(agent.agent_id).state == IDLE
        assert wd.counters["recovered"] == 1
        events = bus.tail(0, kind="fleet.watchdog.agent_recovered")
        assert len(events) == 1 and events[0].sender == "fleet-watchdog"
    finally:
        bus.close()


def test_restart_budget_caps_self_healing(tmp_path):
    bus, reg, agent = _spawn_errored(tmp_path, "Flaky")
    try:
        wd = FleetWatchdog(Orchestrator(reg, reg.bus), interval=0.01, max_restarts_per_hour=1)
        assert len(wd.heal()) == 1

        _fail_once(reg, agent.agent_id, "fail again please")
        assert reg.get(agent.agent_id).state == ERROR

        assert wd.heal() == []  # budget spent: no silent restart loop
        assert reg.get(agent.agent_id).state == ERROR
    finally:
        bus.close()


@pytest.mark.asyncio
async def test_drive_moves_proposed_mission_with_idle_agent(watchdog, orchestrator, fleet_registry, fleet_bus):
    agent = fleet_registry.spawn({"name": "Driver", "provider": "groq"})
    mission = orchestrator.missions.open(goal="Research and summarize the fleet watchdog", budget_tokens=5000)
    orchestrator.missions.decompose(mission.mission_id, _stub_planner)
    assert any(s.status == "open" for s in orchestrator.missions.get(mission.mission_id).subtasks)

    acts = await watchdog.drive()

    assert len(acts) == 1 and acts[0]["act"] == "mission_driven"
    assert acts[0]["ok"] is True
    after = orchestrator.missions.get(mission.mission_id)
    assert any(s.status in ("claimed", "working", "done") for s in after.subtasks)
    assert fleet_bus.tail(0, kind="fleet.watchdog.mission_driven")


@pytest.mark.asyncio
async def test_drive_skips_when_no_idle_agent(watchdog, orchestrator, fleet_registry):
    mission = orchestrator.missions.open(goal="Research and summarize nothing at all", budget_tokens=5000)
    orchestrator.missions.decompose(mission.mission_id, _stub_planner)

    assert await watchdog.drive() == []
    assert watchdog.counters["skipped_no_idle"] >= 1


@pytest.mark.asyncio
async def test_brake_outranks_autonomy(tmp_path, monkeypatch):
    class _Brake:
        def state(self):
            return type("S", (), {"to_dict": lambda self: {"active": True}})()

    monkeypatch.setattr("core.emergency_stop.get_emergency_stop", lambda: _Brake())
    bus, reg, agent = _spawn_errored(tmp_path, "Braked")
    try:
        wd = FleetWatchdog(Orchestrator(reg, reg.bus), interval=0.01)

        res = await wd.tick()

        assert res["reason"] == "brake" and res["acts"] == []
        assert reg.get(agent.agent_id).state == ERROR  # untouched while braked
        assert wd.counters["skipped_brake"] == 1
    finally:
        bus.close()


@pytest.mark.asyncio
async def test_disabled_watchdog_acts_not(tmp_path):
    bus, reg, agent = _spawn_errored(tmp_path, "Quiet")
    try:
        wd = FleetWatchdog(Orchestrator(reg, reg.bus), interval=0.01, enabled=False)

        res = await wd.tick()

        assert res["reason"] == "disabled" and res["acts"] == []
        assert reg.get(agent.agent_id).state == ERROR
    finally:
        bus.close()


def test_gateway_routes_report_watchdog_honestly(orchestrator):
    from fastapi.testclient import TestClient

    from gateway.gateway import app

    install_watchdog(FleetWatchdog(orchestrator))
    with TestClient(app) as client:
        st = client.get("/api/fleet/watchdog").json()
        assert st["ok"] is True and st["enabled"] is True
        tog = client.post("/api/fleet/watchdog/toggle", json={"enabled": False}).json()
        assert tog["ok"] is True and tog["enabled"] is False
        st2 = client.get("/api/fleet/watchdog").json()
        assert st2["enabled"] is False
        install_watchdog(FleetWatchdog(orchestrator))  # restore for other tests
