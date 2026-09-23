"""Tests for Fleet Orchestrator (SPEC §7) - roadmap step 4."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.fleet.bus import FleetBus
from core.fleet.missions import (
    GATE_AFTER_DECOMPOSE,
    GATE_AFTER_REVIEW,
    GATE_ON_BUDGET_WARNING,
    GATE_ON_CONFLICT,
    GATE_ON_STALL_REPLAN,
    POLICY_ASK_USER,
    POLICY_FIRST_RESULT,
    POLICY_HIGHEST_RELIABILITY,
    POLICY_JUDGE_MODEL,
)
from core.fleet.orchestrator import OrchestrationResult, Orchestrator
from core.fleet.registry import FleetRegistry


def _stub_chat_fn(messages): return {"content": "Test response from agent", "tokens": 10}
def _stub_planner(goal): return [f"Research: {goal}", f"Plan: {goal}", f"Execute: {goal}"]
def _stub_verifier(subtask, result): return True, "verified"
def _stub_judge(goal, candidates): return candidates[0]["result"] if candidates else "no results"

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
            try: reg.dismiss(a.agent_id, confirm=True)
            except: pass

@pytest.fixture()
def orchestrator(fleet_registry):
    orch = Orchestrator(fleet_registry, fleet_registry.bus, planner=_stub_planner, verifier=_stub_verifier, judge=_stub_judge)
    yield orch
    orch.close()

@pytest.mark.asyncio
async def test_orchestrate_basic(orchestrator, fleet_registry):
    """Single-agent orchestration completes all three subtasks.

    This was skipped as "a known issue where subtasks are not completed". The
    real cause was in the orchestrator (the default decomposer's result was
    discarded, so missions never left PROPOSED) and in the review phase (the
    string-returning Judge was read as a dict, so every judged subtask was
    marked failed). Both are fixed; single- and multi-agent behave the same.
    """
    agent = fleet_registry.spawn({"name": "DefaultWorker", "provider": "groq"})
    await asyncio.sleep(1.0)

    r = await orchestrator.orchestrate(
        goal="Build a simple calculator app",
        budget_tokens=5000,
        agent_ids=[agent.agent_id],
    )

    assert r.success
    assert r.mission_id
    assert r.synthesis
    assert r.synthesis["goal"] == "Build a simple calculator app"
    assert r.synthesis["subtasks_total"] == 3
    assert r.synthesis["subtasks_completed"] == 3

@pytest.mark.asyncio
async def test_orchestrate_with_agent_ids(orchestrator, fleet_registry):
    a1 = fleet_registry.spawn({"name": "Worker1", "provider": "groq"})
    a2 = fleet_registry.spawn({"name": "Worker2", "provider": "groq"})
    r = await orchestrator.orchestrate(goal="Build a simple calculator app", budget_tokens=5000, agent_ids=[a1.agent_id, a2.agent_id])
    assert r.success
    # `success` alone proved nothing: it used to be hardcoded True.
    assert r.synthesis["subtasks_total"] == 3
    assert r.synthesis["subtasks_completed"] == 3
    assert r.synthesis["subtasks_missing"] == []
    assert orchestrator.missions.get(r.mission_id).state == "done"

@pytest.mark.asyncio
async def test_orchestrate_without_planner_still_decomposes(fleet_registry):
    """Production builds the orchestrator with no planner, so the default
    decomposer must reach MissionManager.decompose — that is what publishes
    subtasks and moves the mission out of PROPOSED."""
    orch = Orchestrator(fleet_registry, fleet_registry.bus, verifier=_stub_verifier, judge=_stub_judge)
    assert orch.planner is None
    r = await orch.orchestrate(goal="Ship the release", budget_tokens=5000)
    assert r.synthesis is not None, f"orchestration failed: {r.error}"
    assert r.synthesis["subtasks_total"] == 3, "default decomposer produced no subtasks"
    assert r.synthesis["subtasks_completed"] == 3
    assert r.success

@pytest.mark.asyncio
async def test_judge_veto_fails_the_subtask_it_rejects(fleet_registry):
    """A Judge returns text, so a veto is an explicit marker; correctness is
    otherwise the verifier's call."""
    def veto_judge(prompt, messages): return "FAIL: result does not answer the subtask"
    orch = Orchestrator(fleet_registry, fleet_registry.bus, planner=_stub_planner, verifier=_stub_verifier, judge=veto_judge)
    r = await orch.orchestrate(goal="Build a simple calculator app", budget_tokens=5000)
    assert r.success is False
    assert r.synthesis["subtasks_completed"] == 0
    assert r.error and "0/3" in r.error

@pytest.mark.asyncio
async def test_empty_agent_result_is_not_success(fleet_registry):
    """An agent that returns nothing must not let the mission claim success."""
    def _no_result_chat(messages): return {"content": "", "tokens": 0}
    reg = FleetRegistry(fleet_registry.bus, chat_fn=_no_result_chat)
    orch = Orchestrator(reg, reg.bus, planner=_stub_planner, verifier=_stub_verifier, judge=_stub_judge)
    r = await orch.orchestrate(goal="Build a simple calculator app", budget_tokens=5000)
    assert r.success is False
    assert r.synthesis["subtasks_completed"] == 0

@pytest.mark.asyncio
async def test_drive_round_closes_a_finished_mission(fleet_registry):
    """A watchdog round that lands the last subtask must close the mission.

    Without this the mission stays WORKING with nothing left to do and the
    watchdog re-drives it on every tick.
    """
    orch = Orchestrator(fleet_registry, fleet_registry.bus, planner=_stub_planner, verifier=_stub_verifier, judge=_stub_judge)
    m = orch.missions.open(goal="Wrap up the release", budget_tokens=5000)
    orch.missions.decompose(m.mission_id, _stub_planner)

    res = await orch.drive_round(m.mission_id)
    assert res["ok"] is True, res
    assert res["closed"] is True, f"all subtasks landed but the mission stayed open: {res}"
    assert orch.missions.get(m.mission_id).state == "done"

@pytest.mark.asyncio
async def test_orchestrate_budget_enforcement(orchestrator):
    r = await orchestrator.orchestrate(goal="Build a simple calculator app", budget_tokens=1)
    assert r.success is False
    assert "budget" in r.error.lower() or r.partial

@pytest.mark.asyncio
async def test_orchestrate_conflict_resolution_first_result(orchestrator):
    r = await orchestrator.orchestrate(goal="Build a simple calculator app", budget_tokens=5000, resolution_policy=POLICY_FIRST_RESULT)
    assert r.success
    assert r.synthesis["resolution_policy"] == POLICY_FIRST_RESULT

@pytest.mark.asyncio
async def test_verifier_called(orchestrator):
    calls = []
    def tv(s, r): calls.append({"s":s,"r":r}); return True, "v"
    o = Orchestrator(orchestrator.registry, orchestrator.bus, planner=_stub_planner, verifier=tv, judge=_stub_judge)
    r = await o.orchestrate(goal="Build a simple calculator app", budget_tokens=5000)
    assert r.success
    assert len(calls) == 3

@pytest.mark.asyncio
async def test_synthesis_includes_budget_info(orchestrator):
    r = await orchestrator.orchestrate(goal="Build a simple calculator app", budget_tokens=5000)
    assert r.synthesis
    assert "budget_used" in r.synthesis
    assert "budget_total" in r.synthesis
    assert r.synthesis["budget_total"] == 5000

@pytest.mark.asyncio
async def test_hitl_gate_stops_rather_than_auto_approving(fleet_registry):
    """A requested gate must actually hold the mission for a human."""
    orch = Orchestrator(fleet_registry, fleet_registry.bus, planner=_stub_planner, verifier=_stub_verifier, judge=_stub_judge)
    r = await orch.orchestrate(goal="Ship the release", budget_tokens=5000, hitl_gates=[GATE_AFTER_DECOMPOSE])
    assert r.state == "suspended", f"gate did not hold the mission: {r}"
    assert r.success is False
    assert GATE_AFTER_DECOMPOSE in (r.error or "")

@pytest.mark.asyncio
async def test_mock_fallback_result_is_not_a_completed_subtask(fleet_registry):
    """core.llm's local mock fallback means no model was reachable — counting it
    as a completed subtask is exactly the fabricated success the honesty
    contract forbids."""
    def _mock_chat(messages): return {"content": "Fallback mock for: " + str(messages[-1].get("content"))[:40], "tokens": 5}
    reg = FleetRegistry(fleet_registry.bus, chat_fn=_mock_chat)
    orch = Orchestrator(reg, reg.bus, planner=_stub_planner, verifier=_stub_verifier, judge=_stub_judge)
    r = await orch.orchestrate(goal="Build a simple calculator app", budget_tokens=5000)
    assert r.success is False
    assert r.synthesis["subtasks_completed"] == 0
    assert r.synthesis["subtasks_total"] == 3

@pytest.mark.asyncio
async def test_orchestrator_close(orchestrator):
    orchestrator.close()
    orchestrator.close()

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--basetemp=.pytest_tmp_orch"])
