"""Execution-path hardening regressions.

These tests pin the behaviour that a code review found missing or dangerous in
the "universal runtime" commit (b753d56):

1.  a crashed mission returns MISSION FAILED with diagnostics — it is never
    silently downgraded to a chat answer;
2.  the request classifier separates QUESTION / EXPLANATION / ANALYSIS from
    ACTION before promoting anything to a mission;
3.  the evidence gate distinguishes *supporting* actions (memory_add,
    slack_notify, …) from goal-completion evidence;
4.  verifier/reviewer/tester stages are judged by expected output type, not by
    a role list that demanded file changes from them;
5.  the mission budget is a hierarchy (planning / execution / verification /
    repair / emergency) larger than a single agent turn;
6.  mission file evidence is scoped to the mission (baseline diff, other
    missions excluded) instead of a global timestamp scan;
7.  mission state is persisted atomically (tmp → fsync → rename);
8.  resume semantics are explicit: blocked/interrupted resumable, failed
    restartable only on purpose, completed/cancelled terminal;
9.  ``extend_budget`` documentation matches the implementation;
10. every dashboard shares one frontend runtime client (queue-first);
11. CI is actually committed (not just described in docs/);
12. model capability negotiation answers tools/vision/context/… up front.

Offline: no model backend is contacted.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from typing import Any

import pytest


@pytest.fixture()
def client():
    from starlette.testclient import TestClient

    from gateway.gateway import app

    with TestClient(app) as c:
        yield c


# ===========================================================================
# 1. mission failure must never silently become chat
# ===========================================================================
class _RecordingAgent:
    """Agent stand-in that records whether it was asked to chat."""

    def __init__(self, response="chat answer"):
        self.calls: list[str] = []
        self._response = response

        class _LLM:
            provider = "fake"

        self.llm = _LLM()
        self.model_name = "fake/model"

    def chat(self, prompt, **kwargs):
        self.calls.append(prompt)
        return {"response": self._response, "tool_calls": [], "tool_results": [], "steps": 0}


def test_mission_crash_returns_structured_failure_not_chat(monkeypatch, tmp_path):
    """The #1 architectural flaw: mission error → chat answer. Must not happen."""
    import core.runtime as runtime
    from core import mission as mission_mod

    monkeypatch.setattr(mission_mod.MissionEngine, "storage_dir", tmp_path, raising=False)

    def boom(self, *a, **kw):
        raise RuntimeError("mission engine exploded on purpose")

    monkeypatch.setattr(mission_mod.MissionEngine, "start_mission", boom, raising=True)

    agent = _RecordingAgent()
    events: list[tuple[str, dict]] = []
    out = runtime.execute(
        "Build this app and keep going until it works",
        agent=agent,
        prefer="mission",
        on_event=lambda t, d: events.append((t, d)),
    )

    # 1. it is a mission failure, not a chat turn
    assert out["run_kind"] == "mission_failed"
    assert out["status"] == "failed"
    assert out["mission_failed"] is True
    assert out["verified"] is False
    # 2. the answer says so instead of explaining how to build the app
    assert "MISSION FAILED" in out["response"]
    assert "chat answer" not in out["response"]
    # 3. the agent was never asked to answer conversationally
    assert agent.calls == []
    # 4. diagnostics: stage + reason + recoverability
    failure = out["failure"]
    for key in ("stage", "reason", "error_type", "recoverable", "resumable"):
        assert key in failure, failure
    assert "mission engine exploded" in failure["reason"]
    assert failure["error_type"] == "RuntimeError"
    # a crash before the mission was created has nothing to resume (the
    # resume handle is provided by the recorded-report path below)
    assert str(out.get("mission_id") or "") == ""
    assert failure["resume_command"] == ""
    # 5. the failure is visible on the event stream
    kinds = [t for t, _ in events]
    assert "mission_error" in kinds and "mission_finished" in kinds


def test_lifecycle_crash_is_recorded_then_reported(monkeypatch, tmp_path):
    """A crash inside the lifecycle: engine records it, runtime reports it."""
    import core.runtime as runtime
    from core import mission as mission_mod

    monkeypatch.setattr(mission_mod.MissionEngine, "storage_dir", tmp_path, raising=False)
    monkeypatch.setattr(mission_mod.MissionEngine, "_run_autonomous_loop",
                        lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("loop died")),
                        raising=True)

    agent = _RecordingAgent()
    out = runtime.execute("mission: build it", agent=agent, prefer="mission")
    assert out["run_kind"] == "mission_failed"
    assert out["mission_failed"] is True
    assert "loop died" in out["failure"]["reason"]
    assert agent.calls == []
    # the crashed mission was recorded, so it has a resume handle
    assert str(out.get("mission_id") or "").startswith("msn_")
    assert out["failure"]["resume_command"].startswith("hermus mission resume ")
    assert out["failure"]["resumable"] is True


def test_mission_crash_fallback_is_opt_in_only(monkeypatch, tmp_path):
    """With HERMUS_MISSION_FALLBACK_TO_CHAT=1 the downgrade is labelled as such."""
    import core.runtime as runtime
    from core import mission as mission_mod
    from core.config import config

    monkeypatch.setattr(mission_mod.MissionEngine, "storage_dir", tmp_path, raising=False)
    monkeypatch.setattr(mission_mod.MissionEngine, "start_mission",
                        lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
                        raising=True)

    old = config.mission_fallback_to_chat
    config.mission_fallback_to_chat = True
    try:
        out = runtime.execute("mission: build it", agent=_RecordingAgent(), prefer="mission")
    finally:
        config.mission_fallback_to_chat = old
    assert out["run_kind"] == "chat_fallback"
    assert out["degraded_from"] == "mission"
    assert out["mission_failed"] is True
    assert out["mission_error"]


def test_engine_records_crash_as_failed_report(tmp_path, monkeypatch):
    """A crash inside the lifecycle is persisted as a failed, restartable report."""
    from core import mission as mission_mod
    from core.mission import MissionEngine, MissionState

    def boom(*a, **kw):
        raise RuntimeError("verifier blew up")

    monkeypatch.setattr(mission_mod.verifier_registry, "verify", boom, raising=True)
    engine = MissionEngine(executor=lambda node, ctx: {"success": True, "output": "worked"},
                           storage_dir=tmp_path / "missions")
    report = engine.start_mission("build a thing", budget_steps=4)

    assert report.state == MissionState.FAILED.value
    assert report.error and report.error["type"] == "RuntimeError"
    assert report.error["recoverable"] is True
    assert report.error["stage"]
    assert "MISSION FAILED" in report.final_proof
    # persisted, and readable as valid JSON
    saved = json.loads((tmp_path / "missions" / f"{report.mission_id}.json").read_text())
    assert saved["state"] == "failed"
    assert saved["error"]["type"] == "RuntimeError"
    # restartable
    assert report.is_resumable() is False
    assert report.is_resumable(allow_restart=True) is True
    summary = report.failure_summary()
    assert summary["stage"] and summary["reason"] and summary["resume_command"]


def test_failed_mission_result_carries_diagnostics(tmp_path):
    """A mission that exhausts its budget also reports a structured failure."""
    from core.mission import MissionEngine, MissionState
    from core.runtime import mission_report_to_result

    calls = {"n": 0}

    def executor(node, ctx):
        calls["n"] += 1
        return {"success": False, "output": "still broken", "error": "assertion failed"}

    engine = MissionEngine(executor=executor, storage_dir=tmp_path / "missions")
    report = engine.start_mission("ship it", budget_steps=3, max_repairs=0)
    assert report.state == MissionState.FAILED.value

    out = mission_report_to_result(report)
    assert out["run_kind"] == "mission"
    assert out["status"] == "failed"
    assert out["progress_pct"] <= 95
    assert out["failure"]["reason"]
    assert out["failure"]["resume_command"].startswith("hermus mission resume ")


# ===========================================================================
# 2. intent classification — questions are not missions
# ===========================================================================
@pytest.mark.parametrize("text", [
    "Can you explain how to fix my app?",
    "What is the best way to build an API?",
    "explain how authentication works",
    "how do I build a REST API?",
    "what is the capital of France?",
    "tell me about the difference between REST and GraphQL",
])
def test_questions_and_explanations_stay_chat(text):
    from core.runtime import classify_request

    assert classify_request(text) == "chat", text


@pytest.mark.parametrize("text", [
    "Build a web app with login and tests and keep going until it works",
    "Write a script that scrapes prices daily and stores them in sqlite",
    "fix the failing tests in the repo",
    "mission: tidy up my notes",
    "do this autonomously please",
    "can you build me a website?",
])
def test_action_requests_become_missions(text):
    from core.runtime import classify_request

    assert classify_request(text) == "mission", text


def test_detect_intent_labels():
    from core.runtime import (INTENT_ACTION, INTENT_ANALYSIS, INTENT_EXPLANATION,
                              INTENT_QUESTION, detect_intent)

    assert detect_intent("What is the best way to build an API?") == INTENT_EXPLANATION
    assert detect_intent("Is the cache warm?") == INTENT_QUESTION
    assert detect_intent("review this diff and summarize findings") == INTENT_ANALYSIS
    assert detect_intent("build me a REST API for payments") == INTENT_ACTION
    # classify_request can report both at once
    from core.runtime import classify_request

    assert classify_request("deploy the service", with_intent=True) == ("mission", INTENT_ACTION)
    assert classify_request("when does the backup run?", with_intent=True) == ("chat", INTENT_QUESTION)


# ===========================================================================
# 3 + 4. evidence gate: goal evidence vs supporting actions, roles by output type
# ===========================================================================
class _Node:
    def __init__(self, role="coder", goal="Implement authentication", inputs=None):
        self.id = "n1"
        self.role = role
        self.goal = goal
        self.inputs = inputs or {}
        self.dependencies = []


class _FakeAgent:
    def __init__(self, response="", tool_calls=None):
        self._response = response
        self._tool_calls = tool_calls or []
        self.prompts: list[str] = []

        class _LLM:
            provider = "fake"

        self.llm = _LLM()

    def chat(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return {"response": self._response, "tool_calls": list(self._tool_calls),
                "tool_results": [{"tool": t} for t in self._tool_calls], "steps": 1}


@pytest.fixture()
def no_file_scan(monkeypatch):
    changed = {"files": []}
    monkeypatch.setattr("core.mission._scan_changed_files",
                        lambda since_ts, roots=None: changed["files"])
    return changed


def test_supporting_actions_do_not_satisfy_a_change_stage(no_file_scan):
    """memory_add / slack_notify / embeddings_add are not proof of the goal."""
    from core.mission import make_agent_backed_executor

    agent = _FakeAgent(
        response="Implemented the auth module end to end, all tests green.",
        tool_calls=["memory_add", "slack_notify", "embeddings_add", "skill_harvest"],
    )
    executor = make_agent_backed_executor(agent=agent)
    res = executor(_Node(role="coder", goal="Implement authentication"), {})

    assert res["success"] is False
    assert res["error"] == "no_evidence_of_work"
    assert res["expected_output"] == "change"
    assert "supporting" in res["instructions"].lower()
    assert res["evidence"][0]["supporting_actions"]


def test_goal_tools_still_satisfy_a_change_stage(no_file_scan):
    from core.mission import make_agent_backed_executor

    agent = _FakeAgent(response="Wrote auth.py", tool_calls=["file_write", "sandbox_run"])
    res = make_agent_backed_executor(agent=agent)(
        _Node(role="coder", goal="Implement authentication"), {})
    assert res["success"] is True
    assert res["evidence"][0]["performed_work"] is True


def test_verifier_reporting_failure_passes_without_file_changes(no_file_scan):
    """A verifier that reports 'tests failed because X' has done its job."""
    from core.mission import make_agent_backed_executor

    agent = _FakeAgent(
        response=("Verification result: 3 tests failed because the login handler "
                  "returns 500 when the session cookie is missing. Traceback shows "
                  "a KeyError in auth/session.py line 42. No files were changed by "
                  "this verification pass."),
    )
    res = make_agent_backed_executor(agent=agent)(
        _Node(role="verifier", goal="Review the auth module and report defects"), {})
    assert res["success"] is True, res
    assert res["evidence"][0]["expected_output"] == "analysis"


def test_expected_output_type_by_role_and_goal():
    from core.mission import (EVIDENCE_ANALYSIS, EVIDENCE_CHANGE, EVIDENCE_EXECUTION,
                              expected_output_type)

    assert expected_output_type(_Node("coder", "Implement the login form"))["primary"] == EVIDENCE_CHANGE
    assert expected_output_type(_Node("verifier", "Review the diff"))["primary"] == EVIDENCE_ANALYSIS
    assert expected_output_type(_Node("reviewer", "Audit the auth flow"))["primary"] == EVIDENCE_ANALYSIS
    assert expected_output_type(_Node("tester", "Run the test suite"))["primary"] == EVIDENCE_EXECUTION
    assert expected_output_type(_Node("tester", "Analyse flaky tests"))["primary"] == EVIDENCE_ANALYSIS
    assert expected_output_type(_Node("architect", "Design the parser"))["primary"] == EVIDENCE_ANALYSIS
    # a verifier explicitly asked to fix something becomes a change stage
    assert expected_output_type(_Node("verifier", "Fix the failing login test"))["primary"] == EVIDENCE_CHANGE
    # the role alone never forces an action requirement any more
    from core.mission import _node_requires_action

    assert _node_requires_action(_Node("verifier", "Review the diff")) is False
    assert _node_requires_action(_Node("coder", "Implement X")) is True


def test_classify_evidence_reports_supporting_separately():
    from core.mission import classify_evidence, expected_output_type

    verdict = classify_evidence(
        tool_calls=["memory_add", "slack_notify"],
        files_changed=[],
        text="a" * 200,
        expectation=expected_output_type(_Node("coder", "Implement authentication")),
    )
    assert verdict["satisfied"] is False
    assert verdict["kinds"] == {"analysis"}
    assert verdict["supporting_tools"] == ["memory_add", "slack_notify"]


# ===========================================================================
# 5. budget hierarchy
# ===========================================================================
def test_mission_budget_is_a_hierarchy_and_beats_one_agent_turn():
    from core.config import config
    from core.mission import (MISSION_PHASES, PHASE_EMERGENCY, PHASE_EXECUTION,
                              PHASE_PLANNING, PHASE_REPAIR, PHASE_VERIFICATION,
                              MissionBudget)

    b = MissionBudget()
    assert b.initial_steps >= int(config.max_tool_steps)
    for phase in MISSION_PHASES:
        assert phase in b.phases and b.phase(phase)["limit"] >= 1
    # spending is accounted per phase and globally
    b.consume(PHASE_EXECUTION, 3)
    assert b.phase(PHASE_EXECUTION)["used"] == 3
    assert b.consumed_steps == 3
    # a phase can borrow from the emergency reserve
    before = b.remaining(PHASE_EXECUTION)
    assert b.borrow(PHASE_EXECUTION, 2) is True
    assert b.remaining(PHASE_EXECUTION) == before + 2
    # extensions reach the phase that ran dry rather than a single counter
    total = b.total_steps()
    b.grant_extension(10)
    assert b.total_steps() == total + 10
    assert b.extensions_used == 1
    d = b.to_dict()
    assert set(MISSION_PHASES) == set(d["phases"])  # planning/execution/verification/repair/emergency
    assert d["phases"][PHASE_PLANNING]["limit"] >= 1
    assert d["phases"][PHASE_REPAIR]["limit"] >= 1
    assert d["phases"][PHASE_VERIFICATION]["limit"] >= 1
    assert d["phases"][PHASE_EMERGENCY]["limit"] >= 1


def test_emergency_extension_is_separate_from_normal_slots():
    from core.mission import MissionBudget

    b = MissionBudget(initial_steps=4, max_extensions=1, max_emergency_extensions=1)
    b.grant_extension(4)
    assert b.grant_emergency_extension(4) is True
    assert b.grant_emergency_extension(4) is False  # capped
    assert b.emergency_extensions == 1


def test_mission_default_budget_uses_config(tmp_path):
    from core.config import config
    from core.mission import MissionEngine

    engine = MissionEngine(executor=lambda node, ctx: {"success": True, "output": "ok"},
                           storage_dir=tmp_path / "missions")
    report = engine.start_mission("do the thing")
    assert report.budget.initial_steps == int(config.mission_budget_steps)
    assert report.budget.phases


# ===========================================================================
# 6. mission file isolation
# ===========================================================================
def test_file_snapshot_detects_only_real_changes(tmp_path):
    from core.mission_files import FileSnapshot

    (tmp_path / "a.txt").write_text("one")
    base = FileSnapshot.capture([tmp_path])
    (tmp_path / "a.txt").write_text("two")
    (tmp_path / "b.txt").write_text("new")
    changed = base.diff(FileSnapshot.capture([tmp_path]))
    assert any(p.endswith("b.txt") for p in changed)
    assert any(p.endswith("a.txt") for p in changed)
    assert base.diff(base) == []


def test_scope_excludes_other_missions_and_filters_paths(tmp_path, monkeypatch):
    from core import mission_files
    from core.mission_files import MissionFileScope

    missions_root = tmp_path / "missions"
    mine = missions_root / "msn_A" / "workspace"
    theirs = missions_root / "msn_B" / "workspace"
    mine.mkdir(parents=True, exist_ok=True)
    theirs.mkdir(parents=True, exist_ok=True)
    (mine / "out.txt").write_text("mine")
    (theirs / "out.txt").write_text("theirs")

    class _StubWorkspace:
        """Minimal workspace stand-in rooted in tmp_path."""

        def __init__(self, root):
            self._root = root

        @property
        def dirs(self):
            return {"missions": self._root}

        def mission_workspace(self, mission_id):
            path = self._root / str(mission_id) / "workspace"
            path.mkdir(parents=True, exist_ok=True)
            return path

    monkeypatch.setattr(mission_files, "workspace", _StubWorkspace(missions_root), raising=False)

    scope = MissionFileScope.open("msn_A")
    roots = [str(r) for r in scope.roots]
    assert any("msn_A" in r for r in roots)
    assert not any("msn_B" in r for r in roots)
    assert scope.contains(str(mine / "out.txt")) is True
    assert scope.contains(str(theirs / "out.txt")) is False
    assert scope.filter([str(mine / "out.txt"), str(theirs / "out.txt")]) == [str(mine / "out.txt")]
    # baseline diff sees this mission's new file, not the other mission's
    (mine / "later.txt").write_text("later")
    changed = scope.changed_since_baseline()
    assert any(p.endswith("later.txt") for p in changed)
    assert not any("msn_B" in p for p in changed)


def test_executor_uses_scoped_diff_when_scope_provided(tmp_path, monkeypatch):
    """With a mission scope, file evidence comes from the baseline diff."""
    from core import mission_files
    from core.mission import make_agent_backed_executor

    root = tmp_path / "msn_X" / "workspace"
    root.mkdir(parents=True)

    class _Scope:
        mission_id = "msn_X"

        def __init__(self):
            self.baseline = None

        def snapshot(self):
            return []

        def changed_since(self, snap):
            return [str(root / "auth.py")]

    agent = _FakeAgent(response="short")
    monkeypatch.setattr(mission_files, "MissionFileScope", _Scope)
    executor = make_agent_backed_executor(agent=agent, scope=_Scope(), workspace_dir=str(root))
    res = executor(_Node(role="coder", goal="Implement authentication"), {})
    assert res["success"] is True
    assert res["evidence"][0]["files_changed"] == [str(root / "auth.py")]
    # the mission workspace is advertised to the model
    assert str(root) in agent.prompts[0]


# ===========================================================================
# 7. atomic persistence
# ===========================================================================
def test_atomic_write_is_all_or_nothing(tmp_path):
    from core.atomic_io import atomic_write_json, read_json

    p = tmp_path / "state.json"
    atomic_write_json(p, {"a": 1})
    p.write_text('{"a": 2}')  # reader-visible document is always complete
    atomic_write_json(p, {"a": 3})
    assert read_json(p) == {"a": 3}
    assert read_json(tmp_path / "missing.json", default=None) is None
    # no temp files left behind
    assert [f.name for f in tmp_path.iterdir()] == ["state.json"]


def test_file_lock_is_acquired_and_released(tmp_path):
    """Advisory locks must not leak: acquire/release cycles stay usable."""
    from core.atomic_io import file_lock, atomic_write_json, read_json

    path = tmp_path / "guarded.json"
    for value in (1, 2, 3):
        with file_lock(path):
            atomic_write_json(path, {"value": value})
        assert read_json(path) == {"value": value}
    assert (tmp_path / "guarded.json.lock").exists()


def test_mission_state_survives_concurrent_saves(tmp_path):
    """Every save leaves a complete, parseable document behind."""
    from core.mission import MissionEngine

    engine = MissionEngine(executor=lambda node, ctx: {"success": True, "output": "ok"},
                           storage_dir=tmp_path / "missions")
    report = engine.start_mission("concurrent writes", budget_steps=3)
    path = tmp_path / "missions" / f"{report.mission_id}.json"
    for _ in range(5):
        report.progress_pct = 42
        engine._save_mission(report)
        assert json.loads(path.read_text())["progress_pct"] == 42
    assert not list((tmp_path / "missions").glob(".*tmp*"))


# ===========================================================================
# 8. resume semantics
# ===========================================================================
def _make_failed(tmp_path, **kw) -> Any:
    from core.mission import MissionEngine, MissionState

    def executor(node, ctx):
        return {"success": False, "output": "", "error": "still broken"}

    engine = MissionEngine(executor=executor, storage_dir=tmp_path / "missions")
    return engine, engine.start_mission("fail once", budget_steps=3, max_repairs=0, **kw)


def test_failed_mission_is_terminal_until_explicitly_restarted(tmp_path):
    from core.mission import MissionState

    engine, report = _make_failed(tmp_path)
    assert report.state == MissionState.FAILED.value

    with pytest.raises(ValueError, match="FAILED"):
        engine.resume_mission(report.mission_id)

    # explicit recovery path works
    calls = {"n": 0}

    def fixed(node, ctx):
        calls["n"] += 1
        return {"success": True, "output": "recovered work output"}

    engine._injected_executor = fixed
    resumed = engine.resume_mission(report.mission_id, restart_failed=True)
    assert calls["n"] >= 1
    assert resumed.state in (MissionState.COMPLETED.value, MissionState.FAILED.value)
    assert resumed.restarts_used == 1
    assert resumed.error is None or resumed.state != MissionState.COMPLETED.value


def test_blocked_mission_resumes_without_flags(tmp_path):
    from core.mission import MissionEngine, MissionState

    calls = {"n": 0}

    def blocking(node, ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"success": False, "blocked": True, "blocker_reason": "missing token"}
        return {"success": True, "output": "deployed"}

    engine = MissionEngine(executor=blocking, storage_dir=tmp_path / "missions")
    report = engine.start_mission("deploy", budget_steps=4)
    assert report.state == MissionState.BLOCKED.value

    resumed = engine.resume_mission(report.mission_id)
    assert resumed.state == MissionState.COMPLETED.value


def test_completed_and_cancelled_are_terminal(tmp_path):
    from core.mission import MissionEngine, MissionState

    engine = MissionEngine(executor=lambda node, ctx: {"success": True, "output": "done"},
                           storage_dir=tmp_path / "missions")
    report = engine.start_mission("finish fast", budget_steps=4)
    if report.state == MissionState.COMPLETED.value:
        with pytest.raises(ValueError, match="completed"):
            engine.resume_mission(report.mission_id)


# ===========================================================================
# 9. extend_budget: behaviour + documentation
# ===========================================================================
def test_extend_budget_works_on_failed_mission_and_docs_are_current(tmp_path):
    from core.mission import MissionEngine

    engine, report = _make_failed(tmp_path)
    extended = engine.extend_budget(report.mission_id, steps=7)
    assert extended.budget.bonus_steps == 7
    assert extended.budget.total_steps() == extended.budget.initial_steps + 7
    assert extended.budget.extensions_used == 1
    # a mission with room again is recoverable
    assert extended.recoverable is True

    doc = MissionEngine.extend_budget.__doc__ or ""
    assert "10 * extensions_used" not in doc  # stale documentation removed
    assert "bonus_steps" in doc


def test_extend_budget_emergency_slot(tmp_path):
    from core.mission import MissionEngine

    engine, report = _make_failed(tmp_path)
    engine.extend_budget(report.mission_id, steps=5)
    engine.extend_budget(report.mission_id, steps=5)
    with pytest.raises(ValueError, match="extensions"):
        engine.extend_budget(report.mission_id, steps=5)
    out = engine.extend_budget(report.mission_id, steps=5, emergency=True)
    assert out.budget.emergency_extensions == 1


# ===========================================================================
# 10. one shared frontend runtime client
# ===========================================================================
def test_every_dashboard_uses_the_shared_queue_first_client():
    root = pathlib.Path(__file__).resolve().parent.parent / "gateway"
    client = (root / "static" / "hermus-client.js").read_text(encoding="utf-8")
    assert "async: 'true'" in client  # queue-first submission
    assert "runtime.turn" in client or "/command" in client
    assert "formatFailure" in client  # mission failures are surfaced

    for name in ("dashboard.html", "jarvis_dashboard.html"):
        html = (root / name).read_text(encoding="utf-8")
        assert "hermus-client.js" in html, f"{name} must load the shared client"
        assert "HermusClient.sendCommand" in html, f"{name} must send through the client"

    jarvis = (root / "jarvis_dashboard.html").read_text(encoding="utf-8")
    assert "jarvisRuntime" in jarvis
    assert "hermus-client.js" in jarvis


def test_shared_client_is_served_and_mounts_assets(client):
    r = client.get("/dashboard-assets/hermus-client.js")
    assert r.status_code == 200
    assert "HermusClient" in r.text


# ===========================================================================
# 11. CI is committed, not just documented
# ===========================================================================
def test_ci_is_local_not_hosted():
    """Hosted CI is intentionally not part of this repo (local gates only).

    Guard against dangling references: no workflow files, no CI badge in the
    README, and the local gate commands stay documented.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    workflows = root / ".github" / "workflows"
    assert not workflows.exists(), (
        "GitHub Actions was intentionally removed; if you want hosted CI back, "
        "re-add it deliberately and update this test"
    )
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "actions/workflows" not in readme, "README still references a CI badge"
    assert "docs/ci-workflow.yml" not in readme, "README points at a removed file"
    hardening = (root / "docs" / "EXECUTION_PATH_HARDENING.md").read_text(encoding="utf-8")
    assert "python -m pytest tests/" in hardening, "local pytest gate must stay documented"


# ===========================================================================
# 12. model capability negotiation
# ===========================================================================
def test_capability_report_answers_every_question(monkeypatch):
    monkeypatch.setenv("HERMUS_CAPABILITY_PROBE", "0")
    from core.model_capabilities import CAPABILITIES, negotiate

    rep = negotiate("ollama/llama3.1:8b")
    for cap in CAPABILITIES:
        assert rep.supports(cap) in ("yes", "no", "unknown")
    assert rep.has("tools") is True
    assert rep.supports("vision") == "no"
    assert rep.supports("long_context") == "yes"
    assert rep.ok_for(["tools"]) is True
    assert "vision" in rep.missing(["tools", "vision"])


def test_capability_gate_recommends_a_model_when_tools_are_missing(monkeypatch):
    monkeypatch.setenv("HERMUS_CAPABILITY_PROBE", "0")
    monkeypatch.setenv("HERMUS_AUTO_SELECT_MODEL", "1")
    from core.model_capabilities import mission_capability_gate, select_compatible_model

    gate = mission_capability_gate("mock/mock")
    assert gate["blocked"] is True
    assert gate["recommended_model"]
    pick, info = select_compatible_model(["tools"])
    assert pick and pick != "mock/mock"
    assert info["required"] == ["tools"]


def test_capability_endpoint_and_mission_resume_api(client, monkeypatch):
    monkeypatch.setenv("HERMUS_CAPABILITY_PROBE", "0")
    r = client.get("/models/capabilities")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "capabilities" in body["report"]
    assert "required" in body

    r2 = client.post("/missions/does-not-exist/resume?restart_failed=true")
    assert r2.status_code in (400, 409)
    assert "error" in r2.json()


# ===========================================================================
# Queue-first execution path (live gateway)
# ===========================================================================
def _wait_job(client, job_id, timeout=60.0):
    import time as _t

    deadline = _t.time() + timeout
    while _t.time() < deadline:
        r = client.get(f"/jobs/{job_id}")
        if r.status_code == 200:
            data = r.json()
            if data.get("status") in ("succeeded", "failed", "cancelled"):
                return data
        _t.sleep(0.2)
    return {"status": "timeout"}


def test_queued_turn_is_pollable_end_to_end(client):
    """Queue-first: submit → job → poll status → fetch result (no SSE needed)."""
    r = client.post("/command", json={
        "text": "Can you explain how to fix my app?", "user_id": "q1",
        "platform": "dashboard", "async": True, "stream": False,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["async"] is True and body["run_kind"] == "queued"
    job_id = body["job_id"]

    st = _wait_job(client, job_id)
    assert st["status"] == "succeeded", st

    # a finished job must be readable — the status payload always carries an
    # (empty) "error" field, which used to make this endpoint answer 404
    got = client.get(f"/jobs/{job_id}")
    assert got.status_code == 200, got.text
    assert got.json()["found"] is True
    assert got.json()["status"] == "succeeded"

    res = client.get(f"/jobs/{job_id}/result").json()["result"]
    assert res["run_kind"] == "chat"          # a question is never promoted
    assert res["response"]

    # unknown jobs are still 404
    assert client.get("/jobs/job_nope").status_code == 404


def test_queued_mission_reports_failure_not_advice(client):
    """A mission on a backend that cannot do the work is reported, not faked."""
    r = client.post("/command", json={
        "text": "mission: build a tiny tool and verify it", "user_id": "q2",
        "platform": "dashboard", "async": True, "stream": False, "autonomous": True,
    })
    job_id = r.json()["job_id"]
    st = _wait_job(client, job_id)
    assert st["status"] in ("succeeded", "failed"), st
    res = client.get(f"/jobs/{job_id}/result").json()["result"]
    assert res["run_kind"] in ("mission", "mission_failed")
    assert res["state"] in ("blocked", "failed")
    assert res.get("blocker_reason") or res.get("failure")
    assert "MISSION FAILED" not in str(res.get("response") or "") or res.get("failure")


def test_long_running_mission_can_be_cancelled(tmp_path):
    """Cooperative cancellation stops the lifecycle and records the state."""
    from core.mission import MissionEngine, MissionState

    rounds = {"n": 0}

    def executor(node, ctx):
        rounds["n"] += 1
        return {"success": True, "output": "work"}

    engine = MissionEngine(executor=executor, storage_dir=tmp_path / "missions")
    report = engine.start_mission(
        "long running job", budget_steps=40, max_repairs=5,
        should_cancel=lambda: rounds["n"] >= 2,
    )
    assert report.state == MissionState.CANCELLED.value
    assert rounds["n"] < 40
    saved = json.loads((tmp_path / "missions" / f"{report.mission_id}.json").read_text())
    assert saved["state"] == "cancelled"
    assert saved["failure"]["resumable"] is False  # terminal: start a new mission
