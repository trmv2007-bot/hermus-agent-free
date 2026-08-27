"""End-to-end tests for world state, graph planning, skills, resume and delegation."""
from __future__ import annotations


from PIL import Image

from core.computer import (
    CallableSource,
    ComputerActionController,
    ComputerAgent,
    ComputerPlanner,
    ComputerSkillStore,
    DelegationPlan,
    DryRunKeyboard,
    DryRunMouse,
    DryRunWindowBackend,
    MultiAgentDelegator,
    RecordingPolicy,
    RepairEngine,
    ScreenRecorder,
    TaskStore,
    WorkUnit,
    WorldState,
)


class _NoJsonLLM:
    def chat(self, messages):
        return type("Response", (), {"content": "model unavailable"})()


def _controller():
    return ComputerActionController(
        mouse=DryRunMouse(),
        keyboard=DryRunKeyboard(),
        window_manager=DryRunWindowBackend(),
    )


def test_world_state_structured_updates_history_and_persistence(tmp_path):
    world = WorldState(task="browse")
    world.update({
        "active_application": "Chrome",
        "active_window": "YouTube",
        "visible_targets": ["address bar", "search button"],
        "dialogs": ["Update popup"],
        "task_state": "BROWSER_READY",
        "confidence": 0.94,
        "detail": "Chrome shows YouTube with an update popup",
    })
    world.update({
        "detail": "The popup is no longer visible; the search button is visible",
        "clear_dialogs": True,
        "visible_targets": ["search button", "video player"],
        "confidence": 0.97,
    }, source="verification")

    assert world.active_application == "Chrome"
    assert world.active_window == "YouTube"
    assert world.dialogs == []
    assert world.visible_targets == ["address bar", "search button", "video player"]
    assert world.revision == 2 and len(world.observations) == 2
    assert world.satisfies("Chrome YouTube search button")["matched"] is True

    path = tmp_path / "world.json"
    world.save(str(path))
    restored = WorldState.load(str(path))
    assert restored.to_dict()["task_state"] == "BROWSER_READY"
    assert restored.visible_targets == world.visible_targets


def test_planner_builds_complex_executable_graph_without_blind_click_fallback(tmp_path):
    planner = ComputerPlanner(
        llm=_NoJsonLLM(),
        skills=ComputerSkillStore(str(tmp_path / "skills")),
        world_state=WorldState(active_application="Chrome", task_state="BROWSER_READY"),
    )
    graph = planner.plan_graph(
        "Download this file, unzip it, open the program and make sure it works."
    )
    kinds = [node.action["kind"] for node in graph.nodes]
    names = [node.name for node in graph.nodes]

    assert graph.validate()["ok"] is True
    assert graph.source == "deterministic_fallback"
    assert "DOWNLOAD_1" in names
    assert any(name.startswith("OPEN_DOWNLOADS") for name in names)
    assert any(name.startswith("EXTRACT") for name in names)
    assert any(name.startswith("LAUNCH_PROGRAM") for name in names)
    assert kinds[-1] == "wait_until"
    assert all(node.action.get("target") != graph.task for node in graph.nodes)
    assert graph.nodes[-1].on_success == "SUCCESS"


def test_intelligent_skill_tracks_reliability_repairs_duration_and_visual_states(tmp_path):
    store = ComputerSkillStore(str(tmp_path / "skills"))
    saved = store.save_skill(
        "Install X",
        [{"name": "INSTALL", "action": {"kind": "click_target", "target": "Install"},
          "expected": "Installed"}],
        duration=40.0,
        repairs=[{"failure": "Permission dialog", "action": {"kind": "click_target", "target": "Allow"},
                  "success": True}],
        visual_states=["Installer ready", "Installed"],
    )
    store.record_run(
        saved["name"], success=False, error="Network timeout", duration=20.0,
        visual_states=["Network error"],
    )
    store.record_run(saved["name"], success=True, duration=30.0)
    skill = store.get_skill(saved["name"])

    assert skill.runs == 3
    assert skill.successes == 2 and skill.failures == 1
    assert skill.success_rate == 0.6667
    assert skill.average_duration == 30.0
    assert "Network timeout" in skill.typical_failures
    assert skill.known_repair("Permission dialog")["success"] is True
    assert store.recall("install x application").name == saved["name"]


def test_repair_engine_reuses_verified_skill_repair():
    engine = RepairEngine(use_llm=False)
    engine.set_known_repairs([{
        "failure": "permission dialog blocked install",
        "state": "ALLOW_PERMISSION",
        "action": {"kind": "click_target", "target": "Allow"},
        "verification": {"expected_state": "Permission dialog is gone"},
        "success": True,
    }])
    plan = engine.create_plan(
        "A permission dialog blocked install",
        "Installer progress is visible",
        {"result": {"ok": True}, "verification": {"ok": False, "detail": "permission dialog blocked install"}},
    )
    assert plan.source == "skill_memory"
    assert plan.steps[0].action == {"kind": "click_target", "target": "Allow"}


def test_task_store_checkpoints_events_and_finds_resume_state(tmp_path):
    store = TaskStore(str(tmp_path / "tasks"))
    plan = [
        {"name": "ONE", "action": {"kind": "press_key", "key": "a"}, "expected": "one"},
        {"name": "TWO", "action": {"kind": "press_key", "key": "b"}, "expected": "two"},
    ]
    world = WorldState(task="demo")
    checkpoint = store.initialize("demo-id", "demo", plan, world_state=world)
    store.checkpoint_event(checkpoint, {
        "state": "ONE", "phase": "original_action", "outcome": "success",
        "verification": {"ok": True},
    }, world)
    store.checkpoint_event(checkpoint, {
        "state": "ONE", "phase": "transition", "outcome": "success_transition", "next_state": "TWO",
    }, world)
    store.mark_interrupted("demo-id", "crash")

    restored = store.load("demo-id")
    assert restored.status == "interrupted"
    assert restored.completed_states == ["ONE"]
    assert restored.current_state == "TWO"
    assert store.next_state(restored) == "TWO"
    assert (tmp_path / "tasks" / "demo-id" / "state.json").exists()
    assert (tmp_path / "tasks" / "demo-id" / "plan.json").exists()


def test_computer_agent_resume_skips_completed_states(tmp_path):
    class StatefulVerifier:
        def __init__(self):
            self.resume_mode = False

        def verify(self, before, after, expected):
            if expected == "first visible":
                return {"ok": True, "matched": True, "detail": expected, "confidence": 1.0}
            return {
                "ok": self.resume_mode,
                "matched": self.resume_mode,
                "detail": "second visible" if self.resume_mode else "second missing",
                "confidence": 1.0,
            }

    verifier = StatefulVerifier()
    recorder = ScreenRecorder(
        source=CallableSource(lambda: Image.new("RGB", (32, 24), "white")),
        fps=20,
        max_seconds=20,
    )
    policy = RecordingPolicy(str(tmp_path / "tasks"))
    store = TaskStore(str(tmp_path / "tasks"))
    plan = lambda task: [  # noqa: E731
        {"name": "FIRST", "action": {"kind": "press_key", "key": "a"}, "expected": "first visible"},
        {"name": "SECOND", "action": {"kind": "press_key", "key": "b"}, "expected": "second visible"},
    ]
    controller = _controller()
    agent = ComputerAgent(
        controller=controller,
        recorder=recorder,
        planner=plan,
        verifier=verifier,
        repair_engine=RepairEngine(use_llm=False),
        policy=policy,
        task_store=store,
        skills=ComputerSkillStore(str(tmp_path / "skills")),
        learn_skills=False,
        max_retries=0,
    )

    first = agent.run("two steps", task_id="resume-me")
    assert first["success"] is False
    assert store.load("resume-me").completed_states == ["FIRST"]
    assert [item["args"]["key"] for item in controller.history] == ["a", "b"]

    verifier.resume_mode = True
    resumed = agent.resume("resume-me")
    assert resumed["success"] is True and resumed["resumed"] is True
    assert [item["args"]["key"] for item in controller.history] == ["a", "b", "b"]
    checkpoint = store.load("resume-me")
    assert checkpoint.status == "success" and checkpoint.resume_count == 1
    task_dir = tmp_path / "tasks" / "resume-me"
    for name in ("state.json", "plan.json", "timeline.json", "actions.json",
                 "verification.json", "repairs.json", "result.json", "summary.md"):
        assert (task_dir / name).exists(), name


class _FakeAgentManager:
    def __init__(self):
        self.submitted = []

    def status(self, name):
        return {"success": True, "alive": True, "name": name}

    def create(self, name, role="generic"):
        return {"success": True, "name": name, "role": role}

    def start(self, name):
        return {"success": True, "name": name, "pid": 1}

    def submit_job(self, name, job):
        job_id = f"job-{len(self.submitted) + 1}"
        self.submitted.append((name, job_id, job))
        return {"success": True, "name": name, "job_id": job_id, "queued": True}

    def wait_job(self, name, job_id, timeout=120):
        return {"success": True, "status": "finished", "job_id": job_id,
                "result": {"success": True, "result": f"done by {name}"}}


def test_background_agent_jobs_persist_queryable_results(tmp_path, monkeypatch):
    import core.agent_manager as manager_module

    monkeypatch.setattr(manager_module.workspace, "base_dir", tmp_path)
    manager = manager_module.AgentManager()
    assert manager.create("worker", role="generic")["success"]
    queued = manager.submit_job("worker", {"task": "background work"})
    manager_module.worker_loop(
        "worker",
        handler=lambda job: {"ok": True, "answer": job["task"].upper()},
        heartbeat_interval=0.01,
        max_idle=0.02,
    )
    status = manager.job_status("worker", queued["job_id"])
    assert status["status"] == "finished"
    assert status["result"]["answer"] == "BACKGROUND WORK"
    assert (tmp_path / "agents" / "worker" / "results" / f"{queued['job_id']}.json").exists()


def test_multi_agent_delegation_respects_dependencies_and_routes_roles(tmp_path):
    manager = _FakeAgentManager()
    delegator = MultiAgentDelegator(manager=manager, root=str(tmp_path / "delegations"))
    plan = DelegationPlan(task="ship app", units=[
        WorkUnit("research", "researcher", "Research the dependency"),
        WorkUnit("code", "coder", "Implement the fix", depends_on=["research"]),
        WorkUnit("desktop", "computer-operator", "Install and test", depends_on=["code"]),
    ])
    result = delegator.execute(plan, wait=True)

    assert result["success"] is True
    assert [job[2]["unit_id"] for job in manager.submitted] == ["research", "code", "desktop"]
    assert [job[0] for job in manager.submitted] == [
        "hermus-researcher", "hermus-coder", "hermus-computer-operator"
    ]
    assert manager.submitted[1][2]["dependencies"]["research"]
    assert (tmp_path / "delegations" / f"{plan.plan_id}.json").exists()
