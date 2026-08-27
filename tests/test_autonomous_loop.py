"""Unit tests for the Unified Mission Autonomous Loop, Behavioral Verification, and Git Transactions."""
from __future__ import annotations

import os
import time


from core.artifact_manager import ArtifactManager
from core.critic import CriticVerdict, critic_manager
from core.mission import MissionEngine, MissionState
from core.modes import AgentMode, get_mode_config
from core.rollback import GitTxState, RollbackManager
from core.verifier_registry import PythonVerifier


def test_mission_engine_autonomous_repair_replan_loop(tmp_path):
    """Verify that Mission Engine runs the full autonomous diagnose-repair-replan cycle within the loop."""
    storage = tmp_path / "missions"
    storage.mkdir()

    attempts = {"count": 0}

    def failing_then_succeeding_executor(node, parent_ctx):
        attempts["count"] += 1
        # First round fails
        if attempts["count"] <= 2:
            return {
                "success": False,
                "output": "SyntaxError: invalid syntax at line 4",
                "error": "SyntaxError in script",
            }
        # Subsequent rounds succeed
        return {
            "success": True,
            "output": "Script executed cleanly with return code 0. 5 tests passed in 0.02s.",
            "evidence": [{"check": "test_suite", "status": "passed"}],
        }

    engine = MissionEngine(executor=failing_then_succeeding_executor, storage_dir=storage)
    report = engine.start_mission(
        goal="Develop verified parser module",
        requirements=["Valid syntax", "Pass unit tests"],
        domain="generic",
        budget_steps=15,
    )

    # Must complete after autonomous repair rounds
    assert report.state == MissionState.COMPLETED.value
    assert report.budget.repairs_used >= 1
    assert len(report.repair_history) >= 1
    assert report.progress_pct == 100
    assert report.confidence_score >= 0.8


def test_structural_vs_behavioral_verification(tmp_path):
    """Verify that structural pass alone is not enough if behavioral tests fail."""
    py_file = tmp_path / "calc.py"
    py_file.write_text("""def add(a, b):
    return a + b
""")

    pv = PythonVerifier()

    # Case 1: Structurally valid, but behavioral runtime output contains unhandled exception
    res_failed_behavior = pv.verify({
        "task": "Test calc",
        "workspace_dir": str(tmp_path),
        "files_modified": [str(py_file)],
        "output": "Traceback (most recent call last):\n  File 'test.py', line 2\nAssertionError: add(1, 1) != 3",
    })

    assert res_failed_behavior.structural_verified is True
    assert res_failed_behavior.behavioral_verified is False
    assert res_failed_behavior.verified is False

    # Case 2: Both structural and behavioral pass
    res_clean = pv.verify({
        "task": "Test calc",
        "workspace_dir": str(tmp_path),
        "files_modified": [str(py_file)],
        "output": "1 passed in 0.01s",
    })
    assert res_clean.structural_verified is True
    assert res_clean.behavioral_verified is True
    assert res_clean.verified is True


def test_outcome_verifier_rejects_unproven_claims():
    """Verify that requirement text appearing in log without executable proof is REJECTED."""
    unproven_log = "User asked to create a weather app. We should create a weather app."
    report = critic_manager.verify_outcome(
        task="Create working weather app",
        execution_log=unproven_log,
        artifacts=[],
        requirements=["Create working weather app"],
        verification_evidence=[],
    )

    assert report.verdict == CriticVerdict.REJECTED.value
    assert report.score == 0
    assert any("lacking executable proof" in f.description for f in report.findings)


def test_mission_aware_artifact_scoping(tmp_path):
    """Ensure artifacts are scoped by creation/modification timestamp to avoid attributing stale files."""
    mgr = ArtifactManager(storage_dir=tmp_path / "artifacts", workspace_root=tmp_path)

    old_file = tmp_path / "old_build.zip"
    old_file.write_bytes(b"PK\x03\x04old")
    # Set old timestamp
    os.utime(old_file, (time.time() - 1000, time.time() - 1000))

    mission_start_time = time.time() - 10
    time.sleep(0.01)

    new_file = tmp_path / "new_build.zip"
    new_file.write_bytes(b"PK\x03\x04new")

    scanned = mgr.scan_workspace(
        target_dir=tmp_path,
        mission_id="msn_fresh",
        since_timestamp=mission_start_time,
    )

    names = [a.name for a in scanned]
    assert "new_build.zip" in names
    assert "old_build.zip" not in names


def test_git_transaction_state_transitions(tmp_path):
    """Verify Git transaction state machine: CREATED -> ACTIVE -> COMMITTING -> MERGING -> COMMITTED."""
    # Initialize a dummy git repo in tmp_path
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), check=True)
    (tmp_path / "README.md").write_text("# Initial Repo")
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(tmp_path), check=True)

    rm = RollbackManager(storage_dir=tmp_path / "checkpoints", workspace_dir=tmp_path)

    # 1. Start transaction
    res = rm.start_git_transaction(repo_dir=tmp_path, transaction_name="feature_x")
    assert res["success"] is True
    assert rm._active_git_tx["state"] == GitTxState.ACTIVE.value

    # Make changes in transaction
    (tmp_path / "feature.py").write_text("print('feature')")

    # 2. Commit transaction
    res_commit = rm.commit_git_transaction(message="Implement feature X")
    assert res_commit["success"] is True
    assert rm._active_git_tx is None
    assert (tmp_path / "feature.py").exists()


def test_swe_agent_mode_configuration():
    """Verify SWE mode configuration is registered as a first-class mode."""
    cfg = get_mode_config(AgentMode.SWE)
    assert cfg.name == "Software Engineer Mode"
    assert "INSPECT" in cfg.system_prompt_addition
    assert "VERIFY" in cfg.system_prompt_addition
    assert cfg.max_tool_calls_per_turn >= 10
