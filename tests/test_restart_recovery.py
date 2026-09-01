"""Restart / resume recovery tests (Spec §13).

These exercise the real durability contract: start a mission, persist it, kill the
"worker" (discard the engine, i.e. a fresh process), load it from disk on a new
engine, continue, and verify. They also check that duplicate execution is
prevented and that cancel state survives. No mocks of the persistence layer.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_mission_restart_continue_after_engine_recreated(tmp_path):
    """Kill the worker (drop the engine), reload from disk, resume, complete.

    The executor is stateful: the first invocation (before restart) produces a
    blocking result; after a fresh engine reloads and resumes, the same mission
    continues and completes. This proves state survives process death and that
    a genuine return path exists (status survives, not fabricated).
    """
    from core.mission import MissionEngine, MissionState

    store = tmp_path / "missions"

    # An executor that only succeeds once the mission has been "restarted".
    phase = {"restarted": False}

    def executor(node, ctx):
        if not phase["restarted"]:
            return {"success": False, "blocked": True, "blocker_reason": "simulated worker kill"}
        return {"success": True, "output": f"node={getattr(node, 'role', '?')} done", "evidence": [{"check": "ok", "status": "passed"}]}

    # --- worker #1: start + persist, then "die" (engine dropped) -------------
    eng1 = MissionEngine(executor=executor, storage_dir=store)
    r1 = eng1.start_mission("restart me", budget_steps=6, max_repairs=1)
    persisted = store / f"{r1.mission_id}.json"
    assert persisted.exists(), "mission must be persisted durably"
    assert r1.state == MissionState.BLOCKED.value, r1.state

    # --- kill the worker: no reference to eng1 is used below -----------------
    phase["restarted"] = True

    # --- worker #2: fresh engine re-binds its executor, loads + resumes -------
    # (In production the restarted worker re-registers its executor function;
    # the durability under test is that state is read from disk, not from RAM.)
    eng2 = MissionEngine(executor=executor, storage_dir=store)
    loaded = eng2.load_mission(r1.mission_id)
    assert loaded.mission_id == r1.mission_id
    assert loaded.state == MissionState.BLOCKED.value
    assert loaded.to_dict()["mission_id"] == r1.mission_id

    resumed = eng2.resume_mission(r1.mission_id)
    assert resumed.state == MissionState.COMPLETED.value, resumed.state
    # The resumed run actually did work (not a fake completion).
    assert resumed.progress_pct == 100
    assert resumed.finished_at

    # Survives on disk for the next process to read.
    eng3 = MissionEngine(storage_dir=store)
    final = eng3.load_mission(r1.mission_id)
    assert final.state == MissionState.COMPLETED.value


def test_restart_duplicate_execution_prevented(tmp_path):
    """A completed mission must NOT be re-runnable ('duplicate execution')."""
    from core.mission import MissionEngine, MissionState

    store = tmp_path / "missions"
    eng = MissionEngine(executor=lambda node, ctx: {"success": True, "output": "ok"},
                        storage_dir=store)
    r = eng.start_mission("idempotent", budget_steps=3)
    if r.state == MissionState.COMPLETED.value:
        with pytest.raises(ValueError, match="completed"):
            eng.resume_mission(r.mission_id)
    # Loading is fine (read-only), re-execution is refused by the API.
    assert eng.load_mission(r.mission_id).state == MissionState.COMPLETED.value


def test_restart_cancel_state_survives(tmp_path):
    """A cancelled mission is a terminal state that persists and never auto-resumes."""
    import json
    from core.mission import MissionEngine, MissionState

    store = tmp_path / "missions"
    rounds = {"n": 0}

    def executor(node, ctx):
        rounds["n"] += 1
        return {"success": True, "output": "work"}

    # Cooperative cancellation stops the loop and persists the CANCELLED state.
    eng1 = MissionEngine(executor=executor, storage_dir=store)
    r1 = eng1.start_mission("cancel me", budget_steps=40, max_repairs=5,
                            should_cancel=lambda: rounds["n"] >= 2)
    assert r1.state == MissionState.CANCELLED.value
    persisted = store / f"{r1.mission_id}.json"
    assert json.loads(persisted.read_text())["state"] == "cancelled"

    # A fresh engine reads the cancelled state and refuses to continue it.
    eng2 = MissionEngine(storage_dir=store)
    loaded = eng2.load_mission(r1.mission_id)
    assert loaded.state == MissionState.CANCELLED.value
    with pytest.raises(ValueError, match="cancelled"):
        eng2.resume_mission(r1.mission_id)
