"""Unit tests for the Mission Platform, Rollback Manager, Artifact Manager, and Agent DAG."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from core.agent_dag import AgentDAG, DAGNodeStatus, create_standard_mission_dag
from core.artifact_manager import ArtifactManager
from core.critic import CriticVerdict, Severity, critic_manager
from core.mission import MissionEngine, MissionState
from core.permissions import Capability, Decision, PolicyGate
from core.rollback import RollbackManager
from core.router2 import ModelRouter


def test_rollback_manager_snapshot_and_restore(tmp_path):
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    storage_dir = tmp_path / "checkpoints"
    storage_dir.mkdir()

    # Create initial files
    (ws_dir / "file1.txt").write_text("initial content 1")
    (ws_dir / "file2.py").write_text("print('hello world')")

    rm = RollbackManager(storage_dir=storage_dir, workspace_dir=ws_dir)
    cp = rm.checkpoint(label="before_edit")
    assert cp.id.startswith("chk_")
    assert "file1.txt" in cp.files
    assert "file2.py" in cp.files

    # Modify file1, delete file2, add file3
    (ws_dir / "file1.txt").write_text("modified content 1")
    (ws_dir / "file2.py").unlink()
    (ws_dir / "file3.json").write_text('{"key": "val"}')

    diff_info = rm.diff(cp.id)
    assert diff_info["has_changes"] is True
    assert "file3.json" in diff_info["added"]
    assert "file2.py" in diff_info["deleted"]
    assert "file1.txt" in diff_info["modified"]

    # Restore
    res_restore = rm.restore(cp.id)
    assert res_restore["success"] is True
    assert (ws_dir / "file1.txt").read_text() == "initial content 1"
    assert (ws_dir / "file2.py").exists()
    assert not (ws_dir / "file3.json").exists()


def test_artifact_manager_bundle_export(tmp_path):
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    art_dir = tmp_path / "artifacts"
    art_dir.mkdir()

    # Create fake build artifacts
    apk = ws_dir / "app-debug.apk"
    apk.write_bytes(b"PK\x03\x04fake apk content")
    report = ws_dir / "test_report.html"
    report.write_text("<h1>Test Report</h1>")

    mgr = ArtifactManager(storage_dir=art_dir, workspace_root=ws_dir)
    art1 = mgr.register_artifact(apk, mission_id="msn_001")
    art2 = mgr.register_artifact(report, mission_id="msn_001")

    assert art1.artifact_type == "apk"
    assert art2.artifact_type == "report"

    listed = mgr.list_artifacts(mission_id="msn_001")
    assert len(listed) == 2

    # Export bundle
    bundle_zip = tmp_path / "export" / "mission_bundle.zip"
    out_path = mgr.export_bundle(bundle_zip, mission_id="msn_001")
    assert Path(out_path).exists()
    assert Path(out_path).stat().st_size > 0


def test_agent_dag_topological_execution():
    dag = AgentDAG(name="SWE Pipeline")
    dag.add_node("spec", "architect", "Define spec")
    dag.add_node("code", "coder", "Write implementation", dependencies=["spec"])
    dag.add_node("review", "reviewer", "Review code", dependencies=["code"])
    dag.add_node("test", "tester", "Run test suite", dependencies=["code"])
    dag.add_node("verify", "verifier", "Final verification", dependencies=["review", "test"])

    # Validate topology
    sorted_nodes = dag.topological_sort()
    assert len(sorted_nodes) == 5
    ids = [n.id for n in sorted_nodes]
    assert ids.index("spec") < ids.index("code")
    assert ids.index("code") < ids.index("review")
    assert ids.index("code") < ids.index("test")
    assert ids.index("review") < ids.index("verify")
    assert ids.index("test") < ids.index("verify")

    # Execution simulation
    executed_order = []

    def mock_node_executor(node, parent_ctx):
        executed_order.append(node.id)
        return {"success": True, "outputs": {"result": f"done: {node.id}"}}

    res = dag.execute_dag(mock_node_executor)
    assert res["success"] is True
    assert res["completed"] == 5
    assert len(executed_order) == 5


def test_agent_dag_cycle_detection():
    dag = AgentDAG(name="Cyclic Graph")
    dag.add_node("a", "worker", "Goal A", dependencies=["c"])
    dag.add_node("b", "worker", "Goal B", dependencies=["a"])
    dag.add_node("c", "worker", "Goal C", dependencies=["b"])

    assert dag.validate() is False
    with pytest.raises(ValueError, match="Cycle detected"):
        dag.topological_sort()


def test_mission_engine_success_lifecycle(tmp_path):
    storage = tmp_path / "missions"
    storage.mkdir()

    def mock_executor(goal, ctx):
        return {
            "success": True,
            "output": f"Output for: {goal}",
            "evidence": [{"step": goal, "status": "ok"}],
        }

    engine = MissionEngine(executor=mock_executor, storage_dir=storage)
    report = engine.start_mission(
        goal="Build Python microservice",
        requirements=["Create service", "Pass unit tests"],
        domain="generic",
        subgoals=["Setup structure", "Implement routes", "Run test suite"],
        budget_steps=10,
    )

    assert report.state == MissionState.COMPLETED.value
    assert report.progress_pct == 100
    assert report.confidence_score >= 0.8
    assert len(report.subgoals) == 3
    assert all(sg.status == DAGNodeStatus.COMPLETED.value for sg in report.subgoals)
    assert len(report.evidence) >= 3


def test_mission_engine_blocked_state_and_resume(tmp_path):
    storage = tmp_path / "missions"
    storage.mkdir()

    call_count = {"val": 0}

    def mock_blocking_executor(goal, ctx):
        call_count["val"] += 1
        if call_count["val"] == 1:
            return {
                "success": False,
                "blocked": True,
                "blocker_reason": "Missing API secret token",
                "blocker_instructions": "Provide HERMUS_API_KEY",
            }
        return {"success": True, "output": f"Resumed output for: {goal}"}

    engine = MissionEngine(executor=mock_blocking_executor, storage_dir=storage)
    report = engine.start_mission(
        goal="Deploy to cloud",
        subgoals=["Authenticate", "Deploy"],
        budget_steps=5,
    )

    # First run should enter BLOCKED state
    assert report.state == MissionState.BLOCKED.value
    assert report.blocker_reason == "Missing API secret token"

    # Resume mission
    resumed_report = engine.resume_mission(report.mission_id)
    assert resumed_report.state == MissionState.COMPLETED.value
    assert resumed_report.blocker_reason is None


def test_independent_critic_panel():
    files = {
        "calculator.py": """def add(a, b):
    # TODO: implement
    pass
""",
        "auth.py": """api_key = 'sk-1234567890abcdef123456'
eval('2 + 2')
""",
    }

    report = critic_manager.run_full_review(
        task="Implement calculator and auth",
        files_content=files,
        execution_log="",
    )

    assert report["approved"] is False
    assert report["verdict"] in (CriticVerdict.REJECTED.value, CriticVerdict.CHANGES_REQUESTED.value)
    assert len(report["repair_directives"]) > 0
    assert any("api_key" in d.lower() or "eval" in d.lower() or "placeholder" in d.lower() for d in report["repair_directives"])


def test_policy_gate_unified_enforcement(tmp_path):
    gate = PolicyGate()
    # Read is allowed by default
    res_read = gate.enforce("read_file", args={"path": "test.txt"})
    assert res_read["allowed"] is True

    # Credential access is denied
    res_cred = gate.enforce("credential_access")
    assert res_cred["allowed"] is False
    assert res_cred["decision"] == Decision.DENY.value

    # Strict mode raises PermissionError on Deny
    with pytest.raises(PermissionError):
        gate.enforce("credential_access", strict=True)
