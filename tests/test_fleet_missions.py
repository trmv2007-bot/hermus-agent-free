"""Tests for the Fleet Mission object (roadmap step 0c, SPEC §7).

Offline by design: planner / verifier / judge are deterministic injected
stubs — no network, no LLM. Every test uses an isolated FleetBus in a
tmp dir (pass --basetemp inside the repo: the machine's %TEMP% denies
pytest's cleanup).
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.fleet.bus import FleetBus
from core.fleet.missions import (
    BUDGET_WARN_RATIO,
    CLAIMING,
    DONE,
    FAILED,
    MAX_REPLANS,
    MAX_REVIEW_RETRIES,
    POLICY_ASK_USER,
    POLICY_FIRST_RESULT,
    POLICY_HIGHEST_RELIABILITY,
    POLICY_JUDGE_MODEL,
    PROPOSED,
    REVIEWING,
    STALL_LIMIT,
    SUSPENDED,
    SYNTHESIZING,
    WORKING,
    MissionError,
    MissionManager,
)


def _bus(tmp_path):
    return FleetBus(base_dir=str(tmp_path / "fleet"), fsync=False, snapshot_every_events=0, snapshot_interval_s=0)


def _planner(texts):
    return lambda goal: list(texts)


def _open(mm, **kw):
    kw.setdefault("budget_tokens", 10_000)
    return mm.open("build the thing", **kw)


# ---------- state machine ----------


def test_open_and_illegal_transitions(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    with pytest.raises(MissionError):
        mm.open("   ")
    m = _open(mm)
    assert m.state == PROPOSED
    assert m.opened_seq > 0
    with pytest.raises(MissionError):
        mm.claim(m.mission_id, "a1", "s1")  # CLAIMING required
    with pytest.raises(MissionError):
        mm.synthesize(m.mission_id)  # REVIEWING/WORKING required
    with pytest.raises(MissionError):
        mm.resolve_gate(m.mission_id, "after_decompose", action="approve")  # no pending gate


def test_decompose_propose_and_gate_default_off(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _open(mm, hitl_gates=[])
    subs = mm.decompose(m.mission_id, _planner(["alpha", "beta"]))
    assert [s.id for s in subs] == ["s1", "s2"]
    assert mm.get(m.mission_id).state == CLAIMING
    assert mm.get(m.mission_id).gates_pending == []  # after_decompose default off


def test_after_decompose_gate_suspends_and_resumes(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _open(mm, hitl_gates=["after_decompose"])
    mm.decompose(m.mission_id, _planner(["alpha"]))
    assert mm.get(m.mission_id).state == SUSPENDED
    resumed = mm.resolve_gate(m.mission_id, "after_decompose", action="approve")
    assert resumed.state == CLAIMING
    with pytest.raises(MissionError):
        mm.resolve_gate(m.mission_id, "after_decompose", action="approve")  # consumed


def test_gate_reject_fails_mission_with_partial(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _open(mm, hitl_gates=["after_decompose"])
    mm.decompose(m.mission_id, _planner(["alpha"]))
    out = mm.resolve_gate(m.mission_id, "after_decompose", action="reject")
    assert out.state == FAILED
    assert out.synthesis["partial"] is True
    assert out.synthesis["missing"] == ["s1"]


def test_gate_edit_adds_subtasks(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _open(mm, hitl_gates=["after_decompose"])
    mm.decompose(m.mission_id, _planner(["alpha"]))
    out = mm.resolve_gate(m.mission_id, "after_decompose", action="edit", edits={"subtasks": ["extra"]})
    assert out.state == CLAIMING


# ---------- claims: timed, leased, first-by-seq ----------


def _claimed(mm, budget=10_000, texts=("alpha", "beta")):
    m = _open(mm, budget_tokens=budget)
    mm.decompose(m.mission_id, _planner(list(texts)))
    return m


def test_claim_and_double_claim_rejected(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _claimed(mm)
    sub = mm.claim(m.mission_id, "a1", "s1")
    assert sub.status == "claimed" and sub.claimed_by == "a1" and sub.lease_until > 0
    with pytest.raises(MissionError):
        mm.claim(m.mission_id, "a2", "s1")


def test_first_claim_by_seq_wins_across_managers(tmp_path):
    """Two managers, same bus: whoever's CLAIM lands first on the log wins."""
    bus = _bus(tmp_path)
    mm1 = MissionManager(bus)
    mm2 = MissionManager(bus)
    m = _open(mm1)
    mm1.decompose(m.mission_id, _planner(["alpha"]))
    # mm2 rebuilt the mission independently from the log (coordinator died).
    assert mm2.rebuild_from_bus() == 1
    mm1.claim(m.mission_id, "a1", "s1")
    with pytest.raises(MissionError, match="first by seq"):
        mm2.claim(m.mission_id, "a2", "s1")


def test_claim_round_timeout(tmp_path):
    now = [1000.0]
    bus = _bus(tmp_path)
    mm = MissionManager(bus, claim_timeout_s=60.0, clock=lambda: now[0])
    m = _open(mm)
    mm.decompose(m.mission_id, _planner(["alpha"]))
    now[0] += 61.0
    with pytest.raises(MissionError, match="timed out"):
        mm.claim(m.mission_id, "a1", "s1")


def test_lease_expiry_returns_subtask_to_pool(tmp_path):
    now = [2000.0]
    bus = _bus(tmp_path)
    mm = MissionManager(bus, lease_seconds=30.0, clock=lambda: now[0])
    m = _claimed(mm)
    mm.claim(m.mission_id, "a1", "s1")
    now[0] += 31.0
    assert mm.expire_leases(m.mission_id) == ["s1"]
    sub = mm.get(m.mission_id).subtasks[0]
    assert sub.status == "open" and sub.claimed_by is None
    mm.claim(m.mission_id, "a2", "s1")  # re-claimable
    assert mm.get(m.mission_id).subtasks[0].claimed_by == "a2"


def test_heartbeat_renews_lease(tmp_path):
    now = [3000.0]
    bus = _bus(tmp_path)
    mm = MissionManager(bus, lease_seconds=30.0, clock=lambda: now[0])
    m = _claimed(mm)
    first = mm.claim(m.mission_id, "a1", "s1").lease_until
    now[0] += 20.0
    renewed = mm.heartbeat(m.mission_id, "a1")
    assert renewed > first
    with pytest.raises(MissionError):
        mm.heartbeat(m.mission_id, "nobody")


def test_claim_sweep_moves_to_working(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _claimed(mm)
    mm.claim(m.mission_id, "a1", "s1")
    left_open = mm.claim_timeout_sweep(m.mission_id)
    assert left_open == ["s2"]
    assert mm.get(m.mission_id).state == WORKING
    with pytest.raises(MissionError):
        mm.claim(m.mission_id, "a2", "s2")  # round is closed



# ---------- work / verify / review / replan ----------


def _working(mm, texts=("alpha", "beta")):
    m = _claimed(mm, texts=texts)
    mm.claim(m.mission_id, "a1", "s1")
    mm.claim_timeout_sweep(m.mission_id)
    return m


def test_submit_verify_review_happy_path(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _working(mm, texts=("alpha",))
    mm.submit_result(m.mission_id, "a1", "s1", "the answer")
    assert mm.get(m.mission_id).state == REVIEWING
    ok, _ = mm.verify(m.mission_id, "s1", lambda sub, res: (True, "schema ok"))
    assert ok is True
    mm.record_review(m.mission_id, "s1", ok=True, reviewer="rev")
    syn = mm.synthesize(m.mission_id)
    assert syn["partial"] is False
    assert syn["results"] == {"s1": "the answer"}
    assert mm.get(m.mission_id).state == DONE


def test_submit_by_non_holder_rejected(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _working(mm, texts=("alpha",))
    with pytest.raises(MissionError):
        mm.submit_result(m.mission_id, "intruder", "s1", "x")


def test_verify_failure_counts_stall(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _working(mm, texts=("alpha",))
    mm.submit_result(m.mission_id, "a1", "s1", "junk")
    ok, _ = mm.verify(m.mission_id, "s1", lambda sub, res: (False, "missing file"))
    assert ok is False
    assert mm.get(m.mission_id).stalls == 1
    assert mm.get(m.mission_id).ledger()["done"] == ["s1"]


def test_review_retries_then_honest_fail_path(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _working(mm, texts=("alpha",))
    mm.submit_result(m.mission_id, "a1", "s1", "v1")
    for _ in range(MAX_REVIEW_RETRIES):
        mm.record_review(m.mission_id, "s1", ok=False, critique="redo")
        assert mm.get(m.mission_id).state == WORKING
    mm.record_review(m.mission_id, "s1", ok=False, critique="still bad")
    assert mm.get(m.mission_id).subtasks[0].status == "failed"


def test_stall_replan_and_replan_budget_exhaustion(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _working(mm, texts=("alpha",))
    mm.submit_result(m.mission_id, "a1", "s1", "junk")
    for _ in range(STALL_LIMIT):
        mm.verify(m.mission_id, "s1", lambda sub, res: (False, "bad"))
    assert mm.get(m.mission_id).stalls == STALL_LIMIT
    fresh = mm.replan(m.mission_id, _planner(["gamma"]))
    assert [s.id for s in fresh] == ["s2"]
    assert mm.get(m.mission_id).replans == 1
    assert mm.get(m.mission_id).stalls == 0
    mm2_replans = mm.get(m.mission_id).replans
    mm.get(m.mission_id).replans = MAX_REPLANS
    with pytest.raises(MissionError, match="replan budget exhausted"):
        mm.replan(m.mission_id, _planner(["delta"]))
    assert mm.get(m.mission_id).state == FAILED
    assert mm2_replans == 1



# ---------- budgets ----------


def test_budget_warn_then_hard_stop_with_partial(tmp_path):
    bus = _bus(tmp_path)
    mm = MissionManager(bus)
    m = _open(mm, budget_tokens=1000, hitl_gates=[])
    mm.decompose(m.mission_id, _planner(["alpha", "beta"]), est_tokens=100)  # used 100
    mm.claim(m.mission_id, "a1", "s1", est_tokens=50)  # a1=50, used 150
    assert mm.get(m.mission_id).warned is False
    mm.claim_timeout_sweep(m.mission_id)
    mm.submit_result(m.mission_id, "a1", "s1", "r1", est_tokens=350)  # a1=400 (==cap), used 500
    assert mm.get(m.mission_id).warned is False
    mm._spend(mm.get(m.mission_id), "a2", 200)  # used 700 → warning
    assert mm.get(m.mission_id).warned is True
    kinds = [e.kind for e in bus.tail()]
    assert "budget_warning" in kinds
    # a3 pushes past 100% → hard stop: mission FAILS with partial synthesis.
    mm._spend(mm.get(m.mission_id), "a3", 300)
    assert mm.get(m.mission_id).state == FAILED
    assert mm.get(m.mission_id).terminate_reason == "budget"
    syn = mm.get(m.mission_id).synthesis
    assert syn["partial"] is True and syn["missing"] == ["s2"]


def test_per_agent_quota_blocks_hog(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _open(mm, budget_tokens=1000)
    mm.decompose(m.mission_id, _planner(["alpha"]), est_tokens=50)
    with pytest.raises(MissionError, match="share"):
        mm.claim(m.mission_id, "hog", "s1", est_tokens=500)  # 40% of 1000 = 400


def test_budget_hard_stop_delivers_partial(tmp_path):
    bus = _bus(tmp_path)
    mm = MissionManager(bus)
    m = _open(mm, budget_tokens=400, hitl_gates=[])
    mm.decompose(m.mission_id, _planner(["alpha", "beta"]), est_tokens=100)
    mm.claim(m.mission_id, "a1", "s1", est_tokens=50)
    mm.claim_timeout_sweep(m.mission_id)
    mm.submit_result(m.mission_id, "a1", "s1", "r1", est_tokens=100)  # used 250
    mm.record_review(m.mission_id, "s1", ok=True)
    # push over 400 with a second agent (per-agent cap is 160; spread the spend)
    mm._spend(mm.get(m.mission_id), "a2", 150)  # used 400 → hard stop
    assert mm.get(m.mission_id).state == FAILED
    assert mm.get(m.mission_id).terminate_reason == "budget"
    syn = mm.get(m.mission_id).synthesis
    assert syn["partial"] is True
    assert syn["missing"] == ["s2"]
    assert syn["results"] == {"s1": "r1"}


# ---------- conflicts ----------


def _done_pair(mm, r1, r2, policy=POLICY_FIRST_RESULT):
    m = _open(mm, budget_tokens=10_000, resolution_policy=policy)
    mm.decompose(m.mission_id, _planner(["one", "two"]))
    mm.claim(m.mission_id, "a1", "s1")
    mm.claim(m.mission_id, "a2", "s2")
    mm.claim_timeout_sweep(m.mission_id)
    mm.submit_result(m.mission_id, "a1", "s1", r1)
    mm.submit_result(m.mission_id, "a2", "s2", r2)
    mm.record_review(m.mission_id, "s1", ok=True)
    mm.record_review(m.mission_id, "s2", ok=True)
    return m


def test_synthesize_no_conflict(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _done_pair(mm, "cats are nice", "dogs are nice")
    syn = mm.synthesize(m.mission_id)
    assert syn["conflicts"] == []
    assert mm.get(m.mission_id).state == DONE


def test_synthesize_surfaces_conflict_first_result(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _done_pair(mm, "price is $10 CONTRADICTS:s2", "price is $20")
    syn = mm.synthesize(m.mission_id)
    assert len(syn["conflicts"]) == 1
    assert syn["conflicts"][0]["claim"] == "s1 vs s2"
    assert set(syn["conflicts"][0]["agents"]) == {"a1", "a2"}
    assert "[s1] price is $10" in syn["answer"]  # first_result wins


def test_synthesize_highest_reliability(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _done_pair(mm, "A says X CONTRADICTS:s2", "B says Y", policy=POLICY_HIGHEST_RELIABILITY)
    syn = mm.synthesize(m.mission_id, reliability={"a1": 0.2, "a2": 0.9})
    assert syn["answer"] == "B says Y"


def test_synthesize_judge_model(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _done_pair(mm, "X CONTRADICTS:s2", "Y", policy=POLICY_JUDGE_MODEL)
    syn = mm.synthesize(m.mission_id, judge=lambda goal, cands: "judged!")
    assert syn["answer"] == "judged!"
    with pytest.raises(MissionError):
        m2 = _open(mm, budget_tokens=10_000, resolution_policy=POLICY_JUDGE_MODEL)
        mm.decompose(m2.mission_id, _planner(["one"]))
        mm.claim(m2.mission_id, "a1", "s1")
        mm.claim_timeout_sweep(m2.mission_id)
        mm.submit_result(m2.mission_id, "a1", "s1", "r")
        mm.record_review(m2.mission_id, "s1", ok=True)
        mm.synthesize(m2.mission_id)  # no judge callable → error


def test_synthesize_ask_user_suspends(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _done_pair(mm, "X CONTRADICTS:s2", "Y", policy=POLICY_ASK_USER)
    with pytest.raises(MissionError, match="on_conflict"):
        mm.synthesize(m.mission_id)
    assert mm.get(m.mission_id).state == SUSPENDED
    assert any(g["gate"] == "on_conflict" for g in mm.get(m.mission_id).gates_pending)


# ---------- terminate / rebuild ----------


def test_terminate_operator_always_delivers(tmp_path):
    mm = MissionManager(_bus(tmp_path))
    m = _working(mm, texts=("alpha", "beta"))
    out = mm.terminate(m.mission_id, reason="operator")
    assert out["partial"] is True
    assert out["missing"] == ["s1", "s2"]
    assert mm.get(m.mission_id).state == FAILED


def test_rebuild_from_bus_after_coordinator_death(tmp_path):
    """The truth path: fresh manager + same log → mission resumable."""
    bus = _bus(tmp_path)
    mm = MissionManager(bus)
    m = _open(mm, budget_tokens=10_000)
    mm.decompose(m.mission_id, _planner(["alpha", "beta"]))
    mm.claim(m.mission_id, "a1", "s1")
    mm.claim_timeout_sweep(m.mission_id)
    mm.submit_result(m.mission_id, "a1", "s1", "r1")
    mm.record_review(m.mission_id, "s1", ok=True)

    mm2 = MissionManager(bus)  # coordinator "died"; a new one replays the log
    assert mm2.rebuild_from_bus() == 1
    r = mm2.get(m.mission_id)
    assert r.goal == "build the thing"
    assert r.ledger()["done"] == ["s1"]
    assert r.state == REVIEWING
    assert r.blackboard[0]["key"] == "s1"
    # ...and it can keep coordinating: reopen the claim round for s2.
    r.state = CLAIMING
    r.claim_deadline = time.time() + 60
    claimed = mm2.claim(m.mission_id, "a2", "s2")
    assert claimed.claimed_by == "a2"


def test_snapshot_restore_round_trip(tmp_path):
    bus = _bus(tmp_path)
    mm = MissionManager(bus)
    m = _open(mm, budget_tokens=5000)
    mm.decompose(m.mission_id, _planner(["alpha"]))
    payload = mm.snapshot_state()
    mm2 = MissionManager(FleetBus(base_dir=str(tmp_path / "other"), fsync=False, snapshot_every_events=0, snapshot_interval_s=0))
    assert mm2.restore_state(payload) == 1
    r = mm2.get(m.mission_id)
    assert r.goal == "build the thing" and [s.id for s in r.subtasks] == ["s1"]
